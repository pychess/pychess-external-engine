from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from pychess_external_engine.config import EngineConfig
from pychess_external_engine.protocol import ProtocolError, SearchRequest
from pychess_external_engine.search import Info, SearchController

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def payload(search_id: str = "search-1") -> dict:
    return {
        "protocolVersion": 1,
        "sessionId": "tab",
        "searchId": search_id,
        "variant": "chess",
        "engineVariant": "chess",
        "chess960": False,
        "initialFen": FEN,
        "moves": [],
        "currentFen": FEN,
        "threads": 1,
        "hash": 16,
        "multiPv": 1,
        "search": {"depth": 5},
    }


class FixtureRules:
    """Lifecycle-only double; semantic/native admission is tested separately."""

    closed = False

    async def start(self, request: SearchRequest) -> bool:
        return False

    async def normalize(self, moves: tuple[str, ...]) -> list[str]:
        valid = []
        for move in moves:
            if move not in ("e2e4", "e7e5", "d2d4", "d7d5"):
                break
            valid.append(move)
        return valid

    async def close(self) -> None:
        self.closed = True


class RequestTest(unittest.TestCase):
    def test_rejects_injection_booleans_and_arbitrary_configuration(self) -> None:
        for update in (
            {"initialFen": FEN + "\nquit"},
            {"moves": ["e2e4\nquit"]},
            {"threads": True},
            {"chess960": 1},
            {"engine": "/arbitrary/path"},
            {"search": {"depth": 129}},
            {"search": {"infinite": True}},
            {"moves": ["e2e4"] * 1001},
            {"engineVariant": "chess moves e2e4"},
        ):
            with self.assertRaises(ProtocolError):
                SearchRequest.parse({**payload(), **update})

    def test_rules_hash_and_extended_moves(self) -> None:
        ini = "[example:chess]\nstartFen = " + FEN + "\n"
        rules = {"ini": ini, "sha256": hashlib.sha256(ini.encode()).hexdigest()}
        extended = ["a10b10", "+P@a3", "b2h8+", "b2h8-", "a4a5pd5", "P@f7"]
        request = SearchRequest.parse({**payload(), "rules": rules, "moves": extended})
        self.assertEqual(request.moves, tuple(extended))
        with self.assertRaises(ProtocolError):
            SearchRequest.parse({**payload(), "rules": {**rules, "ini": ini + "\n"}})


class InfoTest(unittest.TestCase):
    def test_field_order_partial_lines_bounds_and_extended_tokens(self) -> None:
        info = Info.parse(
            "info nodes 45 pv a10b10 +P@a3 b2h8+ a4a5pd5 score mate -3 "
            "upperbound multipv 2 depth 9 time 0 wdl 1 2 3"
        )
        assert info is not None
        self.assertEqual(info.moves, ("a10b10", "+P@a3", "b2h8+", "a4a5pd5"))
        self.assertEqual(info.score, ("mate", -3))
        self.assertEqual(info.multi_pv, 2)
        self.assertEqual(info.bound, "upperbound")
        partial = Info.parse("info score cp 27 nodes nonsense depth 8")
        assert partial is not None
        self.assertEqual(partial.score, ("cp", 27))
        self.assertEqual(partial.nodes, 0)
        self.assertEqual(partial.depth, 8)


class SearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.log = Path(self.directory.name) / "engine.log"
        self.controllers: list[SearchController] = []

    async def asyncTearDown(self) -> None:
        for controller in self.controllers:
            await controller.close()
        self.directory.cleanup()

    def controller(self, mode: str = "normal", **values) -> SearchController:
        config = EngineConfig(
            command=(sys.executable, str(FAKE_ENGINE), "--mode", mode, "--log", str(self.log)),
            handshake_timeout=0.5,
            stop_timeout=0.1,
            **values,
        )
        controller = SearchController(config, FixtureRules)
        self.controllers.append(controller)
        return controller

    async def test_evaluation_completion_identity_and_score_pov(self) -> None:
        controller = self.controller()
        data = {**payload(), "currentFen": FEN.replace(" w ", " b ")}
        # The fixture backend deliberately ignores position semantics; verify that
        # the controller preserves engine-relative scores and does not flip Black.
        request = SearchRequest.parse(data)
        await controller.begin(request)
        events = [event async for event in controller.events(request)]
        self.assertEqual(events[0]["type"], "started")
        self.assertEqual(events[-1]["type"], "completed")
        self.assertTrue(all(event["searchId"] == "search-1" for event in events))
        evaluation = next(event for event in events if event["type"] == "evaluation")
        pvs = evaluation["pvs"]
        assert isinstance(pvs, list)
        self.assertEqual(pvs[0]["cp"], 10)
        self.assertEqual(events[-1]["bestmove"], "e2e4")
        self.assertIsNone(controller.active)

    async def test_busy_cleanup_and_stale_stop_output(self) -> None:
        controller = self.controller("delayed-stop")
        first = SearchRequest.parse(payload())
        await controller.begin(first)
        events = controller.events(first)
        await anext(events)
        await anext(events)
        replacement = SearchRequest.parse(payload("replacement"))
        with self.assertRaises(ProtocolError) as error:
            await controller.begin(replacement)
        self.assertEqual(error.exception.status, 409)
        await events.aclose()
        await controller.begin(replacement)
        next_events = controller.events(replacement)
        await anext(next_events)
        evaluation = await anext(next_events)
        pvs = evaluation["pvs"]
        assert isinstance(pvs, list)
        self.assertEqual(pvs[0]["cp"], 20)
        self.assertEqual(evaluation["searchId"], "replacement")
        await next_events.aclose()
        commands = self.log.read_text().splitlines()
        positions = [i for i, line in enumerate(commands) if " position fen " in line]
        stops = [i for i, line in enumerate(commands) if line.endswith(" stop")]
        self.assertLess(stops[0], positions[1])

    async def test_hung_stop_kills_before_replacement(self) -> None:
        controller = self.controller("ignore-stop")
        first = SearchRequest.parse(payload())
        await controller.begin(first)
        process = controller.engine.process
        await asyncio.wait_for(controller.finish(first), 1)
        assert process is not None
        self.assertIsNotNone(process.returncode)
        await controller.begin(SearchRequest.parse(payload("replacement")))
        assert controller.engine.process is not None
        self.assertNotEqual(controller.engine.process.pid, process.pid)

    async def test_wall_clock_limit_releases_engine(self) -> None:
        controller = self.controller("wait", max_search_seconds=0.15)
        request = SearchRequest.parse(payload())
        await controller.begin(request)
        events = [event async for event in controller.events(request)]
        self.assertEqual(events[-1]["reason"], "wallClockLimit")
        self.assertIsNone(controller.active)
        self.assertFalse(controller.engine.searching)

    async def test_crash_and_oversized_output_release_ownership(self) -> None:
        for mode in ("crash", "oversized-output", "output-flood"):
            controller = self.controller(mode)
            request = SearchRequest.parse(payload())
            await controller.begin(request)
            events = [event async for event in controller.events(request)]
            self.assertEqual(events[-1]["type"], "error")
            self.assertIsNone(controller.active)

    async def test_idle_shutdown_and_restart(self) -> None:
        controller = self.controller(idle_seconds=0.05)
        await controller.initialize()
        process = controller.engine.process
        await asyncio.sleep(0.12)
        self.assertFalse(controller.engine.alive)
        await controller.begin(SearchRequest.parse(payload()))
        assert controller.engine.process is not None and process is not None
        self.assertNotEqual(controller.engine.process.pid, process.pid)

    async def test_stale_cleanup_cannot_stop_new_owner(self) -> None:
        controller = self.controller("wait")
        first = SearchRequest.parse(payload())
        await controller.begin(first)
        await controller.finish(first)
        replacement = SearchRequest.parse(payload("replacement"))
        await controller.begin(replacement)
        await controller.finish(first)
        self.assertIs(controller.active, replacement)
        self.assertTrue(controller.engine.searching)

    async def test_new_search_waits_for_idle_process_close(self) -> None:
        controller = self.controller(idle_seconds=0.03)
        await controller.initialize()
        process = controller.engine.process
        closing = asyncio.Event()
        proceed = asyncio.Event()
        close = controller.engine.close

        async def delayed_close(*, force: bool = False) -> None:
            closing.set()
            await proceed.wait()
            await close(force=force)

        controller.engine.close = delayed_close
        await asyncio.wait_for(closing.wait(), 1)
        request = SearchRequest.parse(payload())
        begin = asyncio.create_task(controller.begin(request))
        await asyncio.sleep(0.01)
        self.assertFalse(begin.done())
        proceed.set()
        await begin
        assert process is not None and controller.engine.process is not None
        self.assertIsNotNone(process.returncode)
        self.assertNotEqual(controller.engine.process.pid, process.pid)

    async def test_cancellation_while_waiting_for_output_drains_search(self) -> None:
        controller = self.controller("wait")
        request = SearchRequest.parse(payload())
        await controller.begin(request)
        rules = controller.rules
        events = controller.events(request)
        await anext(events)
        await anext(events)
        next_event = asyncio.create_task(anext(events))
        await asyncio.sleep(0.01)
        next_event.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await next_event
        self.assertIsNone(controller.active)
        self.assertFalse(controller.engine.searching)
        assert isinstance(rules, FixtureRules)
        self.assertTrue(rules.closed)

    async def test_effective_resource_bounds_and_partial_multi_pv(self) -> None:
        controller = self.controller("multi", max_threads=2, max_hash=64, max_multi_pv=2)
        request = SearchRequest.parse({**payload(), "threads": 16, "hash": 512, "multiPv": 2})
        await controller.begin(request)
        events = [event async for event in controller.events(request)]
        evaluations = [event for event in events if event["type"] == "evaluation"]
        last = evaluations[-1]["pvs"]
        assert isinstance(last, list)
        self.assertEqual([pv["multiPv"] for pv in last], [1, 2])
        self.assertEqual(last[1]["cp"], -5)
        self.assertTrue(last[1]["upperbound"])
        log = self.log.read_text()
        self.assertIn("setoption name Threads value 2", log)
        self.assertIn("setoption name Hash value 64", log)

    async def test_terminal_position_does_not_start_search(self) -> None:
        class TerminalRules(FixtureRules):
            async def start(self, request: SearchRequest) -> bool:
                return True

        controller = self.controller()
        controller.rules_factory = TerminalRules
        request = SearchRequest.parse(payload())
        await controller.begin(request)
        events = [event async for event in controller.events(request)]
        self.assertEqual([event["type"] for event in events], ["started", "completed"])
        self.assertEqual(events[-1]["reason"], "terminal")
        self.assertNotIn(" go ", self.log.read_text())


class NativeFixtureRules(FixtureRules):
    """Rules for trusted native smoke-test positions, not a production admission API."""

    async def start(self, request: SearchRequest) -> bool:
        import pyffish

        self.request = request
        return not pyffish.legal_moves(
            request.engine_variant, request.initial_fen, list(request.moves), request.chess960
        )

    async def normalize(self, moves: tuple[str, ...]) -> list[str]:
        import pyffish

        request = self.request
        history = list(request.moves)
        valid = []
        for move in moves:
            if move not in pyffish.legal_moves(
                request.engine_variant, request.initial_fen, history, request.chess960
            ):
                break
            valid.append(move)
            history.append(move)
        return valid


@unittest.skipUnless(os.environ.get("PYCHESS_TEST_ENGINE"), "Native engine smoke test is opt-in")
class NativeSearchTest(unittest.IsolatedAsyncioTestCase):
    async def test_native_variants_and_legal_principal_variations(self) -> None:
        import pyffish

        config = EngineConfig(
            command=(os.environ["PYCHESS_TEST_ENGINE"],),
            options=(("Use NNUE", "false"),),
        )
        controller = SearchController(config, NativeFixtureRules)
        try:
            for variant in ("chess", "shogi", "xiangqi", "grand"):
                with self.subTest(variant=variant):
                    fen = pyffish.start_fen(variant)
                    request = SearchRequest.parse(
                        {
                            **payload(variant),
                            "variant": variant,
                            "engineVariant": variant,
                            "initialFen": fen,
                            "currentFen": fen,
                            "search": {"depth": 2},
                            "multiPv": 2,
                        }
                    )
                    await controller.begin(request)
                    events = [event async for event in controller.events(request)]
                    self.assertEqual(events[-1]["type"], "completed")
                    evaluations = [event for event in events if event["type"] == "evaluation"]
                    self.assertTrue(evaluations)
                    pvs = evaluations[-1]["pvs"]
                    assert isinstance(pvs, list)
                    self.assertEqual(len(pvs), 2)
                    self.assertIn(
                        events[-1]["bestmove"], pyffish.legal_moves(variant, fen, [], False)
                    )
                    self.assertIsNone(controller.active)
        finally:
            await controller.close()
