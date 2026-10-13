"""Representative native UCI/rules compatibility receipt; no browser or public writes."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
from dataclasses import dataclass
from pathlib import Path

import pyffish

from pychess_external_engine.config import EngineConfig
from pychess_external_engine.protocol import SearchRequest
from pychess_external_engine.rules import RulesWorker
from pychess_external_engine.search import SearchController


@dataclass(frozen=True)
class Case:
    name: str
    variant: str
    fen: str | None = None
    moves: tuple[str, ...] = ()
    chess960: bool = False
    terminal: bool = False


def cases() -> tuple[Case, ...]:
    return (
        Case("chess-start", "chess"),
        Case("black-score", "chess", "7k/8/8/8/8/8/8/Q6K b - - 0 1"),
        Case("chess960-castle", "chess", "7k/8/8/8/8/8/8/5KR1 w G - 0 1", ("f1g1",), True),
        Case(
            "embassy-castles",
            "embassy",
            "r3k4r/10/10/10/10/10/10/R3K4R b KQkq - 0 1",
            ("e8b8", "e1h1"),
        ),
        Case("shogi-drop", "shogi", "4k4/9/9/9/9/9/9/9/4K4[P] w 0 1", ("P@e5",)),
        Case("shogi-promote", "shogi", "4k4/9/4P4/9/9/9/9/9/4K4[-] w 0 1", ("e7e8+",)),
        Case("kyoto-demote", "kyotoshogi", moves=("a1a2-",)),
        Case("janggi-pass", "janggi", moves=("e2e2",)),
        Case("xiangqi-ten-ranks", "xiangqi", moves=("b3b10",)),
        Case("grand-ten-ranks", "grand"),
        Case(
            "pocket-promoted-capture", "crazyhouse", "7k/8/8/8/4q~3/8/4R3/7K[] w - - 0 1", ("e2e4",)
        ),
        Case(
            "seirawan-gate",
            "seirawan",
            "rh3rk1/pp4pp/2n3q1/2pp1pP1/8/2PP1N2/PP3P2/R1BQ1RK1[H] w ACD - 1 19",
            ("c1f4h",),
        ),
        Case("repetition-history", "chess", moves=("g1f3", "g8f6", "f3g1", "f6g8") * 2),
        Case("terminal-mate", "chess", "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", terminal=True),
    )


def request_for(case: Case) -> SearchRequest:
    fen = case.fen or pyffish.start_fen(case.variant)
    current = pyffish.get_fen(case.variant, fen, list(case.moves), case.chess960, False, True)
    return SearchRequest.parse(
        {
            "protocolVersion": 1,
            "sessionId": "compatibility",
            "searchId": case.name,
            "variant": case.variant,
            "engineVariant": case.variant,
            "chess960": case.chess960,
            "initialFen": fen,
            "currentFen": current,
            "moves": list(case.moves),
            "threads": 1,
            "hash": 16,
            "multiPv": 2,
            "search": {"depth": 2},
        }
    )


async def run_case(controller: SearchController, case: Case) -> dict[str, object]:
    request = request_for(case)
    await controller.begin(request)
    if controller.terminal != case.terminal:
        raise AssertionError("Unexpected terminal classification")
    if case.name == "repetition-history":
        if not pyffish.is_optional_game_end(
            case.variant, request.initial_fen, list(request.moves), case.chess960
        )[0]:
            raise AssertionError("Fixture did not reach repetition")
    events = [event async for event in controller.events(request)]
    completed = events[-1]
    if completed["type"] != "completed":
        raise AssertionError("Native search did not complete: " + str(completed))
    evaluations = [event for event in events if event["type"] == "evaluation"]
    if not case.terminal and (not evaluations or not completed.get("bestmove")):
        raise AssertionError("Search omitted evaluation/bestmove")
    for event in evaluations:
        pvs = event["pvs"]
        assert isinstance(pvs, list)
        for pv in pvs:
            history = list(request.moves)
            for move in pv["moves"]:
                if move not in pyffish.legal_moves(
                    case.variant, request.initial_fen, history, case.chess960
                ):
                    raise AssertionError("Illegal PV token")
                history.append(move)
    score = None
    if evaluations:
        pvs = evaluations[-1]["pvs"]
        assert isinstance(pvs, list)
        pv = pvs[0]
        score = {key: pv[key] for key in ("cp", "mate") if key in pv}
        if case.name == "black-score" and not any(value < 0 for value in score.values()):
            raise AssertionError("Expected a negative Black-to-move score")
    return {
        "case": case.name,
        "variant": case.variant,
        "chess960": case.chess960,
        "historyLength": len(case.moves),
        "currentFen": request.current_fen,
        "reason": completed["reason"],
        "bestmove": completed.get("bestmove"),
        "score": score,
        "legalPvs": "passed",
        "result": "passed",
    }


async def check(config: EngineConfig) -> dict[str, object]:
    controller = SearchController(config, RulesWorker)
    report: dict[str, object] = {
        "platform": platform.platform(),
        "rulesVersion": list(pyffish.version()),
        "depth": 2,
        "multiPv": 2,
        "checks": [],
    }
    try:
        await controller.initialize()
        report["engine"] = controller.engine.name
        checks = []
        for case in cases():
            checks.append(await run_case(controller, case))
        report["checks"] = checks
        report["result"] = "passed"
    finally:
        await controller.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--setoption", nargs=2, action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args()
    report = asyncio.run(
        check(EngineConfig((args.engine,), options=tuple(tuple(o) for o in args.setoption)))
    )
    rendered = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
