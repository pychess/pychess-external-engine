"""A controllable UCI fixture, launched only by the subprocess tests."""

from __future__ import annotations

import argparse
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="normal")
    args = parser.parse_args()
    loaded = False
    for raw in sys.stdin:
        line = raw.strip()
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
        elif line == "quit":
            if args.mode == "ignore-quit":
                time.sleep(10)
            return


if __name__ == "__main__":
    main()
