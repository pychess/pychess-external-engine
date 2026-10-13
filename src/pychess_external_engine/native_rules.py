"""Check exact definitions with the configured native engine before registering them."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .config import EngineConfig
from .protocol import ProtocolError, RulesDefinition
from .uci import EngineError

MAX_CHECK_OUTPUT = 256 * 1024


async def check_native_rules(config: EngineConfig, rules: RulesDefinition, directory: Path) -> None:
    # XBoard image tables do not affect UCI rules. Disable only that check in the
    # diagnostic copy; the original, hashed bytes are loaded into the search engine.
    sections = re.findall(r"^\[([^\]:]+)(?::[^\]]+)?\]", rules.ini, re.MULTILINE)
    lines = []
    for line in rules.ini.splitlines():
        if line.strip().split("=", 1)[0].strip() == "pieceToCharTable":
            continue
        lines.append(line)
        if line.startswith("["):
            lines.append("pieceToCharTable = -")
    path = directory / "check.ini"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        process = await asyncio.create_subprocess_exec(
            *config.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        raise EngineError(
            "engine_unavailable", "The configured engine could not be launched"
        ) from None
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None

    async def bounded_read(stream: asyncio.StreamReader) -> bytes:
        result = bytearray()
        while chunk := await stream.read(8192):
            result.extend(chunk)
            if len(result) > MAX_CHECK_OUTPUT:
                raise EngineError(
                    "rules_check_output", "The native rules check produced too much output"
                )
        return bytes(result)

    readers = [
        asyncio.create_task(bounded_read(stream)) for stream in (process.stdout, process.stderr)
    ]

    def kill_if_running() -> None:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    try:
        async with asyncio.timeout(config.handshake_timeout):
            process.stdin.write(f"check {path}\nquit\n".encode())
            await process.stdin.drain()
            _, diagnostics = await asyncio.gather(*readers)
            await process.wait()
        accepted = [f"Parsing variant: {name}" for name in sections]
        actual = [
            line.strip()
            for line in diagnostics.decode(errors="replace").splitlines()
            if line.strip()
        ]
        if process.returncode != 0 or not accepted or actual != accepted:
            raise ProtocolError(
                "incompatible_rules",
                "The native engine did not accept these exact rule definitions",
                422,
            )
    except TimeoutError:
        raise EngineError("rules_check_timeout", "The native rules check timed out") from None
    except ConnectionError:
        raise EngineError(
            "rules_check_failed", "The native rules checker exited unexpectedly"
        ) from None
    finally:
        kill_if_running()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)

        # Drain without accumulating so a paused output pipe cannot block reaping.
        async def discard(stream: asyncio.StreamReader) -> None:
            while await stream.read(8192):
                pass

        await asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())
