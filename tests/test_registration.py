from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from pychess_external_engine.registration import Registration, RegistrationError, website_origin

TOKEN = "account_" + "x" * 40
SECRET = "local_" + "y" * 40


class RegistrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.calls = []
        self.status = 200

        async def handler(request):
            self.calls.append(
                (request.path, request.headers.get("Authorization"), await request.json())
            )
            return web.json_response({"id": "engine01", "ok": True}, status=self.status)

        app = web.Application()
        app.router.add_post("/{path:.*}", handler)
        self.server = TestServer(app)
        await self.server.start_server()
        self.registration = Registration(
            str(self.server.make_url("/")),
            TOKEN,
            "http://127.0.0.1:9670",
            {"name": "My UCI engine"},
            SECRET,
        )

    async def asyncTearDown(self) -> None:
        await self.registration.close()
        await self.server.close()

    async def test_register_and_unregister_send_only_metadata_and_separate_credentials(
        self,
    ) -> None:
        await self.registration.start()
        self.assertEqual(self.registration.id, "engine01")
        path, authorization, data = self.calls[0]
        self.assertEqual(path, "/api/external-engine")
        self.assertEqual(authorization, "Bearer " + TOKEN)
        self.assertEqual(data["clientSecret"], SECRET)
        self.assertEqual(data["address"], "http://127.0.0.1:9670")
        self.assertNotIn(TOKEN, str(data))
        await self.registration.close()
        self.assertEqual(self.calls[-1][0], "/api/external-engine/engine01/stop")
        self.assertEqual(self.calls[-1][2], {"instanceId": data["instanceId"]})

    async def test_revoked_registration_stops_renewal_without_recreating_it(self) -> None:
        await self.registration.start()
        self.status = 403
        with patch("pychess_external_engine.registration.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaises(RegistrationError):
                await self.registration.run()
        self.assertEqual(self.calls[-1][0], "/api/external-engine/engine01/heartbeat")
        self.assertEqual(len(self.calls), 2)

    async def test_failed_start_closes_its_http_client(self) -> None:
        self.status = 403
        with self.assertRaisesRegex(RegistrationError, "invalid, revoked"):
            await self.registration.start()
        assert self.registration.client is not None
        self.assertTrue(self.registration.client.closed)

    async def test_redirects_are_rejected_instead_of_forwarding_account_credentials(self) -> None:
        self.status = 302
        with self.assertRaisesRegex(RegistrationError, "HTTP 302"):
            await self.registration.start()
        self.assertEqual(len(self.calls), 1)


class WebsiteTest(unittest.TestCase):
    def test_website_requires_https_or_development_loopback_and_no_paths(self) -> None:
        self.assertEqual(website_origin("https://www.pychess.org/"), "https://www.pychess.org")
        self.assertEqual(website_origin("http://localhost:8080"), "http://localhost:8080")
        for site in (
            "http://remote.example",
            "https://user@pychess.org",
            "https://pychess.org/path",
            "https://pychess.org?token=x",
            "https://pychess.org#x",
        ):
            with self.assertRaises(ValueError):
                website_origin(site)
