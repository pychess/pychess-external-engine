"""Local-only configuration. Browser requests cannot choose executables or paths."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

MANAGED_OPTIONS = frozenset(
    ("threads", "hash", "multipv", "uci_variant", "uci_chess960", "uci_analysemode")
)


def command_text(value: str) -> str:
    if not value or any(char in value for char in "\r\n\x00"):
        raise ValueError("UCI values must be nonempty and contain no line breaks or NUL")
    return value


@dataclass(frozen=True)
class EngineConfig:
    command: tuple[str, ...]
    options: tuple[tuple[str, str], ...] = ()
    max_threads: int = field(default_factory=lambda: os.cpu_count() or 1)
    max_hash: int = 512
    max_multi_pv: int = 20
    handshake_timeout: float = 10
    stop_timeout: float = 5
    max_search_seconds: float = 900
    idle_seconds: float = 300

    def __post_init__(self) -> None:
        if not self.command or any(not value or "\x00" in value for value in self.command):
            raise ValueError("An engine executable and valid argument array are required")
        for name, value in self.options:
            command_text(name)
            command_text(value)
            if name.casefold() in MANAGED_OPTIONS:
                raise ValueError(
                    f"{name} is controlled by search requests and local resource limits"
                )
        if (
            any(
                type(value) is not int or value < 1
                for value in (self.max_threads, self.max_hash, self.max_multi_pv)
            )
            or self.max_multi_pv > 20
        ):
            raise ValueError("Resource limits must be positive integers; MultiPV cannot exceed 20")
        for value in (
            self.handshake_timeout,
            self.stop_timeout,
            self.max_search_seconds,
            self.idle_seconds,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts must be finite and positive")
