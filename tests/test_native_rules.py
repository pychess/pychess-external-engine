from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from test_rules import custom_payload
from test_search import payload

from pychess_external_engine.config import EngineConfig
from pychess_external_engine.native_rules import check_native_rules
from pychess_external_engine.protocol import ProtocolError, SearchRequest
from pychess_external_engine.rules import RulesWorker
from pychess_external_engine.search import SearchController
from pychess_external_engine.uci import EngineError


class RulesCheckFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_flood_and_missing_acknowledgment(self) -> None:
        request = SearchRequest.parse(custom_payload("[testvariant:chess]\n"))
        assert request.rules is not None
        for code, error in (
            ("import time; time.sleep(30)", EngineError),
            ("import sys; sys.stderr.write('x' * 1000000); sys.stderr.flush()", EngineError),
            ("pass", ProtocolError),
        ):
            with tempfile.TemporaryDirectory() as directory:
                config = EngineConfig(command=(sys.executable, "-c", code), handshake_timeout=0.3)
                with self.assertRaises(error):
                    await check_native_rules(config, request.rules, Path(directory))


@unittest.skipUnless(
    os.environ.get("PYCHESS_TEST_ENGINE"), "Set PYCHESS_TEST_ENGINE for native checks"
)
class NativeRulesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        local = Path(self.directory.name) / "local.ini"
        local.write_text("[testvariant:chess]\ndoubleStep = true\n")
        self.controller = SearchController(
            EngineConfig(
                command=(os.environ["PYCHESS_TEST_ENGINE"],),
                options=(("Use NNUE", "false"), ("VariantPath", str(local))),
            ),
            RulesWorker,
        )
        await self.controller.initialize()

    async def asyncTearDown(self) -> None:
        await self.controller.close()
        self.directory.cleanup()

    async def test_exact_rules_replace_local_registry_and_hash_changes(self) -> None:
        for ini, same_process in (
            ("[testvariant:chess]\ndoubleStep = false\n", False),
            ("[testvariant:chess]\ndoubleStep = false\n", True),
            ("[testvariant:chess]\ndoubleStep = true\n", False),
        ):
            before = self.controller.engine.process
            request = SearchRequest.parse({**custom_payload(ini), "search": {"depth": 2}})
            await self.controller.begin(request)
            after = self.controller.engine.process
            self.assertEqual(before is after, same_process)
            directory = self.controller.engine.rules_directory
            assert directory is not None
            self.assertEqual((Path(directory.name) / "rules.ini").read_bytes(), ini.encode())
            self.assertIn("testvariant", self.controller.engine.variants)
            events = [event async for event in self.controller.events(request)]
            self.assertEqual(events[-1]["type"], "completed")
            self.assertTrue(any(event["type"] == "evaluation" for event in events))
            if not same_process and before is not None:
                self.assertIsNotNone(before.returncode)
        before = self.controller.engine.process
        request = SearchRequest.parse(payload())
        await self.controller.begin(request)
        self.assertIsNot(self.controller.engine.process, before)
        self.assertIsNone(self.controller.engine.rules_directory)
        await self.controller.finish(request)

    async def test_inherited_snapshot_and_unicode_comment(self) -> None:
        ini = "# Árvíztűrő\n[savedbase:chess]\ndoubleStep = false\n[studysnap_saved:savedbase]\n"
        request = SearchRequest.parse(
            {**custom_payload(ini, "studysnap_saved"), "search": {"depth": 2}}
        )
        await self.controller.begin(request)
        events = [event async for event in self.controller.events(request)]
        self.assertEqual(events[-1]["type"], "completed")

    async def test_unknown_rules_fail_before_search_and_can_recover(self) -> None:
        request = SearchRequest.parse(custom_payload("[testvariant:chess]\nunknownRule = true\n"))
        assert request.rules is not None
        with self.assertRaises(ProtocolError):
            await check_native_rules(
                self.controller.config, request.rules, Path(self.directory.name)
            )
        with self.assertRaises(ProtocolError) as failure:
            await self.controller.begin(request)
        self.assertEqual(failure.exception.code, "incompatible_rules")
        self.assertIsNone(self.controller.active)
        self.assertFalse(self.controller.engine.searching)
        self.assertIsNone(self.controller.engine.rules_directory)
        await self.controller.begin(SearchRequest.parse(payload()))
