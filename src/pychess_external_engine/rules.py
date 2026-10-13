"""Bounded rules IPC: native registrations and faults cannot accumulate in the bridge."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any

from .protocol import MAX_BODY_BYTES, ProtocolError, SearchRequest
from .uci import EngineError


@dataclass
class RulesWorker:
    timeout: float = 10
    command: tuple[str, ...] = (sys.executable, "-m", "pychess_external_engine.rules_worker")
    process: asyncio.subprocess.Process | None = None

    async def exchange(self, message: dict[str, object]) -> dict[str, Any]:
        if self.process is None:
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *self.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=MAX_BODY_BYTES + 1024,
                )
            except OSError:
                raise EngineError(
                    "rules_unavailable", "The isolated rules helper could not start"
                ) from None
        process = self.process
        assert process.stdin is not None and process.stdout is not None
        try:
            async with asyncio.timeout(self.timeout):
                process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
                await process.stdin.drain()
                line = await process.stdout.readline()
                if not line:
                    raise EngineError("rules_exited", "The isolated rules helper exited")
                result = json.loads(line)
                if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                    raise EngineError("rules_protocol", "The rules helper returned invalid data")
                if not result["ok"]:
                    raise ProtocolError(result["code"], result["message"], result["status"])
                return result
        except TimeoutError:
            await self.close()
            raise EngineError("rules_timeout", "The isolated rules helper timed out") from None
        except (ConnectionError, ValueError):
            await self.close()
            raise EngineError("rules_protocol", "The rules helper connection failed") from None
        except (EngineError, ProtocolError):
            await self.close()
            raise

    async def start(self, request: SearchRequest) -> bool:
        result = await self.exchange({"op": "start", "request": request.payload()})
        return bool(result["terminal"])

    async def normalize(self, moves: tuple[str, ...]) -> list[str]:
        result = await self.exchange({"op": "pv", "moves": list(moves)})
        return result["moves"]

    async def capabilities(self) -> dict[str, Any]:
        return await self.exchange({"op": "capabilities"})

    async def close(self) -> None:
        process = self.process
        if process is None:
            return

        async def discard_output() -> None:
            if process.stdout is not None:
                while await process.stdout.read(8192):
                    pass

        discard = asyncio.create_task(discard_output())
        try:
            if process.returncode is None:
                process.kill()
            async with asyncio.timeout(2):
                await process.wait()
        except ProcessLookupError:
            await process.wait()
        finally:
            discard.cancel()
            await asyncio.gather(discard, return_exceptions=True)
            self.process = None
