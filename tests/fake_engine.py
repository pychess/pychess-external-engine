"""A controllable UCI fixture, launched only by the subprocess tests."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="normal")
    parser.add_argument("--log")
    args = parser.parse_args()
    loaded = False
    searches = 0
    for raw in sys.stdin:
        line = raw.strip()
        if args.log:
            with Path(args.log).open("a", encoding="utf-8") as log:
                log.write(f"{os.getpid()} {line}\n")
        if line == "uci":
            if args.mode == "no-handshake":
                continue
            if args.mode == "exit":
                return
            print("id name Fake UCI engine", flush=True)
            if args.mode != "minimal":
                print("option name Threads type spin default 1 min 1 max 4", flush=True)
                print("option name Hash type spin default 16 min 1 max 1024", flush=True)
                print("option name MultiPV type spin default 1 min 1 max 50", flush=True)
                print("option name UCI_Chess960 type check default false", flush=True)
                print("option name UCI_AnalyseMode type check default false", flush=True)
                print("option name VariantPath type string default <empty>", flush=True)
                variants = " var custom-variant" if loaded else ""
                print(
                    "option name UCI_Variant type combo default chess var chess var shogi"
                    + variants,
                    flush=True,
                )
            print("uciok", flush=True)
        elif line.startswith("setoption name VariantPath value "):
            loaded = True
        elif line == "isready":
            if args.mode == "no-ready":
                continue
            print("readyok", flush=True)
        elif line.startswith("go "):
            searches += 1
            if args.mode in ("oversized-output", "output-flood"):
                size = 1000000 if args.mode == "output-flood" else 64 * 1024
                print("info " + "x" * size, flush=True)
            else:
                print(
                    f"info nodes 100 time 5 pv e2e4 e7e5 score cp {searches * 10} "
                    "depth 4 multipv 1",
                    flush=True,
                )
            if args.mode == "crash":
                return
            if args.mode == "multi":
                print(
                    "info score cp -5 upperbound depth 3 multipv 2 pv d2d4 d7d5 nodes 150 time 7",
                    flush=True,
                )
            if args.mode not in ("wait", "delayed-stop", "ignore-stop", "oversized-output"):
                print("bestmove e2e4 ponder e7e5", flush=True)
        elif line == "stop":
            if args.mode == "ignore-stop":
                continue
            if args.mode == "delayed-stop":
                print("info depth 10 multipv 1 score cp 999 nodes 500 time 100 pv d2d4", flush=True)
                time.sleep(0.03)
            print("bestmove e2e4", flush=True)
        elif line == "quit":
            if args.mode == "ignore-quit":
                time.sleep(10)
            return


if __name__ == "__main__":
    main()
