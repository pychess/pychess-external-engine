"""Phase-1 loopback transport prototype. This does not run a chess engine."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlsplit

from aiohttp import web

DEFAULT_PORT = 9670
MAX_BODY_BYTES = 64 * 1024
DEFAULT_ORIGINS = ("https://www.pychess.org", "https://pychess.org")


@dataclass
class ProbeState:
    """An ephemeral token and one stream lease shared by all browser tabs."""

    token: str
    origins: frozenset[str]
    authority: str
    active: tuple[str, str] | None = None
    started: int = 0
    released: int = 0
    last_search: str | None = None
    interval: float = 0.1
    max_seconds: float = 30
    idle: asyncio.Event = field(default_factory=asyncio.Event)


STATE = web.AppKey("probe_state", ProbeState)


def validate_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "An allowed origin must be a complete HTTP(S) origin without a path"
        )
    # Reading port validates malformed and out-of-range ports.
    _ = parsed.port
    return origin


def error(status: int, code: str) -> web.Response:
    return web.json_response(
        {"protocolVersion": 1, "error": {"code": code}}, status=status
    )


def cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


@web.middleware
async def access(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    state = request.app[STATE]
    # Comparing the exact local authority also rejects DNS-rebinding hosts.
    expected_host = state.authority
    if request.host != expected_host:
        return error(403, "invalid_host")
    origin = request.headers.get("Origin", "")
    if origin not in state.origins:
        return error(403, "origin_not_allowed")
    headers = cors_headers(origin)
    if request.method == "OPTIONS":
        requested_method = request.headers.get("Access-Control-Request-Method", "")
        requested_headers = {
            name.strip().lower()
            for name in request.headers.get("Access-Control-Request-Headers", "").split(
                ","
            )
            if name.strip()
        }
        routes = {"/v1/capabilities": "GET", "/v1/analyse": "POST"}
        if routes.get(request.path) != requested_method or not requested_headers <= {
            "authorization",
            "content-type",
        }:
            response = error(403, "preflight_not_allowed")
            response.headers.update(headers)
            return response
        headers.update(
            {
                "Access-Control-Allow-Methods": requested_method,
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "600",
            }
        )
        # Older browsers using PNA preflights still need explicit opt-in.
        if request.headers.get("Access-Control-Request-Private-Network") == "true":
            headers["Access-Control-Allow-Private-Network"] = "true"
        return web.Response(status=204, headers=headers)
    supplied = request.headers.get("Authorization", "")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + state.token).encode()):
        response = error(401, "pairing_required")
        response.headers.update(headers)
        return response
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = error(exc.status, "request_rejected")
    # Streaming handlers add these headers before prepare().
    if not response.prepared:
        response.headers.update(headers)
    return response


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
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError(
            "Pairing tokens must contain at least 32 non-whitespace ASCII characters"
        )
    app = web.Application(middlewares=[access], client_max_size=MAX_BODY_BYTES)
    app[STATE] = ProbeState(
        token,
        frozenset(validate_origin(origin) for origin in origins),
        authority,
        interval=interval,
        max_seconds=max_seconds,
    )
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
