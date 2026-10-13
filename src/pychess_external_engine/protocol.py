"""Versioned browser requests and bounded engine command inputs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

MAX_BODY_BYTES = 256 * 1024
MAX_FEN_LENGTH = 4096
MAX_RULES_BYTES = 64 * 1024
MAX_MOVES = 1000
MAX_PV_MOVES = 64
IDENTITY_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
VARIANT_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def text(value: object, *, limit: int, spaces: bool = False) -> str:
    minimum = 32 if spaces else 33
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= limit
        or any(not minimum <= ord(char) <= 126 for char in value)
    ):
        raise ProtocolError("invalid_request", "Invalid text or engine command characters")
    return value


def positive_integer(value: object, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ProtocolError(
            "invalid_request", "Search and resource values must be bounded integers"
        )
    return value


@dataclass(frozen=True)
class RulesDefinition:
    ini: str
    sha256: str

    @classmethod
    def parse(cls, value: object) -> RulesDefinition:
        if not isinstance(value, dict) or set(value) != {"ini", "sha256"}:
            raise ProtocolError("invalid_rules", "Rules require exact INI text and its hash")
        ini, digest = value["ini"], value["sha256"]
        if (
            not isinstance(ini, str)
            or not ini
            or len(ini.encode()) > MAX_RULES_BYTES
            or "\x00" in ini
            or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        ):
            raise ProtocolError("invalid_rules", "Invalid rules payload")
        if hashlib.sha256(ini.encode()).hexdigest() != digest:
            raise ProtocolError(
                "rules_mismatch", "Rules text does not match the supplied hash", 422
            )
        return cls(ini, digest)


@dataclass(frozen=True)
class SearchRequest:
    session_id: str
    search_id: str
    variant: str
    engine_variant: str
    chess960: bool
    initial_fen: str
    moves: tuple[str, ...]
    current_fen: str
    threads: int
    hash: int
    multi_pv: int
    search: tuple[tuple[str, int], ...]
    rules: RulesDefinition | None = None

    @classmethod
    def parse(cls, value: Any) -> SearchRequest:
        if not isinstance(value, dict):
            raise ProtocolError("invalid_request", "Analysis requests must be objects")
        allowed = {
            "protocolVersion",
            "sessionId",
            "searchId",
            "variant",
            "engineVariant",
            "chess960",
            "initialFen",
            "moves",
            "currentFen",
            "threads",
            "hash",
            "multiPv",
            "search",
            "rules",
        }
        if (
            set(value) - allowed
            or type(value.get("protocolVersion")) is not int
            or value["protocolVersion"] != 1
        ):
            raise ProtocolError("invalid_protocol", "Unsupported protocol or request fields")
        session = text(value.get("sessionId"), limit=64)
        identity = text(value.get("searchId"), limit=64)
        variant = text(value.get("variant"), limit=128)
        engine_variant = text(value.get("engineVariant"), limit=128)
        if not IDENTITY_RE.fullmatch(session) or not IDENTITY_RE.fullmatch(identity):
            raise ProtocolError("invalid_search_identity", "Invalid tab or search ID")
        if not VARIANT_RE.fullmatch(variant) or not VARIANT_RE.fullmatch(engine_variant):
            raise ProtocolError("unsupported_variant", "Invalid variant key", 422)
        if type(value.get("chess960")) is not bool:
            raise ProtocolError("invalid_request", "Chess960 mode must be explicit")
        initial = text(value.get("initialFen"), limit=MAX_FEN_LENGTH, spaces=True)
        current = text(value.get("currentFen"), limit=MAX_FEN_LENGTH, spaces=True)
        moves = value.get("moves")
        if not isinstance(moves, list) or len(moves) > MAX_MOVES:
            raise ProtocolError("invalid_history", "Move history exceeds the supported bound")
        move_tokens = tuple(text(move, limit=64) for move in moves)
        limits = value.get("search")
        if (
            not isinstance(limits, dict)
            or not limits
            or set(limits) - {"depth", "movetime", "nodes"}
        ):
            raise ProtocolError("invalid_search", "At least one supported search limit is required")
        maxima = {"depth": 128, "movetime": 86_400_000, "nodes": 2**63 - 1}
        search = tuple(
            (key, positive_integer(limits[key], maxima[key])) for key in maxima if key in limits
        )
        if (
            len(("position fen " + initial + " moves " + " ".join(move_tokens)).encode())
            > 64 * 1024
        ):
            raise ProtocolError("invalid_history", "Position command exceeds the engine line bound")
        return cls(
            session,
            identity,
            variant,
            engine_variant,
            value["chess960"],
            initial,
            move_tokens,
            current,
            positive_integer(value.get("threads", 1), 1024),
            positive_integer(value.get("hash", 16), 1_048_576),
            positive_integer(value.get("multiPv", 1), 20),
            search,
            RulesDefinition.parse(value["rules"]) if "rules" in value else None,
        )

    def event(self, kind: str, **fields: object) -> dict[str, object]:
        return {"protocolVersion": 1, "searchId": self.search_id, "type": kind, **fields}

    def payload(self) -> dict[str, object]:
        value: dict[str, object] = {
            "protocolVersion": 1,
            "sessionId": self.session_id,
            "searchId": self.search_id,
            "variant": self.variant,
            "engineVariant": self.engine_variant,
            "chess960": self.chess960,
            "initialFen": self.initial_fen,
            "moves": list(self.moves),
            "currentFen": self.current_fen,
            "threads": self.threads,
            "hash": self.hash,
            "multiPv": self.multi_pv,
            "search": dict(self.search),
        }
        if self.rules is not None:
            value["rules"] = {"ini": self.rules.ini, "sha256": self.rules.sha256}
        return value
