from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from pychess_external_engine.app import STATE
from pychess_external_engine.cli import main


class CliTest(unittest.TestCase):
    def test_loopback_default_port_authority_and_shutdown_deadline(self) -> None:
        for port, authority in ((80, "127.0.0.1"), (9670, "127.0.0.1:9670")):
            with (
                patch(
                    "sys.argv",
                    ["pychess-external-engine", "--engine", "stockfish", "--port", str(port)],
                ),
                patch("pychess_external_engine.cli.web.run_app") as run_app,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main()
            app = run_app.call_args.args[0]
            self.assertEqual(app[STATE].authority, authority)
            self.assertEqual(run_app.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run_app.call_args.kwargs["shutdown_timeout"], 5)
            self.assertIsNone(app[STATE].controller.alice_config)
