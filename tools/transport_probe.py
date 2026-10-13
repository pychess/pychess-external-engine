"""Phase-1 loopback transport prototype. This does not run a chess engine."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import cast

from aiohttp import web

from pychess_external_engine.access import (
    ACCESS,
    DEFAULT_ORIGINS,
    DEFAULT_PORT,
    AccessSettings,
    access,
    cors_headers,
    error,
    validate_origin,
    validate_token,
)

MAX_BODY_BYTES = 64 * 1024


@dataclass
class ProbeState(AccessSettings):
    """An ephemeral token and one stream lease shared by all browser tabs."""

    active: tuple[str, str] | None = None
    started: int = 0
    released: int = 0
    last_search: str | None = None
    interval: float = 0.1
    max_seconds: float = 30
    idle: asyncio.Event = field(default_factory=asyncio.Event)


STATE = web.AppKey("probe_state", ProbeState)


async def capabilities(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "protocolVersion": 1,
            "prototype": True,
            "scorePov": "sideToMove",
            "engine": {"name": "Transport probe (fake evaluations)", "variants": []},
            "limits": {"maxBodyBytes": MAX_BODY_BYTES, "maxSearchSeconds": 30},
        }
    )


async def wait_for_disconnect(request: web.Request) -> None:
    while request.transport is not None and not request.transport.is_closing():
        await asyncio.sleep(0.02)


async def analyse(request: web.Request) -> web.StreamResponse:
    if request.content_type != "application/json":
        return error(415, "json_required")
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return error(400, "invalid_json")
    if (
        not isinstance(body, dict)
        or type(body.get("protocolVersion")) is not int
        or body.get("protocolVersion") != 1
    ):
        return error(400, "invalid_protocol")
    ids = [body.get("sessionId"), body.get("searchId")]
    if not all(
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and value.isascii()
        and all(char.isalnum() or char in "_-" for char in value)
        for value in ids
    ):
        return error(400, "invalid_search_identity")
    session_id, search_id = (cast(str, value) for value in ids)
    state = request.app[STATE]
    if state.active is not None:
        return error(409, "engine_busy")
    # Acquire without an await so two handlers cannot both own the engine.
    state.active = (session_id, search_id)
    state.started += 1
    state.last_search = search_id
    state.idle.clear()
    response = web.StreamResponse(
        headers={
            **cors_headers(request.headers["Origin"]),
            "Content-Type": "application/x-ndjson",
        }
    )
    disconnect: asyncio.Task[None] | None = None

    async def send(event: dict[str, object]) -> None:
        event.update({"protocolVersion": 1, "searchId": search_id, "prototype": True})
        await response.write((json.dumps(event) + "\n").encode())

    try:
        await response.prepare(request)
        disconnect = asyncio.create_task(wait_for_disconnect(request))
        await send({"type": "started"})
        async with asyncio.timeout(state.max_seconds):
            depth = 0
            while True:
                done, _ = await asyncio.wait({disconnect}, timeout=state.interval)
                if done:
                    break
                depth += 1
                await send(
                    {
                        "type": "evaluation",
                        "depth": depth,
                        "nodes": depth * 100,
                        "millis": round(depth * state.interval * 1000),
                        "pvs": [{"depth": depth, "cp": 0, "moves": []}],
                    }
                )
    except TimeoutError:
        await send({"type": "completed", "reason": "limit"})
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        if disconnect is not None:
            disconnect.cancel()
            await asyncio.gather(disconnect, return_exceptions=True)
        state.active = None
        state.released += 1
        state.idle.set()
    return response


def create_app(
    *,
    token: str,
    origins: tuple[str, ...] = DEFAULT_ORIGINS,
    authority: str = f"127.0.0.1:{DEFAULT_PORT}",
    interval: float = 0.1,
    max_seconds: float = 30,
) -> web.Application:
    validate_token(token)
    app = web.Application(middlewares=[access], client_max_size=MAX_BODY_BYTES)
    app[STATE] = ProbeState(
        token,
        frozenset(validate_origin(origin) for origin in origins),
        authority,
        interval=interval,
        max_seconds=max_seconds,
    )
    app[ACCESS] = app[STATE]
    app[STATE].idle.set()
    app.router.add_get("/v1/capabilities", capabilities)
    app.router.add_post("/v1/analyse", analyse)
    app.router.add_route("OPTIONS", "/{path:.*}", preflight)
    return app


async def preflight(request: web.Request) -> web.Response:
    # Middleware handles validated preflights before this route is reached.
    return error(403, "preflight_not_allowed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--allow-origin", action="append", default=[])
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    token = secrets.token_urlsafe(32)
    app = create_app(
        token=token,
        origins=tuple(args.allow_origin) or DEFAULT_ORIGINS,
        authority=f"127.0.0.1:{args.port}",
    )
    print("Transport prototype only: fake evaluations, no engine searches.")
    print(f"Local address: http://127.0.0.1:{args.port}")
    print(f"Pairing token: {token}")
    web.run_app(
        app,
        host="127.0.0.1",
        port=args.port,
        access_log=None,
        handler_cancellation=True,
    )


if __name__ == "__main__":
    main()
