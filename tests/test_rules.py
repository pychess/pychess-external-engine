from __future__ import annotations

import asyncio
import hashlib
import sys
import unittest

import pyffish
from test_search import FEN, payload

from pychess_external_engine.protocol import ProtocolError, SearchRequest
from pychess_external_engine.rules import RulesWorker
from pychess_external_engine.uci import EngineError


def custom_payload(ini: str, name: str = "testvariant") -> dict:
    return {
        **payload(),
        "variant": name,
        "engineVariant": name,
        "rules": {"ini": ini, "sha256": hashlib.sha256(ini.encode()).hexdigest()},
    }


class RulesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.workers: list[RulesWorker] = []

    async def asyncTearDown(self) -> None:
        for worker in self.workers:
            await worker.close()

    def worker(self, **values) -> RulesWorker:
        worker = RulesWorker(**values)
        self.workers.append(worker)
        return worker

    async def test_start_history_and_legal_pv_prefix(self) -> None:
        worker = self.worker()
        moves = ["e2e4", "e7e5"]
        current = pyffish.get_fen("chess", FEN, moves, False, False, True)
        request = SearchRequest.parse({**payload(), "moves": moves, "currentFen": current})
        self.assertFalse(await worker.start(request))
        self.assertEqual(await worker.normalize(("g1f3", "b8c6", "e2e4")), ["g1f3", "b8c6"])
        self.assertEqual(await worker.normalize(("illegal",)), [])

    async def test_mismatch_and_illegal_history_are_rejected(self) -> None:
        for data in (
            {**payload(), "moves": ["e2e4"]},
            {**payload(), "moves": ["e2e5"]},
            {**payload(), "currentFen": FEN.replace(" w ", " b ")},
        ):
            with self.assertRaises(ProtocolError):
                await self.worker().start(SearchRequest.parse(data))

    async def test_geometry_pocket_and_counter_guards(self) -> None:
        invalid = (
            "999999999999/8/8/8/8/8/8/8 w - - 0 1",
            "8/8/8/8/8/8/8/8/8/8/8/8 w - - 0 1",
            "8/8/8/8/8/8/8/8[" + "P" * 200 + "] w - - 0 1",
            "invalid w - - 0 1",
        )
        for fen in invalid:
            with self.assertRaises(ProtocolError):
                await self.worker().start(
                    SearchRequest.parse({**payload(), "initialFen": fen, "currentFen": fen})
                )

    async def test_exact_custom_rules_and_fresh_processes(self) -> None:
        definition = "[testvariant:chess]\ndoubleStep = false\n"
        first = self.worker()
        await first.start(SearchRequest.parse(custom_payload(definition)))
        self.assertEqual(await first.normalize(("e2e4",)), [])
        self.assertEqual(await first.normalize(("e2e3",)), ["e2e3"])
        process = first.process
        await first.close()
        assert process is not None
        self.assertIsNotNone(process.returncode)
        second = self.worker()
        await second.start(SearchRequest.parse(custom_payload("[testvariant:chess]\n")))
        self.assertEqual(await second.normalize(("e2e4",)), ["e2e4"])
        assert second.process is not None
        self.assertNotEqual(second.process.pid, process.pid)
        self.assertNotIn("testvariant", pyffish.variants())

    async def test_inheritance_and_snapshot_alias(self) -> None:
        name = "studysnap_0123456789abcdefabcd"
        definition = f"[savedbase:chess]\ndoubleStep = false\n[{name}:savedbase]\n"
        worker = self.worker()
        await worker.start(SearchRequest.parse(custom_payload(definition, name)))
        self.assertEqual(await worker.normalize(("e2e4",)), [])
        self.assertEqual(await worker.normalize(("e2e3",)), ["e2e3"])

    async def test_rejected_custom_dimensions_paths_and_overrides(self) -> None:
        for definition in (
            "[testvariant:chess]\nmaxRank = 1000000\n",
            "[testvariant:chess]\nEvalFile = /arbitrary/file\n",
            "[testvariant:unknownbase]\n",
            "[chess:chess]\n",
        ):
            name = "chess" if definition.startswith("[chess:") else "testvariant"
            with self.assertRaises(ProtocolError):
                await self.worker().start(SearchRequest.parse(custom_payload(definition, name)))

    async def test_unicode_comments_preserve_exact_hash(self) -> None:
        worker = self.worker()
        await worker.start(
            SearchRequest.parse(custom_payload("# Árvíztűrő tükörfúrógép\n[testvariant:chess]\n"))
        )
        self.assertEqual(await worker.normalize(("e2e4",)), ["e2e4"])

    async def test_terminal_and_unsupported_categories(self) -> None:
        terminal = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
        worker = self.worker()
        self.assertTrue(
            await worker.start(
                SearchRequest.parse({**payload(), "initialFen": terminal, "currentFen": terminal})
            )
        )
        for variant in ("bughouse", "alice", "fogofwar", "jieqi"):
            with self.assertRaises(ProtocolError):
                await self.worker().start(
                    SearchRequest.parse({**payload(), "variant": variant, "engineVariant": variant})
                )

    async def test_metadata_does_not_register_in_parent(self) -> None:
        worker = self.worker()
        info = await worker.capabilities()
        self.assertIn("shogi", info["variants"])
        self.assertNotIn("bughouse", info["variants"])
        self.assertEqual(info["version"], [0, 0, 90])

    async def test_seirawan_and_snapshot_gating_after_castling(self) -> None:
        fen = "rh3rk1/pp4pp/2n3q1/2pp1pP1/8/2PP1N2/PP3P2/R1BQ1RK1[H] w ACD - 1 19"
        for name, rules in (("seirawan", None), ("testvariant", "[testvariant:seirawan]\n")):
            worker = self.worker()
            data = {
                **payload(),
                "variant": name,
                "engineVariant": name,
                "initialFen": fen,
                "currentFen": fen,
            }
            if rules is not None:
                data["rules"] = {"ini": rules, "sha256": hashlib.sha256(rules.encode()).hexdigest()}
            await worker.start(SearchRequest.parse(data))
            self.assertEqual(await worker.normalize(("c1f4h",)), ["c1f4h"])

    async def test_shogi_promotion_and_xiangqi_multidigit_coordinates(self) -> None:
        for variant, fen, move in (
            ("shogi", "4k4/9/4P4/9/9/9/9/9/4K4[-] w 0 1", "e7e8+"),
            ("xiangqi", pyffish.start_fen("xiangqi"), "b3b10"),
        ):
            worker = self.worker()
            data = {
                **payload(),
                "variant": variant,
                "engineVariant": variant,
                "initialFen": fen,
                "currentFen": fen,
            }
            await worker.start(SearchRequest.parse(data))
            self.assertEqual(await worker.normalize((move,)), [move])

    async def test_optional_chess_and_required_pocket_promotion_markers(self) -> None:
        for variant, root in (
            ("chess", "7k/P7/8/8/8/8/8/7K w - - 0 1"),
            ("crazyhouse", "7k/P7/8/8/8/8/8/7K[] w - - 0 1"),
        ):
            moves = ["a7a8q"]
            current = pyffish.get_fen(variant, root, moves, False, False, False)
            data = {
                **payload(),
                "variant": variant,
                "engineVariant": variant,
                "initialFen": root,
                "moves": moves,
                "currentFen": current,
            }
            await self.worker().start(SearchRequest.parse(data))
            if variant == "crazyhouse":
                self.assertIn("~", current)
                with self.assertRaises(ProtocolError):
                    await self.worker().start(
                        SearchRequest.parse({**data, "currentFen": current.replace("~", "")})
                    )

    async def test_chess960_castling_and_en_passant_state(self) -> None:
        fen = "7k/8/8/8/8/8/8/5KR1 w G - 0 1"
        worker = self.worker()
        await worker.start(
            SearchRequest.parse(
                {**payload(), "initialFen": fen, "currentFen": fen, "chess960": True}
            )
        )
        self.assertEqual(await worker.normalize(("f1g1",)), ["f1g1"])
        root = "7k/8/8/8/3p4/8/4P3/7K w - - 0 1"
        current = pyffish.get_fen("chess", root, ["e2e4"], False)
        self.assertIn(" e3 ", current)
        data = {**payload(), "initialFen": root, "moves": ["e2e4"], "currentFen": current}
        await self.worker().start(SearchRequest.parse(data))
        with self.assertRaises(ProtocolError):
            await self.worker().start(
                SearchRequest.parse({**data, "currentFen": current.replace(" e3 ", " - ")})
            )

    async def test_hung_and_invalid_worker_output_is_bounded(self) -> None:
        for code in (
            "import time; time.sleep(30)",
            "import sys; print('not-json', flush=True); sys.stdin.read()",
            "import sys, time; sys.stdout.write('x' * 1000000); sys.stdout.flush(); time.sleep(30)",
        ):
            worker = self.worker(timeout=0.2, command=(sys.executable, "-c", code))
            with self.assertRaises(EngineError):
                await asyncio.wait_for(worker.capabilities(), 3)
            self.assertIsNone(worker.process)
