"""UCI discovery and subprocess I/O, owned by one serialized search controller."""

from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import EngineConfig, command_text
from .protocol import ProtocolError, RulesDefinition

MAX_LINE_BYTES = 64 * 1024
MAX_HANDSHAKE_LINES = 4096
OPTION_RE = re.compile(r"^option name (.*?) type (check|spin|combo|string|button)(?: (.*))?$")


class EngineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Option:
    name: str
    kind: str
    default: str = ""
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()

    @classmethod
    def parse(cls, line: str) -> Option | None:
        match = OPTION_RE.fullmatch(line)
        if match is None:
            return None
        name, kind, tail = match.groups()
        tail = tail or ""
        minimum = maximum = None
        choices: tuple[str, ...] = ()
        default = tail.removeprefix("default ") if tail.startswith("default ") else ""
        if kind == "spin":
            parts = dict(re.findall(r"(?:^| )(default|min|max) (-?\d+)(?= |$)", tail))
            try:
                minimum, maximum = int(parts["min"]), int(parts["max"])
                default = str(int(parts["default"]))
            except (KeyError, ValueError):
                raise EngineError(
                    "invalid_engine_option", "Engine advertised an invalid spin option"
                ) from None
            if minimum > maximum:
                raise EngineError("invalid_engine_option", "Engine option bounds are reversed")
        elif kind == "combo":
            segments = re.split(r"(?:^| )var ", tail)
            default = segments[0].removeprefix("default ").strip()
            choices = tuple(value.strip() for value in segments[1:])
        return cls(name, kind, default, minimum, maximum, choices)

    def validate(self, value: str) -> str:
        command_text(value)
        if self.kind == "spin":
            try:
                integer = int(value)
            except ValueError:
                raise ValueError(f"{self.name} requires an integer") from None
            if (
                self.minimum is None
                or self.maximum is None
                or not self.minimum <= integer <= self.maximum
            ):
                raise ValueError(f"{self.name} is outside the engine's advertised bounds")
            return str(integer)
        if self.kind == "check" and value not in ("true", "false"):
            raise ValueError(f"{self.name} requires true or false")
        if self.kind == "combo" and value not in self.choices:
            raise ValueError(f"{self.name} is not an advertised choice")
        if self.kind == "button":
            raise ValueError("Button options are not accepted as configured values")
        return value


class UciEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.name = "UCI engine"
        self.options: dict[str, Option] = {}
        self.searching = False
        self.session: tuple[str, str, bool, str | None] | None = None
        self.rules_digest: str | None = None
        self.rules_directory: tempfile.TemporaryDirectory[str] | None = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    @property
    def variants(self) -> tuple[str, ...]:
        variant = self.options.get("uci_variant")
        return variant.choices if variant is not None else ("chess",)

    async def open(self, *, skip_variant_path: bool = False) -> None:
        if self.alive:
            return
        self.options = {}
        self.session = None
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.config.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_LINE_BYTES + 1,
            )
        except OSError:
            raise EngineError(
                "engine_unavailable", "The configured engine could not be launched"
            ) from None
        try:
            await self.discover()
            for name, value in self.config.options:
                if skip_variant_path and name.casefold() == "variantpath":
                    continue
                await self.set_option(name, value)
            await self.set_optional("UCI_AnalyseMode", "true")
            await self.ready()
            # VariantPath may change the combo: discover again after local configuration.
            if self.config.options:
                await self.discover()
                await self.ready()
        except BaseException:
            await self.close(force=True)
            raise

    async def prepare_rules(self, rules: RulesDefinition | None) -> None:
        """Replace the registry on hash changes; existing Fairy keys cannot be overwritten."""
        from .native_rules import check_native_rules

        digest = rules.sha256 if rules is not None else None
        if self.alive and self.rules_digest == digest:
            return
        await self.close(force=True)
        await self.open(skip_variant_path=rules is not None)
        if rules is None:
            return
        if "variantpath" not in self.options:
            raise ProtocolError(
                "unsupported_rules", "This engine cannot load custom definitions", 422
            )
        try:
            self.rules_directory = tempfile.TemporaryDirectory(prefix="pychess-engine-")
            directory = Path(self.rules_directory.name)
            await check_native_rules(self.config, rules, directory)
            path = directory / "rules.ini"
            path.write_bytes(rules.ini.encode("utf-8"))
            await self.set_option("VariantPath", str(path))
            await self.ready()
            await self.discover()
            await self.ready()
            self.rules_digest = digest
        except BaseException:
            await self.close(force=True)
            raise

    async def send(self, command: str) -> None:
        command_text(command)
        if len(command.encode()) > MAX_LINE_BYTES:
            raise EngineError("command_too_large", "The engine command exceeds the line limit")
        if not self.alive or self.process is None or self.process.stdin is None:
            raise EngineError("engine_exited", "The engine exited unexpectedly")
        try:
            self.process.stdin.write((command + "\n").encode())
            async with asyncio.timeout(self.config.handshake_timeout):
                await self.process.stdin.drain()
        except (ConnectionError, TimeoutError):
            raise EngineError(
                "engine_write_failed", "The engine stopped accepting commands"
            ) from None

    async def read_line(self) -> str:
        if self.process is None or self.process.stdout is None:
            raise EngineError("engine_exited", "The engine is not running")
        try:
            line = await self.process.stdout.readline()
        except ValueError:
            raise EngineError(
                "engine_output_too_large", "The engine output exceeds the line limit"
            ) from None
        if not line:
            raise EngineError("engine_exited", "The engine exited unexpectedly")
        if len(line) > MAX_LINE_BYTES:
            raise EngineError("engine_output_too_large", "The engine output exceeds the line limit")
        return line.decode("utf-8", errors="replace").strip()

    async def discover(self) -> None:
        await self.send("uci")
        options: dict[str, Option] = {}
        try:
            async with asyncio.timeout(self.config.handshake_timeout):
                for _ in range(MAX_HANDSHAKE_LINES):
                    line = await self.read_line()
                    if line == "uciok":
                        self.options = options
                        return
                    if line.startswith("id name "):
                        self.name = line[8:][:200]
                    elif line.startswith("option "):
                        option = Option.parse(line)
                        if option is not None:
                            options[option.name.casefold()] = option
        except TimeoutError:
            raise EngineError("engine_timeout", "The UCI handshake timed out") from None
        raise EngineError("engine_output_too_large", "The UCI handshake contains too many lines")

    async def set_option(self, name: str, value: str) -> None:
        option = self.options.get(name.casefold())
        if option is None:
            raise ValueError(f"The engine does not advertise {name}")
        value = option.validate(value)
        await self.send(f"setoption name {option.name} value {value}")

    async def set_optional(self, name: str, value: str) -> None:
        if name.casefold() in self.options:
            await self.set_option(name, value)

    async def ready(self) -> None:
        await self.send("isready")
        try:
            async with asyncio.timeout(self.config.handshake_timeout):
                for _ in range(MAX_HANDSHAKE_LINES):
                    if await self.read_line() == "readyok":
                        return
        except TimeoutError:
            raise EngineError("engine_timeout", "The engine readiness check timed out") from None
        raise EngineError(
            "engine_output_too_large", "The readiness response contains too many lines"
        )

    def resource_maximum(self, name: str, local_maximum: int) -> int:
        option = self.options.get(name.casefold())
        if option is None:
            return 1 if name in ("Threads", "MultiPV") else 0
        if option.kind != "spin" or option.minimum is None or option.maximum is None:
            raise EngineError(
                "invalid_engine_option", "Engine resource options must be bounded integers"
            )
        maximum = min(local_maximum, option.maximum)
        if maximum < option.minimum:
            raise EngineError(
                "invalid_engine_option", "Local resource limits exclude the engine minimum"
            )
        return maximum

    def capabilities(self) -> dict[str, object]:
        return {
            "name": self.name,
            "variants": list(self.variants),
            "chess960": "uci_chess960" in self.options,
            "customRules": "variantpath" in self.options,
            "maxThreads": self.resource_maximum("Threads", self.config.max_threads),
            "maxHash": self.resource_maximum("Hash", self.config.max_hash),
            "maxMultiPv": self.resource_maximum("MultiPV", self.config.max_multi_pv),
        }

    async def close(self, *, force: bool = False) -> None:
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
                if force:
                    process.kill()
                else:
                    try:
                        await self.send("quit")
                    except EngineError:
                        process.kill()
            try:
                async with asyncio.timeout(self.config.stop_timeout):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
        except ProcessLookupError:
            await process.wait()
        finally:
            discard.cancel()
            await asyncio.gather(discard, return_exceptions=True)
            self.process = None
            self.searching = False
            self.session = None
            self.rules_digest = None
            if self.rules_directory is not None:
                self.rules_directory.cleanup()
                self.rules_directory = None
