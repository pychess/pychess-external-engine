"""Paired local HTTP access to validated native searches."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from .access import (
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
from .config import EngineConfig
from .protocol import MAX_BODY_BYTES, ProtocolError, SearchRequest
from .rules import RulesWorker
from .search import SearchController
from .uci import EngineError


@dataclass
class BridgeState(AccessSettings):
    controller: SearchController
    capabilities: dict[str, Any] = field(default_factory=dict)
    started: int = 0
    released: int = 0
    idle: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def active(self) -> tuple[str, str] | None:
        request = self.controller.active
        return (request.session_id, request.search_id) if request is not None else None


STATE = web.AppKey("engine_bridge", BridgeState)


async def lifecycle(app: web.Application) -> AsyncIterator[None]:
    state = app[STATE]
    worker = RulesWorker()
    try:
        await state.controller.initialize()
        rules = await worker.capabilities()
        engine = state.controller.engine.capabilities()
        engine["variants"] = [v for v in state.controller.engine.variants if v in rules["variants"]]
        state.capabilities = {
            "protocolVersion": 1,
            "scorePov": "sideToMove",
            "engine": engine,
            "rules": {"version": rules["version"]},
            "limits": {
                "maxBodyBytes": MAX_BODY_BYTES,
                "maxSearchSeconds": state.controller.config.max_search_seconds,
            },
        }
        await worker.close()
        yield
    finally:
        await worker.close()
        await state.controller.close()


async def capabilities(request: web.Request) -> web.Response:
    return web.json_response(request.app[STATE].capabilities)


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
    try:
        search = SearchRequest.parse(body)
    except ProtocolError as exc:
        return error(exc.status, exc.code)
    state = request.app[STATE]
    # begin() claims ownership synchronously. It validates before sending position/go.
    try:
        await state.controller.begin(search)
    except ProtocolError as exc:
        return error(exc.status, exc.code)
    except (EngineError, ValueError):
        return error(503, "engine_unavailable")
    state.started += 1
    state.idle.clear()
    response = web.StreamResponse(
        headers={
            **cors_headers(request.headers["Origin"]),
            "Content-Type": "application/x-ndjson",
        }
    )
    events = state.controller.events(search)
    disconnect = asyncio.create_task(wait_for_disconnect(request))
    pending: asyncio.Task[dict[str, object]] | None = None
    try:
        await response.prepare(request)
        while True:
            pending = asyncio.create_task(anext(events))
            done, _ = await asyncio.wait((pending, disconnect), return_when=asyncio.FIRST_COMPLETED)
            if disconnect in done:
                break
            try:
                event = pending.result()
            except StopAsyncIteration:
                break
            async with asyncio.timeout(5):
                await response.write((json.dumps(event, separators=(",", ":")) + "\n").encode())
    except (ConnectionError, TimeoutError, asyncio.CancelledError):
        pass
    finally:
        disconnect.cancel()
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await asyncio.gather(disconnect, return_exceptions=True)
        await events.aclose()
        await state.controller.finish(search)
        state.released += 1
        # An older handler's final write can overlap a replacement search.
        if state.controller.active is None:
            state.idle.set()
    return response


async def preflight(request: web.Request) -> web.Response:
    return error(403, "preflight_not_allowed")


def create_app(
    config: EngineConfig,
    *,
    token: str,
    origins: tuple[str, ...] = DEFAULT_ORIGINS,
    authority: str = f"127.0.0.1:{DEFAULT_PORT}",
) -> web.Application:
    validate_token(token)
    app = web.Application(middlewares=[access], client_max_size=MAX_BODY_BYTES)
    state = BridgeState(
        token,
        frozenset(validate_origin(origin) for origin in origins),
        authority,
        SearchController(config, RulesWorker),
    )
    state.idle.set()
    app[STATE] = state
    app[ACCESS] = state
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/v1/capabilities", capabilities)
    app.router.add_post("/v1/analyse", analyse)
    app.router.add_route("OPTIONS", "/{path:.*}", preflight)
    return app
