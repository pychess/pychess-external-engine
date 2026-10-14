from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from pychess_external_engine.config import EngineConfig
from pychess_external_engine.uci import EngineError, Option, UciEngine

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")


class OptionTest(unittest.TestCase):
    def test_multiword_names_and_choices(self) -> None:
        option = Option.parse(
            "option name Multi Word Option type combo default First Choice "
            "var First Choice var Second Choice"
        )
        self.assertIsNotNone(option)
        assert option is not None
        self.assertEqual(option.name, "Multi Word Option")
        self.assertEqual(option.default, "First Choice")
        self.assertEqual(option.choices, ("First Choice", "Second Choice"))
        self.assertEqual(option.validate("Second Choice"), "Second Choice")
        with self.assertRaises(ValueError):
            option.validate("Unknown Choice")

    def test_spin_validation_and_protocol_injection(self) -> None:
        option = Option.parse("option name Hash type spin default 16 min 1 max 512")
        assert option is not None
        self.assertEqual(option.validate("32"), "32")
        for value in ("0", "513", "nan", "32\nquit"):
            with self.assertRaises(ValueError):
                option.validate(value)
        with self.assertRaises(EngineError):
            Option.parse("option name Hash type spin default 16 min 20 max 10")

    def test_local_limits_and_managed_options(self) -> None:
        command = (sys.executable,)
        for make_config in (
            lambda: EngineConfig(command=command, max_threads=0),
            lambda: EngineConfig(command=command, max_multi_pv=21),
            lambda: EngineConfig(command=command, idle_seconds=float("inf")),
            lambda: EngineConfig(command=command, options=(("Threads", "999"),)),
            lambda: EngineConfig(command=command, options=(("EvalFile", "file\nquit"),)),
        ):
            with self.assertRaises(ValueError):
                make_config()


class UciTest(unittest.IsolatedAsyncioTestCase):
    def make_engine(self, mode: str = "normal", **values) -> UciEngine:
        config = EngineConfig(
            command=(sys.executable, str(FAKE_ENGINE), "--mode", mode),
            handshake_timeout=0.5,
            stop_timeout=0.1,
            **values,
        )
        return UciEngine(config)

    async def test_discovery_bounds_and_close(self) -> None:
        engine = self.make_engine(max_threads=2, max_hash=128)
        await engine.open()
        process = engine.process
        self.assertIsNotNone(process)
        self.assertEqual(engine.variants, ("chess", "shogi"))
        capabilities = engine.capabilities()
        self.assertEqual(capabilities["maxThreads"], 2)
        self.assertEqual(capabilities["maxHash"], 128)
        self.assertEqual(capabilities["maxMultiPv"], 20)
        await engine.close()
        assert process is not None
        self.assertIsNotNone(process.returncode)
        self.assertFalse(engine.alive)

    async def test_discover_after_loading_rules(self) -> None:
        engine = self.make_engine(options=(("VariantPath", "a path with spaces.ini"),))
        try:
            await engine.open()
            self.assertIn("custom-variant", engine.variants)
        finally:
            await engine.close()

    async def test_engine_without_optional_options(self) -> None:
        engine = self.make_engine("minimal")
        try:
            await engine.open()
            self.assertEqual(engine.variants, ("chess",))
            self.assertEqual(engine.capabilities()["maxThreads"], 1)
            self.assertEqual(engine.capabilities()["maxHash"], 0)
            self.assertFalse(engine.capabilities()["chess960"])
        finally:
            await engine.close()

    async def test_sole_default_variant_is_not_reset_for_any_engine(self) -> None:
        for variant in ("alice", "shogi", "chess"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "commands"
                engine = UciEngine(
                    EngineConfig(
                        (
                            sys.executable,
                            str(FAKE_ENGINE),
                            "--variant-only",
                            variant,
                            "--log",
                            str(log),
                        )
                    )
                )
                try:
                    await engine.open()
                    await engine.select_variant(variant)
                    await engine.ready()
                    self.assertNotIn("setoption name UCI_Variant", log.read_text())
                finally:
                    await engine.close()

    async def test_multi_variant_selection_is_sent_even_for_default_variant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "commands"
            engine = UciEngine(EngineConfig((sys.executable, str(FAKE_ENGINE), "--log", str(log))))
            try:
                await engine.open()
                await engine.select_variant("shogi")
                await engine.select_variant("chess")
                await engine.ready()
                self.assertIn("setoption name UCI_Variant value shogi", log.read_text())
                self.assertIn("setoption name UCI_Variant value chess", log.read_text())
            finally:
                await engine.close()

    async def test_handshake_errors_release_process(self) -> None:
        for mode in ("no-handshake", "no-ready", "exit"):
            engine = self.make_engine(mode)
            with self.assertRaises(EngineError):
                await engine.open()
            self.assertIsNone(engine.process)

    async def test_cancellation_during_handshake_releases_process(self) -> None:
        engine = self.make_engine("no-handshake")
        task = asyncio.create_task(engine.open())
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(engine.process)

    async def test_unknown_local_option_releases_process(self) -> None:
        engine = self.make_engine(options=(("Unknown", "value"),))
        with self.assertRaises(ValueError):
            await engine.open()
        self.assertIsNone(engine.process)

    async def test_unresponsive_quit_is_bounded(self) -> None:
        engine = self.make_engine("ignore-quit")
        await engine.open()
        process = engine.process
        await asyncio.wait_for(engine.close(), 1)
        assert process is not None
        self.assertIsNotNone(process.returncode)
