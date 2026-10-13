"""Shared pairing, origin/authority admission and CORS for loopback APIs."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from aiohttp import web

DEFAULT_PORT = 9670
DEFAULT_ORIGINS = ("https://www.pychess.org", "https://pychess.org")


@dataclass
class AccessSettings:
    token: str
    origins: frozenset[str]
    authority: str


ACCESS = web.AppKey("loopback_access", AccessSettings)


def validate_token(token: str) -> None:
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("Pairing tokens must contain at least 32 non-whitespace ASCII characters")


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
        raise ValueError("An allowed origin must be a complete HTTP(S) origin without a path")
    # Reading port validates malformed and out-of-range ports.
    _ = parsed.port
    return origin


def error(status: int, code: str) -> web.Response:
    return web.json_response({"protocolVersion": 1, "error": {"code": code}}, status=status)


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
    state = request.app[ACCESS]
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
            for name in request.headers.get("Access-Control-Request-Headers", "").split(",")
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
