"""Local engine configuration and discovery command."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets

from aiohttp import web

from .access import DEFAULT_ORIGINS, DEFAULT_PORT
from .app import create_app
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
    parser.add_argument("--alice-engine", help="Optional Alice-Stockfish executable")
    parser.add_argument("--alice-engine-arg", action="append", default=[])
    parser.add_argument(
        "--alice-setoption", nargs=2, action="append", default=[], metavar=("NAME", "VALUE")
    )
    parser.add_argument(
        "--setoption", nargs=2, action="append", default=[], metavar=("NAME", "VALUE")
    )
    parser.add_argument("--max-threads", type=int)
    parser.add_argument("--max-hash", type=int, default=512)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--allow-origin", action="append", default=[])
    parser.add_argument("--max-search-seconds", type=float, default=900)
    parser.add_argument("--idle-seconds", type=float, default=300)
    parser.add_argument(
        "--check-engine", action="store_true", help="Discover capabilities and exit"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        values = {
            "command": (args.engine, *args.engine_arg),
            "options": tuple(tuple(option) for option in args.setoption),
            "max_hash": args.max_hash,
            "max_search_seconds": args.max_search_seconds,
            "idle_seconds": args.idle_seconds,
        }
        if args.max_threads is not None:
            values["max_threads"] = args.max_threads
        config = EngineConfig(**values)
        if not args.alice_engine and (args.alice_engine_arg or args.alice_setoption):
            parser.error("Alice options require --alice-engine")
        alice_config = (
            EngineConfig(
                **{
                    **values,
                    "command": (args.alice_engine, *args.alice_engine_arg),
                    "options": tuple(tuple(option) for option in args.alice_setoption),
                }
            )
            if args.alice_engine
            else None
        )
        if args.check_engine:
            asyncio.run(check_engine(config))
            if alice_config is not None:
                asyncio.run(check_engine(alice_config))
        else:
            token = secrets.token_urlsafe(32)
            app = create_app(
                config,
                token=token,
                origins=tuple(args.allow_origin) or DEFAULT_ORIGINS,
                authority="127.0.0.1" if args.port == 80 else f"127.0.0.1:{args.port}",
                alice_config=alice_config,
            )
            print(f"Local address: http://127.0.0.1:{args.port}", flush=True)
            print(f"Pairing token: {token}", flush=True)
            web.run_app(
                app,
                host="127.0.0.1",
                port=args.port,
                access_log=None,
                handler_cancellation=True,
                shutdown_timeout=config.stop_timeout,
            )
    except (ValueError, EngineError) as exc:
        parser.exit(1, f"Engine configuration failed: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130)


if __name__ == "__main__":
    main()
