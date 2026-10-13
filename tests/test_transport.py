from __future__ import annotations

import asyncio
import json
import unittest

from aiohttp.test_utils import TestClient, TestServer

from tools.transport_probe import MAX_BODY_BYTES, STATE, create_app

ORIGIN = "https://www.pychess.org"
TOKEN = "test-pairing-token-" + "x" * 32


class TransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = create_app(token=TOKEN, interval=0.01, max_seconds=1)
        self.server = TestServer(self.app, handler_cancellation=True)
        await self.server.start_server()
        self.app[STATE].authority = self.server.host + ":" + str(self.server.port)
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.headers = {"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN}

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def start(self, search: str = "search-1", session: str = "tab-1"):
        return await self.client.post(
            "/v1/analyse",
            headers=self.headers,
            json={"protocolVersion": 1, "sessionId": session, "searchId": search},
        )

    async def test_authenticated_capabilities(self) -> None:
        response = await self.client.get("/v1/capabilities", headers=self.headers)
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertTrue(data["prototype"])
        self.assertEqual(data["engine"]["variants"], [])
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_rejects_missing_or_incorrect_pairing(self) -> None:
        for token in ("", "Bearer incorrect"):
            response = await self.client.get(
                "/v1/capabilities", headers={"Origin": ORIGIN, "Authorization": token}
            )
            self.assertEqual(response.status, 401)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
            self.assertNotIn(TOKEN, await response.text())

    async def test_rejects_other_origins_and_hosts(self) -> None:
        for origin in ("", "null", "https://evil.example", ORIGIN + ".evil.example"):
            response = await self.client.get(
                "/v1/capabilities", headers={**self.headers, "Origin": origin}
            )
            self.assertEqual(response.status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        response = await self.client.get(
            "/v1/capabilities", headers={**self.headers, "Host": "rebound.example"}
        )
        self.assertEqual(response.status, 403)

    async def test_preflight_needs_no_token_and_starts_no_work(self) -> None:
        response = await self.client.options(
            "/v1/analyse",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Private-Network"], "true")
        self.assertEqual(self.app[STATE].started, 0)
        self.assertEqual(await response.text(), "")

    async def test_rejects_unrecognized_preflight(self) -> None:
        for path, method, headers in (
            ("/v1/analyse", "DELETE", "authorization"),
            ("/v1/analyse", "POST", "x-arbitrary"),
            ("/other", "POST", "authorization"),
        ):
            response = await self.client.options(
                path,
                headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": headers,
                },
            )
            self.assertEqual(response.status, 403)

    async def test_stream_abort_releases_lease(self) -> None:
        response = await self.start()
        self.assertEqual(response.status, 200)
        first = json.loads(await response.content.readline())
        second = json.loads(await response.content.readline())
        self.assertEqual(first["type"], "started")
        self.assertEqual(second["type"], "evaluation")
        self.assertEqual(second["searchId"], "search-1")
        response.close()
        await asyncio.wait_for(self.app[STATE].idle.wait(), 1)
        self.assertIsNone(self.app[STATE].active)
        self.assertEqual(self.app[STATE].released, 1)
        replacement = await self.start("search-2")
        self.assertEqual(replacement.status, 200)
        self.assertEqual(json.loads(await replacement.content.readline())["searchId"], "search-2")
        replacement.close()

    async def test_second_tab_cannot_replace_first_tab(self) -> None:
        owner = await self.start()
        for session in ("tab-1", "tab-2"):
            contender = await self.start("search-2", session)
            self.assertEqual(contender.status, 409)
            self.assertEqual((await contender.json())["error"]["code"], "engine_busy")
            self.assertEqual(self.app[STATE].active, ("tab-1", "search-1"))
        owner.close()

    async def test_validation_and_body_limit(self) -> None:
        for data in (
            [],
            {"protocolVersion": True},
            {"protocolVersion": 2},
            {"protocolVersion": 1, "sessionId": "tab", "searchId": "bad\nidentity"},
            {"protocolVersion": 1, "sessionId": "", "searchId": "search"},
        ):
            response = await self.client.post("/v1/analyse", headers=self.headers, json=data)
            self.assertEqual(response.status, 400)
        response = await self.client.post("/v1/analyse", headers=self.headers, data="not-json")
        self.assertEqual(response.status, 415)
        response = await self.client.post(
            "/v1/analyse", headers=self.headers, json={"padding": "x" * MAX_BODY_BYTES}
        )
        self.assertEqual(response.status, 413)
        self.assertEqual(self.app[STATE].started, 0)

    async def test_completion_releases_lease(self) -> None:
        self.app[STATE].max_seconds = 0.03
        response = await self.start()
        events = [json.loads(line) for line in (await response.text()).splitlines()]
        self.assertEqual(events[-1]["type"], "completed")
        self.assertIsNone(self.app[STATE].active)


class ConfigurationTest(unittest.TestCase):
    def test_rejects_weak_tokens_and_malformed_origins(self) -> None:
        for token in ("short", "x" * 32 + "\n"):
            with self.assertRaises(ValueError):
                create_app(token=token)
        for origin in (
            "null",
            "https://a.example/",
            "https://a.example:99999",
            "https://x@y",
        ):
            with self.assertRaises(ValueError):
                create_app(token=TOKEN, origins=(origin,))
