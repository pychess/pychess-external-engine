from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pyffish
from aiohttp.test_utils import TestClient, TestServer
from test_search import FAKE_ENGINE, FEN, payload
from test_transport import ORIGIN, TOKEN

from pychess_external_engine.app import STATE, create_app
from pychess_external_engine.config import EngineConfig
from pychess_external_engine.protocol import MAX_BODY_BYTES


class AppTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.log = Path(self.directory.name) / "engine.log"
        self.clients: list[TestClient] = []
        await self.open_app()

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.close()
        self.directory.cleanup()

    async def open_app(self, mode: str = "wait", **options) -> None:
        config = EngineConfig(
            command=(sys.executable, str(FAKE_ENGINE), "--mode", mode, "--log", str(self.log)),
            handshake_timeout=0.5,
            stop_timeout=0.1,
            **options,
        )
        self.app = create_app(config, token=TOKEN)
        server = TestServer(self.app, handler_cancellation=True)
        await server.start_server()
        self.app[STATE].authority = server.host + ":" + str(server.port)
        self.client = TestClient(server)
        self.clients.append(self.client)
        await self.client.start_server()
        self.headers = {"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN}

    async def start(self, data: dict | None = None):
        return await self.client.post("/v1/analyse", headers=self.headers, json=data or payload())

    async def test_capabilities_intersect_rules_and_engine_and_require_pairing(self) -> None:
        response = await self.client.get("/v1/capabilities", headers=self.headers)
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertNotIn("prototype", data)
        self.assertEqual(data["scorePov"], "sideToMove")
        self.assertEqual(data["engine"]["variants"], ["chess", "shogi"])
        self.assertEqual(data["rules"]["version"], [0, 0, 90])
        rejected = await self.client.get("/v1/capabilities", headers={"Origin": ORIGIN})
        self.assertEqual(rejected.status, 401)
        self.assertEqual(rejected.headers["Access-Control-Allow-Origin"], ORIGIN)

    async def test_disconnect_stops_silent_engine_and_replacement_has_new_identity(self) -> None:
        response = await self.start()
        first = json.loads(await response.content.readline())
        evaluation = json.loads(await response.content.readline())
        self.assertEqual(first["type"], "started")
        self.assertEqual(evaluation["type"], "evaluation")
        response.close()
        await asyncio.wait_for(self.app[STATE].idle.wait(), 2)
        self.assertFalse(self.app[STATE].controller.engine.searching)
        self.assertEqual(self.app[STATE].released, 1)
        replacement = await self.start(payload("replacement"))
        event = json.loads(await replacement.content.readline())
        self.assertEqual(event["searchId"], "replacement")
        replacement.close()
        await asyncio.wait_for(self.app[STATE].idle.wait(), 2)
        lines = self.log.read_text().splitlines()
        positions = [i for i, line in enumerate(lines) if " position fen " in line]
        stop = next(i for i, line in enumerate(lines) if line.endswith(" stop"))
        self.assertLess(stop, positions[1])

    async def test_competing_tab_is_rejected_without_replacing_owner(self) -> None:
        response = await self.start()
        await response.content.readline()
        other = await self.start({**payload("other"), "sessionId": "other-tab"})
        self.assertEqual(other.status, 409)
        self.assertEqual((await other.json())["error"]["code"], "engine_busy")
        active = self.app[STATE].controller.active
        assert active is not None
        self.assertEqual(active.search_id, "search-1")
        response.close()

    async def test_invalid_position_and_history_fail_before_go(self) -> None:
        for data in (
            {**payload(), "moves": ["e2e5"]},
            {**payload(), "currentFen": FEN.replace(" w ", " b ")},
            {**payload(), "initialFen": "99999/8/8/8/8/8/8/8 w - - 0 1"},
        ):
            response = await self.start(data)
            self.assertEqual(response.status, 422)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
            self.assertNotIn("application/x-ndjson", response.headers["Content-Type"])
            self.assertIsNone(self.app[STATE].controller.active)
        self.assertNotIn(" go ", self.log.read_text())

    async def test_json_protocol_and_body_limits(self) -> None:
        for data in (
            {**payload(), "protocolVersion": True},
            {**payload(), "threads": True},
            {**payload(), "rules": {"ini": "\ud800", "sha256": "0" * 64}},
        ):
            self.assertEqual((await self.start(data)).status, 400)
        response = await self.client.post(
            "/v1/analyse", headers=self.headers, json={"padding": "x" * MAX_BODY_BYTES}
        )
        self.assertEqual(response.status, 413)
        self.assertEqual(self.app[STATE].started, 0)

    async def test_terminal_releases_without_engine_search(self) -> None:
        fen = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
        response = await self.start({**payload(), "initialFen": fen, "currentFen": fen})
        events = [json.loads(line) for line in (await response.text()).splitlines()]
        self.assertEqual(events[-1]["reason"], "terminal")
        await asyncio.wait_for(self.app[STATE].idle.wait(), 2)
        self.assertNotIn(" go ", self.log.read_text())

    async def test_engine_crash_emits_error_and_releases(self) -> None:
        await self.open_app("crash")
        response = await self.start()
        events = [json.loads(line) for line in (await response.text()).splitlines()]
        self.assertEqual(events[-1]["type"], "error")
        await asyncio.wait_for(self.app[STATE].idle.wait(), 2)
        self.assertIsNone(self.app[STATE].controller.active)

    async def test_disconnect_hung_engine_is_killed_before_replacement(self) -> None:
        await self.open_app("ignore-stop")
        response = await self.start()
        await response.content.readline()
        process = self.app[STATE].controller.engine.process
        response.close()
        await asyncio.wait_for(self.app[STATE].idle.wait(), 2)
        assert process is not None
        self.assertIsNotNone(process.returncode)
        replacement = await self.start(payload("new-process"))
        self.assertEqual(replacement.status, 200)
        self.assertIsNot(self.app[STATE].controller.engine.process, process)
        replacement.close()

    async def test_app_shutdown_closes_engine_and_rules_worker(self) -> None:
        response = await self.start()
        await response.content.readline()
        controller = self.app[STATE].controller
        engine = controller.engine.process
        rules = controller.rules
        await self.client.close()
        assert engine is not None and rules is not None
        self.assertIsNotNone(engine.returncode)
        self.assertIsNone(controller.active)
        self.assertIsNone(controller.engine.process)


@unittest.skipUnless(
    os.environ.get("PYCHESS_TEST_ENGINE"), "Set PYCHESS_TEST_ENGINE for native checks"
)
class NativeAppTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_native_http_evaluation_and_cancel(self) -> None:
        app = create_app(
            EngineConfig(
                command=(os.environ["PYCHESS_TEST_ENGINE"],), options=(("Use NNUE", "false"),)
            ),
            token=TOKEN,
        )
        server = TestServer(app, handler_cancellation=True)
        client = TestClient(server)
        try:
            await client.start_server()
            app[STATE].authority = server.host + ":" + str(server.port)
            for variant in ("chess", "shogi", "xiangqi", "grand"):
                fen = pyffish.start_fen(variant)
                data = {
                    **payload(variant),
                    "variant": variant,
                    "engineVariant": variant,
                    "initialFen": fen,
                    "currentFen": fen,
                    "search": {"depth": 100},
                }
                response = await client.post(
                    "/v1/analyse",
                    json=data,
                    headers={"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN},
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(await response.content.readline())["type"], "started")
                event = json.loads(await response.content.readline())
                self.assertEqual(event["type"], "evaluation")
                self.assertTrue(event["pvs"][0]["moves"])
                self.assertTrue(set(event["pvs"][0]) & {"cp", "mate"})
                response.close()
                await asyncio.wait_for(app[STATE].idle.wait(), 2)
        finally:
            await client.close()
