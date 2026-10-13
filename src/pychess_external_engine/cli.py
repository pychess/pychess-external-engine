"""Local engine configuration and discovery command."""

from __future__ import annotations

import argparse
import asyncio
import json

from .config import EngineConfig
from .uci import EngineError, UciEngine


async def check_engine(config: EngineConfig) -> None:
    engine = UciEngine(config)
    try:
        await engine.open()
        print(json.dumps(engine.capabilities(), indent=2))
    finally:
        await engine.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, help="Path to the native UCI executable")
    parser.add_argument("--engine-arg", action="append", default=[])
    parser.add_argument(
        "--setoption", nargs=2, action="append", default=[], metavar=("NAME", "VALUE")
    )
    parser.add_argument("--max-threads", type=int)
    parser.add_argument("--max-hash", type=int, default=512)
    parser.add_argument(
        "--check-engine", action="store_true", help="Discover capabilities and exit"
    )
    args = parser.parse_args()
    if not args.check_engine:
        parser.error("This development step supports --check-engine; the HTTP search API is next")
    try:
        values = {
            "command": (args.engine, *args.engine_arg),
            "options": tuple(tuple(option) for option in args.setoption),
            "max_hash": args.max_hash,
        }
        if args.max_threads is not None:
            values["max_threads"] = args.max_threads
        config = EngineConfig(**values)
        asyncio.run(check_engine(config))
    except (ValueError, EngineError) as exc:
        parser.exit(1, f"Engine configuration failed: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130)


if __name__ == "__main__":
    main()
