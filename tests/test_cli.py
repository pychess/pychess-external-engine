from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest.mock import AsyncMock, patch

from pychess_external_engine.app import STATE
from pychess_external_engine.cli import main
from pychess_external_engine.registration import RegistrationError


class CliTest(unittest.TestCase):
    def test_loopback_default_port_authority_and_shutdown_deadline(self) -> None:
        for port, authority in ((80, "127.0.0.1"), (9670, "127.0.0.1:9670")):
            with (
                patch(
                    "sys.argv",
                    ["pychess-external-engine", "--engine", "stockfish", "--port", str(port)],
                ),
                patch.dict(os.environ, {"PYCHESS_API_TOKEN": "test_" + "x" * 40}),
                patch("pychess_external_engine.cli.serve", new_callable=AsyncMock) as serve,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                main()
            app = serve.call_args.args[0]
            self.assertEqual(app[STATE].authority, authority)
            self.assertEqual(serve.call_args.args[1], port)
            self.assertEqual(serve.call_args.args[2], "https://www.pychess.org")
            self.assertEqual(app[STATE].controller.config.stop_timeout, 5)
            self.assertIsNone(app[STATE].controller.alice_config)
            self.assertNotIn(app[STATE].token, output.getvalue())

    def test_registration_error_is_reported_as_runtime_failure(self) -> None:
        with (
            patch("sys.argv", ["pychess-external-engine", "--engine", "stockfish"]),
            patch.dict(os.environ, {"PYCHESS_API_TOKEN": "test_" + "x" * 40}),
            patch(
                "pychess_external_engine.cli.serve",
                new_callable=AsyncMock,
                side_effect=RegistrationError("Pychess engine registration failed (HTTP 404)"),
            ),
            contextlib.redirect_stderr(io.StringIO()) as output,
            self.assertRaises(SystemExit) as caught,
        ):
            main()
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("External engine failed:", output.getvalue())
        self.assertIn("HTTP 404", output.getvalue())
