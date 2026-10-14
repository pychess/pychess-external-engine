from __future__ import annotations

import asyncio
import contextlib
import io
import socket
import sys
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from test_search import FAKE_ENGINE, payload
from test_transport import ORIGIN, TOKEN

from pychess_external_engine.app import STATE, create_app
from pychess_external_engine.cli import serve
from pychess_external_engine.config import EngineConfig
from pychess_external_engine.registration import RegistrationError
from pychess_external_engine.rules import RulesWorker


class RegisteredBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_registration_failure_reaps_an_active_engine_and_rules_worker(self) -> None:
        registered = asyncio.Event()
        revoked = asyncio.Event()

        async def register(request):
            registered.set()
            return web.json_response({"id": "engine01"})

        async def stop(request):
            return web.json_response({"ok": True})

        async def renew(_self):
            await revoked.wait()
            raise RegistrationError("Account token was revoked")

        website = web.Application()
        website.router.add_post("/api/external-engine", register)
        website.router.add_post("/api/external-engine/engine01/stop", stop)
        server = TestServer(website)
        await server.start_server()
        config = EngineConfig(
            command=(sys.executable, str(FAKE_ENGINE), "--mode", "wait"),
            handshake_timeout=1,
            stop_timeout=0.1,
        )
        app = create_app(config, token=TOKEN)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        app[STATE].authority = f"127.0.0.1:{port}"
        with (
            patch("pychess_external_engine.registration.Registration.run", renew),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            task = asyncio.create_task(
                serve(app, port, str(server.make_url("/")), "account_" + "x" * 40)
            )
            try:
                await asyncio.wait_for(registered.wait(), 5)
                async with ClientSession() as client:
                    response = await client.post(
                        f"http://127.0.0.1:{port}/v1/analyse",
                        headers={"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN},
                        json=payload(),
                    )
                    await response.content.readline()
                    controller = app[STATE].controller
                    engine = controller.engine.process
                    rules = controller.rules
                    assert engine is not None and isinstance(rules, RulesWorker)
                    revoked.set()
                    with self.assertRaises(RegistrationError):
                        await asyncio.wait_for(task, 5)
                    self.assertIsNotNone(engine.returncode)
                    self.assertIsNone(controller.active)
                    self.assertIsNone(controller.engine.process)
                    self.assertIsNone(rules.process)
                    response.close()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await server.close()
