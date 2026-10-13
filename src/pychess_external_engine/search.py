"""Serialized UCI search ownership, output delivery, and bounded cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol

from .config import EngineConfig
from .protocol import MAX_PV_MOVES, ProtocolError, SearchRequest
from .uci import EngineError, UciEngine

INFO_INTEGERS = frozenset(
    (
        "depth",
        "seldepth",
        "time",
        "nodes",
        "multipv",
        "nps",
        "hashfull",
        "tbhits",
        "sbhits",
        "cpuload",
        "currmovenumber",
    )
)
INFO_KEYS = INFO_INTEGERS | {
    "score",
    "pv",
    "lowerbound",
    "upperbound",
    "string",
    "wdl",
    "currmove",
    "refutation",
    "currline",
}


class SearchRules(Protocol):
    async def start(self, request: SearchRequest) -> bool:
        """Validate the exact request before search; return whether it is terminal."""
        ...

    async def normalize(self, moves: tuple[str, ...]) -> list[str]:
        """Return a legal PV prefix from this request's root and history."""
        ...

    async def close(self) -> None: ...


@dataclass
class Info:
    depth: int | None = None
    multi_pv: int = 1
    nodes: int = 0
    millis: int = 0
    score: tuple[str, int] | None = None
    bound: str | None = None
    moves: tuple[str, ...] = ()

    @classmethod
    def parse(cls, line: str) -> Info | None:
        parts = line.split()
        if not parts or parts[0] != "info":
            return None
        if line.startswith("info string ERROR:"):
            raise EngineError(
                "engine_error", "The engine reported an analysis error; check local configuration"
            )
        result = cls()
        values: dict[str, int] = {}
        index = 1
        while index < len(parts):
            key = parts[index]
            index += 1
            if key == "string":
                break
            if key in INFO_INTEGERS and index < len(parts):
                try:
                    value = int(parts[index])
                    if 0 <= value <= 2**63 - 1:
                        values[key] = value
                except ValueError:
                    pass
                index += 1
            elif key == "score" and index + 1 < len(parts):
                kind = parts[index]
                try:
                    score = int(parts[index + 1])
                    if kind in ("cp", "mate") and abs(score) <= 2**31 - 1:
                        result.score = (kind, score)
                except ValueError:
                    pass
                index += 2
            elif key in ("upperbound", "lowerbound"):
                result.bound = key
            elif key == "pv":
                moves: list[str] = []
                while index < len(parts) and parts[index] not in INFO_KEYS:
                    if len(moves) < MAX_PV_MOVES:
                        moves.append(parts[index])
                    index += 1
                result.moves = tuple(moves)
        result.depth = values.get("depth")
        result.multi_pv = values.get("multipv", 1)
        result.nodes = values.get("nodes", 0)
        result.millis = values.get("time", 0)
        return result


@dataclass
class SearchController:
    config: EngineConfig
    rules_factory: Callable[[], SearchRules]
    engine: UciEngine = field(init=False)
    active: SearchRequest | None = None
    rules: SearchRules | None = None
    terminal: bool = False
    last_used: float = field(default_factory=monotonic)
    _cleanup: asyncio.Task[None] | None = None
    _read: asyncio.Task[str] | None = None
    _idle: asyncio.Task[None] | None = None
    _maintenance: asyncio.Task[None] | None = None
    _deadline: float = 0
    _multi_pv: int = 1

    def __post_init__(self) -> None:
        self.engine = UciEngine(self.config)

    async def initialize(self) -> None:
        await self.engine.open()
        self._idle = asyncio.create_task(self._watch_idle())

    async def _watch_idle(self) -> None:
        while True:
            await asyncio.sleep(min(self.config.idle_seconds, 1))
            if self.active is None and monotonic() - self.last_used >= self.config.idle_seconds:
                self.last_used = monotonic()
                if self.engine.alive:
                    self._maintenance = asyncio.create_task(self.engine.close(force=True))
                    await asyncio.shield(self._maintenance)
                    self._maintenance = None

    async def begin(self, request: SearchRequest) -> None:
        if self.active is not None:
            raise ProtocolError("engine_busy", "Another search owns the engine", 409)
        self.active = request
        self._cleanup = None
        self._deadline = monotonic() + self.config.max_search_seconds
        try:
            self.rules = self.rules_factory()
            if self._maintenance is not None:
                await asyncio.shield(self._maintenance)
            self.terminal = await self.rules.start(request)
            await self.engine.prepare_rules(request.rules)
            if request.engine_variant not in self.engine.variants:
                raise ProtocolError(
                    "unsupported_variant", "The configured engine cannot search these rules", 422
                )
            if request.chess960 and "uci_chess960" not in self.engine.options:
                raise ProtocolError(
                    "unsupported_chess960", "The engine does not advertise Chess960 mode", 422
                )
            await self.engine.set_optional("UCI_Variant", request.engine_variant)
            await self.engine.set_optional("UCI_Chess960", str(request.chess960).lower())
            for name, value, local_max in (
                ("Threads", request.threads, self.config.max_threads),
                ("Hash", request.hash, self.config.max_hash),
                ("MultiPV", request.multi_pv, self.config.max_multi_pv),
            ):
                option = self.engine.options.get(name.casefold())
                if option is not None:
                    maximum = self.engine.resource_maximum(name, local_max)
                    effective = min(max(value, option.minimum or 1), maximum)
                    await self.engine.set_option(name, str(effective))
                    if name == "MultiPV":
                        self._multi_pv = effective
                elif name == "MultiPV":
                    self._multi_pv = 1
            session = (
                request.session_id,
                request.engine_variant,
                request.chess960,
                request.rules.sha256 if request.rules else None,
            )
            if session != self.engine.session:
                await self.engine.send("ucinewgame")
                self.engine.session = session
            await self.engine.ready()
            self._deadline = monotonic() + self.config.max_search_seconds
            if not self.terminal:
                position = "position fen " + request.initial_fen
                if request.moves:
                    position += " moves " + " ".join(request.moves)
                await self.engine.send(position)
                self.engine.searching = True
                await self.engine.send(
                    "go " + " ".join(f"{name} {value}" for name, value in request.search)
                )
        except BaseException:
            await self.finish(request)
            raise

    async def events(self, request: SearchRequest) -> AsyncGenerator[dict[str, object], None]:
        if self.active is not request:
            raise ProtocolError("invalid_search", "Search ownership is no longer valid", 409)
        latest: dict[int, Info] = {}
        last_emit = 0.0
        try:
            yield request.event("started")
            if self.terminal:
                yield request.event("completed", reason="terminal")
                return
            while True:
                if self.active is not request:
                    return
                self._read = asyncio.create_task(self.engine.read_line())
                try:
                    async with asyncio.timeout(max(0, self._deadline - monotonic())):
                        line = await self._read
                finally:
                    self._read = None
                if line.startswith("bestmove"):
                    self.engine.searching = False
                    if latest:
                        event = await self._evaluation(request, latest)
                        if event is not None:
                            yield event
                    parts = line.split()
                    if len(parts) < 2:
                        raise EngineError("invalid_bestmove", "Engine omitted its best move")
                    best = parts[1]
                    if best in ("0000", "(none)"):
                        yield request.event("completed", reason="terminal")
                        return
                    assert self.rules is not None
                    if not await self.rules.normalize((best,)):
                        raise EngineError(
                            "invalid_bestmove", "Engine returned an illegal best move"
                        )
                    fields: dict[str, object] = {"reason": "limit", "bestmove": best}
                    if len(parts) >= 4 and parts[2] == "ponder":
                        if len(await self.rules.normalize((best, parts[3]))) == 2:
                            fields["ponder"] = parts[3]
                    yield request.event("completed", **fields)
                    return
                info = Info.parse(line)
                if (
                    info is None
                    or info.depth is None
                    or info.score is None
                    or not 1 <= info.multi_pv <= self._multi_pv
                ):
                    continue
                latest[info.multi_pv] = info
                now = monotonic()
                if now - last_emit >= 0.1:
                    event = await self._evaluation(request, latest)
                    if event is not None:
                        yield event
                    last_emit = now
        except TimeoutError:
            await self.finish(request)
            yield request.event("completed", reason="wallClockLimit")
        except (EngineError, ProtocolError) as exc:
            yield request.event("error", error={"code": exc.code, "message": str(exc)[:300]})
        finally:
            await self.finish(request)

    async def _evaluation(
        self, request: SearchRequest, latest: dict[int, Info]
    ) -> dict[str, object] | None:
        assert self.rules is not None
        pvs: list[dict[str, object]] = []
        for index, info in sorted(latest.items()):
            if info.score is None or not info.moves:
                continue
            moves = await self.rules.normalize(info.moves)
            if not moves:
                continue
            pv: dict[str, object] = {
                "multiPv": index,
                "depth": info.depth,
                "moves": moves,
                info.score[0]: info.score[1],
            }
            if info.bound is not None:
                pv[info.bound] = True
            pvs.append(pv)
        if not pvs:
            return None
        info = latest.get(1) or next(iter(latest.values()))
        return request.event(
            "evaluation", depth=info.depth, nodes=info.nodes, millis=max(1, info.millis), pvs=pvs
        )

    async def finish(self, request: SearchRequest) -> None:
        if self.active is not request:
            return
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._release(request))
        await asyncio.shield(self._cleanup)

    async def _release(self, request: SearchRequest) -> None:
        try:
            if self._read is not None:
                self._read.cancel()
                await asyncio.gather(self._read, return_exceptions=True)
                self._read = None
            if self.engine.searching:
                try:
                    await self.engine.send("stop")
                    async with asyncio.timeout(self.config.stop_timeout):
                        while not (await self.engine.read_line()).startswith("bestmove"):
                            pass
                    self.engine.searching = False
                    await self.engine.ready()
                except (EngineError, TimeoutError):
                    await self.engine.close(force=True)
            elif not self.engine.alive:
                await self.engine.close(force=True)
        finally:
            try:
                if self.rules is not None:
                    await self.rules.close()
            finally:
                self.rules = None
                self.active = None
                self.last_used = monotonic()

    async def close(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle
            self._idle = None
        if self.active is not None:
            await self.finish(self.active)
        if self._maintenance is not None:
            await asyncio.shield(self._maintenance)
            self._maintenance = None
        await self.engine.close(force=True)
