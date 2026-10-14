from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from test_search import FAKE_ENGINE, FEN, payload
from test_transport import ORIGIN, TOKEN

from pychess_external_engine.app import STATE, create_app
from pychess_external_engine.config import EngineConfig
from pychess_external_engine.protocol import ProtocolError, SearchRequest
from pychess_external_engine.rules import RulesWorker
from pychess_external_engine.uci import EngineError


def alice_payload(**changes) -> dict:
    return {**payload(), "variant": "alice", "engineVariant": "alice", **changes}


class AliceRulesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sf = importlib.import_module("pyffish_alice")
        self.workers: list[RulesWorker] = []

    async def asyncTearDown(self) -> None:
        for worker in self.workers:
            await worker.close()

    def worker(self) -> RulesWorker:
        worker = RulesWorker(alice=True)
        self.workers.append(worker)
        return worker

    async def test_transfer_history_and_marker_mismatch(self) -> None:
        history = ["e2e4", "e7e5"]
        current = self.sf.get_fen("alice", FEN, history)
        self.assertIn("|P", current)
        self.assertIn("|p", current)
        request = alice_payload(moves=history, currentFen=current)
        worker = self.worker()
        self.assertFalse(await worker.start(SearchRequest.parse(request)))
        self.assertEqual(await worker.normalize(("g1f3", "b8c6", "e2e4")), ["g1f3", "b8c6"])
        with self.assertRaises(ProtocolError) as error:
            await self.worker().start(
                SearchRequest.parse({**request, "currentFen": current.replace("|", "")})
            )
        self.assertEqual(error.exception.code, "position_mismatch")

    async def test_castling_promotion_and_transfer_back(self) -> None:
        for fen, history, move in (
            ("4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", [], "e1g1"),
            ("7k/P7/8/8/8/8/8/7K w - - 0 1", [], "a7a8q"),
            (FEN, ["e2e4", "a7a6"], "e4e5"),
        ):
            worker = self.worker()
            await worker.start(
                SearchRequest.parse(
                    alice_payload(
                        initialFen=fen,
                        currentFen=self.sf.get_fen("alice", fen, history),
                        moves=history,
                    )
                )
            )
            self.assertEqual(await worker.normalize((move,)), [move])
            result = self.sf.get_fen("alice", fen, [*history, move])
            if move == "e4e5":
                self.assertNotIn("|P", result)
            else:
                self.assertIn("|", result)

    async def test_fixed_rules_capability_and_invalid_compact_fens(self) -> None:
        self.assertEqual((await self.worker().capabilities())["variants"], ["alice"])
        for changes in (
            {"chess960": True},
            {"variant": "chess"},
            {"engineVariant": "chess"},
            {"moves": ["e2e5"]},
        ):
            with self.assertRaises(ProtocolError):
                await self.worker().start(SearchRequest.parse(alice_payload(**changes)))
        for fen in (
            FEN.replace("8/8/8/8", "|8/8/8/8"),
            FEN.replace("8/8/8/8", "||P7/8/8/8"),
            FEN.replace("8/8/8/8", "9/8/8/8"),
            FEN.replace(" - ", " e3 "),
            FEN.replace(" 0 1", " 0 0"),
            FEN.replace("8/8/8/8", "QQQQQQQQ/QQQQQQQQ/8/8"),
        ):
            with self.assertRaises(ProtocolError):
                await self.worker().start(
                    SearchRequest.parse(alice_payload(initialFen=fen, currentFen=fen))
                )


class AliceLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_unadvertised_alice_is_rejected_without_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "commands"
            app = create_app(
                EngineConfig((sys.executable, str(FAKE_ENGINE), "--log", str(log))), token=TOKEN
            )
            client = TestClient(TestServer(app))
            try:
                await client.start_server()
                app[STATE].authority = client.server.host + ":" + str(client.server.port)
                self.assertNotIn("alice", app[STATE].capabilities["engine"]["variants"])
                rejected = await client.post(
                    "/v1/analyse",
                    headers={"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN},
                    json=alice_payload(),
                )
                self.assertEqual(rejected.status, 422)
                self.assertNotIn(" position ", log.read_text())
                self.assertNotIn(" go ", log.read_text())
            finally:
                await client.close()

    async def test_one_multi_variant_executable_uses_the_matching_rules_workers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "commands"
            app = create_app(
                EngineConfig((sys.executable, str(FAKE_ENGINE), "--with-alice", "--log", str(log))),
                token=TOKEN,
            )
            client = TestClient(TestServer(app))
            try:
                await client.start_server()
                app[STATE].authority = client.server.host + ":" + str(client.server.port)
                caps = app[STATE].capabilities["engine"]
                self.assertIn("chess", caps["variants"])
                self.assertIn("alice", caps["variants"])
                self.assertNotIn("adapters", caps)
                self.assertEqual(
                    caps["variantRestrictions"],
                    {"alice": {"chess960": False, "customRules": False}},
                )
                process = app[STATE].controller.engine.process
                for data in (payload(), alice_payload(), payload("chess-again")):
                    response = await client.post(
                        "/v1/analyse",
                        headers={"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN},
                        json=data,
                    )
                    self.assertEqual(response.status, 200)
                    events = [json.loads(line) for line in (await response.text()).splitlines()]
                    self.assertEqual(events[-1]["type"], "completed")
                    self.assertEqual(events[-1]["bestmove"], "e2e4")
                    await asyncio.wait_for(app[STATE].idle.wait(), 2)
                    self.assertIs(app[STATE].controller.engine.process, process)
                self.assertEqual(len({line.split()[0] for line in log.read_text().splitlines()}), 1)
            finally:
                await client.close()

    async def test_bad_engine_or_incomplete_qualification_reaps_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "commands"
            for mode in ("crash", "wait", "illegal-bestmove"):
                command = (sys.executable, str(FAKE_ENGINE), "--log", str(log))
                app = create_app(
                    EngineConfig(
                        (*command, "--mode", mode, "--alice"),
                        handshake_timeout=0.7,
                        stop_timeout=0.1,
                    ),
                    token=TOKEN,
                )
                client = TestClient(TestServer(app))
                try:
                    with self.assertRaises(EngineError):
                        await client.start_server()
                    self.assertIsNone(app[STATE].controller.active)
                finally:
                    await client.close()
            for pid in {int(line.split()[0]) for line in log.read_text().splitlines()}:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    async def test_alice_resource_limits_and_fixed_rules_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "commands"
            command = (sys.executable, str(FAKE_ENGINE))
            app = create_app(
                EngineConfig(
                    (*command, "--alice", "--log", str(log)),
                    max_threads=1,
                    max_hash=8,
                    max_multi_pv=1,
                ),
                token=TOKEN,
            )
            client = TestClient(TestServer(app))
            try:
                await client.start_server()
                app[STATE].authority = client.server.host + ":" + str(client.server.port)
                headers = {"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN}
                caps = (await (await client.get("/v1/capabilities", headers=headers)).json())[
                    "engine"
                ]
                self.assertEqual(caps["variants"], ["alice"])
                self.assertEqual(caps["name"], "Fake UCI engine")
                self.assertNotIn("adapters", caps)
                self.assertEqual(
                    caps["variantRestrictions"],
                    {"alice": {"chess960": False, "customRules": False}},
                )
                process = app[STATE].controller.engine.process
                response = await client.post(
                    "/v1/analyse",
                    headers=headers,
                    json=alice_payload(threads=9, hash=1024, multiPv=20),
                )
                self.assertEqual(response.status, 200)
                await response.read()
                text = log.read_text()
                self.assertIn("setoption name Hash value 8", text)
                self.assertIn("setoption name Threads value 1", text)
                self.assertIn("setoption name MultiPV value 1", text)
                self.assertNotIn("setoption name UCI_Variant", text)
                self.assertIs(app[STATE].controller.engine.process, process)
                before = text.count(" go ")
                ini = "[alice:chess]\n"
                for data in (
                    alice_payload(chess960=True),
                    payload(),
                    alice_payload(
                        rules={"ini": ini, "sha256": hashlib.sha256(ini.encode()).hexdigest()}
                    ),
                ):
                    rejected = await client.post("/v1/analyse", headers=headers, json=data)
                    self.assertEqual(rejected.status, 422)
                self.assertEqual(log.read_text().count(" go "), before)
            finally:
                await client.close()


@unittest.skipUnless(
    os.environ.get("PYCHESS_TEST_ALICE_ENGINE"),
    "Set PYCHESS_TEST_ALICE_ENGINE for native Alice search checks",
)
class NativeAliceTest(unittest.IsolatedAsyncioTestCase):
    async def test_standalone_http_history_pvs_and_competing_ownership(self) -> None:
        sf = importlib.import_module("pyffish_alice")

        alice_options = (
            (("EvalFile", os.environ["PYCHESS_TEST_ALICE_NETWORK"]),)
            if (os.environ.get("PYCHESS_TEST_ALICE_NETWORK"))
            else (("Use NNUE", "false"),)
        )
        app = create_app(
            EngineConfig((os.environ["PYCHESS_TEST_ALICE_ENGINE"],), options=alice_options),
            token=TOKEN,
        )
        client = TestClient(TestServer(app, handler_cancellation=True))
        headers = {"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN}
        try:
            await client.start_server()
            app[STATE].authority = client.server.host + ":" + str(client.server.port)
            caps = (await (await client.get("/v1/capabilities", headers=headers)).json())["engine"]
            self.assertIn("alice", caps["variants"])
            self.assertEqual(caps["variants"], ["alice"])
            self.assertNotIn("adapters", caps)
            self.assertEqual(
                caps["variantRestrictions"], {"alice": {"chess960": False, "customRules": False}}
            )
            process = app[STATE].controller.engine.process
            for index, history in enumerate(([], ["e2e4"], ["e2e4", "e7e5"])):
                current = sf.get_fen("alice", FEN, history) if history else FEN
                data = {
                    **payload(str(index)),
                    "variant": "alice",
                    "engineVariant": "alice",
                    "moves": history,
                    "currentFen": current,
                    "multiPv": 2,
                    "search": {"depth": 3},
                }
                response = await client.post("/v1/analyse", headers=headers, json=data)
                self.assertEqual(response.status, 200)
                events = [json.loads(line) for line in (await response.text()).splitlines()]
                self.assertEqual(events[-1]["type"], "completed")
                self.assertIn("bestmove", events[-1])
                evaluation = next(event for event in events if event["type"] == "evaluation")
                self.assertTrue(evaluation["pvs"])
                for pv in evaluation["pvs"]:
                    moves = list(history)
                    for move in pv["moves"]:
                        self.assertIn(move, sf.legal_moves("alice", FEN, moves))
                        moves.append(move)
                await asyncio.wait_for(app[STATE].idle.wait(), 2)
                assert process is not None
                self.assertIsNone(process.returncode)
                self.assertIs(app[STATE].controller.engine.process, process)
            unsupported = await client.post("/v1/analyse", headers=headers, json=payload("chess"))
            self.assertEqual(unsupported.status, 422)
            self.assertIs(app[STATE].controller.engine.process, process)
            response = await client.post(
                "/v1/analyse", headers=headers, json=alice_payload(search={"depth": 100})
            )
            await response.content.readline()
            other = await client.post("/v1/analyse", headers=headers, json=payload("other"))
            self.assertEqual(other.status, 409)
            response.close()
            await asyncio.wait_for(app[STATE].idle.wait(), 2)
            self.assertFalse(app[STATE].controller.engine.searching)
        finally:
            await client.close()
