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
        self.response_data: object = {"id": "engine01", "ok": True}
        self.response_text = None

        async def handler(request):
            self.calls.append(
                (request.path, request.headers.get("Authorization"), await request.json())
            )
            if self.response_text is not None:
                return web.Response(text=self.response_text, status=self.status)
            return web.json_response(self.response_data, status=self.status)

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

    async def test_start_errors_identify_site_status_and_action_without_secrets(self) -> None:
        for status, guidance in (
            (401, "/account/engines"),
            (403, "same website"),
            (404, "--site http://localhost:8080"),
            (409, "conflicting registration"),
            (302, "final origin"),
            (400, "server logs"),
            (500, "server logs"),
        ):
            with self.subTest(status=status):
                self.status = status
                self.response_text = TOKEN + SECRET
                with self.assertRaises(RegistrationError) as caught:
                    await self.registration.start()
                message = str(caught.exception)
                self.assertIn(self.registration.site, message)
                self.assertIn("registration", message)
                self.assertIn(f"HTTP {status}", message)
                self.assertIn(guidance, message)
                self.assertNotIn(TOKEN, message)
                self.assertNotIn(SECRET, message)
                self.assertNotIn("removed or replaced", message)
                assert self.registration.client is not None
                self.assertTrue(self.registration.client.closed)

    async def test_removed_heartbeat_is_distinct_from_missing_api_and_never_reregisters(
        self,
    ) -> None:
        await self.registration.start()
        for status, guidance in (
            (409, "removed or replaced"),
            (404, "API endpoint was not found"),
        ):
            with self.subTest(status=status):
                self.status = status
                with patch(
                    "pychess_external_engine.registration.asyncio.sleep", new_callable=AsyncMock
                ):
                    with self.assertRaises(RegistrationError) as caught:
                        await self.registration.run()
                message = str(caught.exception)
                self.assertIn("heartbeat", message)
                self.assertIn(self.registration.site, message)
                self.assertIn(f"HTTP {status}", message)
                self.assertIn(guidance, message)
        self.assertEqual([call[0] for call in self.calls].count("/api/external-engine"), 1)

    async def test_invalid_responses_include_context_without_echoing_response(self) -> None:
        for data, text in (
            ({"id": TOKEN}, None),
            ([TOKEN, SECRET], None),
            (None, TOKEN + SECRET),
        ):
            with self.subTest(data=data):
                self.response_data = data
                self.response_text = text
                with self.assertRaises(RegistrationError) as caught:
                    await self.registration.start()
                message = str(caught.exception)
                self.assertIn("invalid", message)
                self.assertIn("HTTP 200", message)
                self.assertIn(self.registration.site, message)
                self.assertNotIn(TOKEN, message)
                self.assertNotIn(SECRET, message)

    async def test_connection_failure_names_the_target_site(self) -> None:
        await self.server.close()
        with self.assertRaisesRegex(RegistrationError, "cannot connect") as caught:
            await self.registration.start()
        self.assertIn(self.registration.site, str(caught.exception))
        assert self.registration.client is not None
        self.assertTrue(self.registration.client.closed)

    async def test_timeout_has_distinct_guidance_and_does_not_echo_exception(self) -> None:
        await self.registration.start()
        assert self.registration.client is not None
        with patch.object(self.registration.client, "post", side_effect=TimeoutError(TOKEN)):
            with self.assertRaisesRegex(RegistrationError, "timed out") as caught:
                await self.registration.request("/api/external-engine/engine01/heartbeat", {})
        message = str(caught.exception)
        self.assertIn("heartbeat", message)
        self.assertIn(self.registration.site, message)
        self.assertNotIn(TOKEN, message)


class WebsiteTest(unittest.TestCase):
    def test_website_requires_https_or_development_loopback_and_no_paths(self) -> None:
        self.assertEqual(website_origin("https://www.pychess.org/"), "https://www.pychess.org")
        self.assertEqual(website_origin("http://localhost:8080"), "http://localhost:8080")
        for site in (
            "127.0.0.1:8080",
            "http://remote.example",
            "https://user@pychess.org",
            "https://pychess.org/path",
            "https://pychess.org?token=x",
            "https://pychess.org#x",
        ):
            with self.assertRaises(ValueError):
                website_origin(site)

    def test_missing_scheme_explains_complete_local_origin(self) -> None:
        with self.assertRaisesRegex(ValueError, "including the scheme") as caught:
            website_origin("127.0.0.1:8080")
        self.assertIn("http://127.0.0.1:8080", str(caught.exception))
