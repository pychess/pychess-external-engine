"""Register a local bridge with its owner's Pychess account; never relay searches."""

from __future__ import annotations

import asyncio
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

from aiohttp import (
    ClientConnectorCertificateError,
    ClientConnectorError,
    ClientConnectorSSLError,
    ClientError,
    ClientSession,
    ClientTimeout,
)


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
            "Pychess --site must be a complete origin including the scheme, for example "
            "https://www.pychess.org or http://127.0.0.1:8080 for a local server. "
            "HTTP is allowed only for localhost/127.0.0.1; paths, credentials, queries "
            "and fragments are not allowed."
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
        operation = (
            "registration"
            if path == "/api/external-engine"
            else "heartbeat"
            if path.endswith("/heartbeat")
            else "shutdown"
        )
        context = f"Pychess engine {operation} at {self.site}"
        try:
            async with self.client.post(
                self.site + path,
                json=body,
                headers={"Authorization": "Bearer " + self.api_token},
                allow_redirects=False,
            ) as response:
                failure = f"{context} failed (HTTP {response.status}). "
                if response.status in (401, 403):
                    raise RegistrationError(
                        failure + "Engine account token is invalid, revoked, or missing engine "
                        f"permissions. Create an engine token at {self.site}/account/engines "
                        "and update --token-file or PYCHESS_API_TOKEN. Tokens must come from "
                        "the same website configured with --site."
                    )
                if response.status == 404:
                    raise RegistrationError(
                        failure + "The external-engine API endpoint was not found. Check that "
                        "this website runs the matching Pychess server integration. "
                        "For a local server, set --site to its origin, for example "
                        "--site http://localhost:8080; the default is https://www.pychess.org."
                    )
                if response.status == 409:
                    detail = (
                        "Engine registration was removed or replaced. Stop other apps using "
                        "this account token, or give each installation its own token, then "
                        "start the app again."
                        if operation != "registration"
                        else "The website rejected a conflicting registration. Use a separate "
                        "engine token for each installation and start the app again."
                    )
                    raise RegistrationError(failure + detail)
                if response.status != 200:
                    detail = (
                        "Redirects are not followed to protect your account token. Set --site "
                        "to the website's final origin."
                        if 300 <= response.status < 400
                        else "Check the website's availability and matching server integration; "
                        "consult its server logs if the error persists."
                    )
                    raise RegistrationError(failure + detail)
                try:
                    data = await response.json()
                except (ClientError, ValueError, UnicodeError) as exc:
                    raise RegistrationError(
                        f"{context} returned an invalid JSON response (HTTP 200). "
                        "Check --site and the matching Pychess server integration."
                    ) from exc
                if not isinstance(data, dict):
                    raise RegistrationError(
                        f"{context} returned an invalid response (HTTP 200; expected an object). "
                        "Check the matching Pychess server integration."
                    )
                return data
        except (ClientConnectorCertificateError, ClientConnectorSSLError) as exc:
            raise RegistrationError(
                f"{context} failed: TLS certificate or connection verification failed. "
                "Check the website certificate and your computer's clock/trust settings."
            ) from exc
        except ClientConnectorError as exc:
            raise RegistrationError(
                f"{context} failed: cannot connect to the website. Check --site, its host/port, "
                "and that the server is running and reachable from this computer."
            ) from exc
        except TimeoutError as exc:
            raise RegistrationError(
                f"{context} timed out. Check the website connection and server availability."
            ) from exc
        except ClientError as exc:
            raise RegistrationError(
                f"{context} failed: the HTTP connection could not be completed. "
                "Check the website connection and server availability."
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
                raise RegistrationError(
                    f"Pychess engine registration at {self.site} returned an invalid "
                    "registration ID (HTTP 200). Check the matching Pychess server integration."
                )
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
