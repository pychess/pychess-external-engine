from __future__ import annotations

import asyncio
import importlib
import importlib.util
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

HAS_ALICE = importlib.util.find_spec("pyffish_alice") is not None


def alice_payload(**changes) -> dict:
    return {**payload(), "variant": "alice", "engineVariant": "alice", **changes}


@unittest.skipUnless(HAS_ALICE, "Install the alice extra for Alice rules checks")
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


@unittest.skipUnless(HAS_ALICE, "Install the alice extra")
class AliceLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_bad_engine_or_incomplete_qualification_reaps_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "commands"
            for mode, extra in (("normal", ()), ("crash", ("--alice",)), ("wait", ("--alice",))):
                command = (sys.executable, str(FAKE_ENGINE), "--log", str(log))
                app = create_app(
                    EngineConfig(command),
                    token=TOKEN,
                    alice_config=EngineConfig(
                        (*command, "--mode", mode, *extra), handshake_timeout=0.7, stop_timeout=0.1
                    ),
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
                EngineConfig(command),
                token=TOKEN,
                alice_config=EngineConfig(
                    (*command, "--alice", "--log", str(log)),
                    max_threads=1,
                    max_hash=8,
                    max_multi_pv=1,
                ),
            )
            client = TestClient(TestServer(app))
            try:
                await client.start_server()
                app[STATE].authority = client.server.host + ":" + str(client.server.port)
                headers = {"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN}
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
                before = text.count(" go ")
                rejected = await client.post(
                    "/v1/analyse", headers=headers, json=alice_payload(chess960=True)
                )
                self.assertEqual(rejected.status, 422)
                self.assertEqual(log.read_text().count(" go "), before)
            finally:
                await client.close()


@unittest.skipUnless(
    HAS_ALICE
    and os.environ.get("PYCHESS_TEST_ENGINE")
    and os.environ.get("PYCHESS_TEST_ALICE_ENGINE"),
    "Set both native engines and install the alice extra",
)
class NativeAliceTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_switching_pvs_and_competing_ownership(self) -> None:
        sf = importlib.import_module("pyffish_alice")

        alice_options = (
            (("EvalFile", os.environ["PYCHESS_TEST_ALICE_NETWORK"]),)
            if (os.environ.get("PYCHESS_TEST_ALICE_NETWORK"))
            else (("Use NNUE", "false"),)
        )
        app = create_app(
            EngineConfig((os.environ["PYCHESS_TEST_ENGINE"],), options=(("Use NNUE", "false"),)),
            alice_config=EngineConfig(
                (os.environ["PYCHESS_TEST_ALICE_ENGINE"],), options=alice_options
            ),
            token=TOKEN,
        )
        client = TestClient(TestServer(app, handler_cancellation=True))
        headers = {"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN}
        try:
            await client.start_server()
            app[STATE].authority = client.server.host + ":" + str(client.server.port)
            caps = (await (await client.get("/v1/capabilities", headers=headers)).json())["engine"]
            self.assertIn("alice", caps["variants"])
            self.assertFalse(caps["adapters"]["alice"]["chess960"])
            process = app[STATE].controller.engine.process
            for index, variant in enumerate(("alice", "chess", "alice")):
                history = ["e2e4"] if variant == "alice" else []
                current = sf.get_fen("alice", FEN, history) if history else FEN
                data = {
                    **payload(str(index)),
                    "variant": variant,
                    "engineVariant": variant,
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
                if variant == "alice":
                    for pv in evaluation["pvs"]:
                        moves = list(history)
                        for move in pv["moves"]:
                            self.assertIn(move, sf.legal_moves("alice", FEN, moves))
                            moves.append(move)
                await asyncio.wait_for(app[STATE].idle.wait(), 2)
                assert process is not None
                self.assertIsNotNone(process.returncode)
                process = app[STATE].controller.engine.process
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
