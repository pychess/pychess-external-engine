"""Register a local bridge with its owner's Pychess account; never relay searches."""

from __future__ import annotations

import asyncio
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout


class RegistrationError(Exception):
    pass


def website_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or (
            parsed.scheme != "https"
            and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))
        )
    ):
        raise ValueError(
            "Pychess site must be an HTTPS origin (HTTP loopback is allowed for development)"
        )
    _ = parsed.port
    return value.rstrip("/")


class Registration:
    def __init__(
        self, site: str, api_token: str, address: str, engine: dict[str, Any], secret: str
    ) -> None:
        self.site = website_origin(site)
        self.api_token = api_token
        self.address = address
        self.engine = engine
        self.secret = secret
        self.instance_id = secrets.token_urlsafe(32)
        self.id: str | None = None
        self.client: ClientSession | None = None

    async def request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert self.client is not None
        try:
            async with self.client.post(
                self.site + path,
                json=body,
                headers={"Authorization": "Bearer " + self.api_token},
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise RegistrationError(
                        "Engine account token is invalid, revoked, or missing engine permissions"
                    )
                if response.status in (404, 409):
                    raise RegistrationError(
                        "Engine registration was removed or replaced; start the app again"
                    )
                if response.status != 200:
                    raise RegistrationError(
                        f"Pychess engine registration failed (HTTP {response.status})"
                    )
                data = await response.json()
                if not isinstance(data, dict):
                    raise RegistrationError("Invalid Pychess registration response")
                return data
        except (ClientError, TimeoutError, ValueError) as exc:
            raise RegistrationError(
                "Cannot contact Pychess to register the engine; check the site and connection"
            ) from exc

    async def start(self) -> None:
        self.client = ClientSession(timeout=ClientTimeout(total=10), trust_env=False)
        try:
            data = await self.request(
                "/api/external-engine",
                {
                    "protocolVersion": 1,
                    "instanceId": self.instance_id,
                    "address": self.address,
                    "clientSecret": self.secret,
                    "engine": self.engine,
                },
            )
            if not isinstance(data.get("id"), str) or not re.fullmatch(
                r"\w{8}", data["id"], flags=re.ASCII
            ):
                raise RegistrationError("Invalid Pychess engine registration ID")
            self.id = data["id"]
        except BaseException:
            await self.client.close()
            raise

    async def run(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.request(
                f"/api/external-engine/{self.id}/heartbeat", {"instanceId": self.instance_id}
            )

    async def close(self) -> None:
        if self.client is None or self.client.closed:
            return
        try:
            if self.id is not None:
                async with asyncio.timeout(3):
                    await self.request(
                        f"/api/external-engine/{self.id}/stop", {"instanceId": self.instance_id}
                    )
        except (RegistrationError, TimeoutError):
            pass
        finally:
            await self.client.close()
