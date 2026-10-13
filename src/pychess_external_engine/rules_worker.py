"""Isolated pyffish admission/PV worker; JSON on stdin/stdout, one search per process."""

from __future__ import annotations

import importlib
import json
import re
import sys
from collections import Counter

from .protocol import MAX_BODY_BYTES, MAX_PV_MOVES, VARIANT_RE, ProtocolError, SearchRequest
from .rule_keys import RULE_KEYS

pyffish = importlib.import_module("pyffish_alice" if "--alice" in sys.argv else "pyffish")
ALICE = "--alice" in sys.argv

HIDDEN_VARIANTS = frozenset(("fogofwar", "jieqi"))


def width(rank: str) -> int:
    """Adapted from Pychess server/utils.py fen_board_width (AGPL-3.0)."""
    return sum(
        int(token) if token.isdigit() else int(token not in "+~|")
        for token in re.findall(r"\d+|[^\d]", rank)
    )


def admit_fen(fen: str, start_fen: str) -> str:
    """Pychess Study/FEN geometry and material guards before entering native code."""
    fields = fen.split()
    if len(fields) < 4 or fields[1] not in ("w", "b"):
        raise ProtocolError("invalid_position", "FEN must include an explicit side to move", 422)
    placement = fields[0]
    baseline = start_fen.split()[0]
    start_board = baseline.split("[", 1)[0].split("/")
    board = placement.split("[", 1)[0].split("/")
    columns = width(start_board[0])
    if not len(start_board) <= len(board) <= len(start_board) + 1 or any(
        width(rank) > columns for rank in board[: len(start_board)]
    ):
        raise ProtocolError("invalid_position", "FEN exceeds the variant's board geometry", 422)
    material = [char for char in placement if char.isascii() and char.isalpha()]
    if len(material) > 128:
        raise ProtocolError("invalid_position", "FEN exceeds native material limits", 422)
    if "[" in baseline or "[" in placement or len(board) > len(start_board):
        pocket = (
            placement.split("[", 1)[1].split("]", 1)[0]
            if "[" in placement
            else (board[-1] if len(board) > len(start_board) else "")
        )
        slots = 2 * columns
        if any(
            count > slots
            for count in Counter(
                char for char in pocket if char.isascii() and char.isalpha()
            ).values()
        ):
            raise ProtocolError("invalid_position", "Pocket exceeds native material limits", 422)

        # Promoted pawns marked ~ revert to pawns when captured.
        def counts(value: str) -> Counter[str]:
            return Counter(
                "p" if i + 1 < len(value) and value[i + 1] == "~" else char.lower()
                for i, char in enumerate(value)
                if char.isascii() and char.isalpha()
            )

        initial_counts = counts(baseline)
        if any(
            count > max(slots, initial_counts[role]) for role, count in counts(placement).items()
        ):
            raise ProtocolError(
                "invalid_position", "Position exceeds native piece-array limits", 422
            )
    return " ".join(fields)


def rule_sections(ini: str) -> tuple[str, ...]:
    """Bound syntax/geometry before registering native definitions, even in the worker."""
    names: list[str] = []
    for raw in ini.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            match = re.fullmatch(r"\[([^\]:]+)(?::([^\]]+))?\]", line)
            if match is None or not raw.startswith("["):
                raise ProtocolError("invalid_rules", "Invalid variant section syntax", 422)
            name, base = match.groups()
            if VARIANT_RE.fullmatch(name) is None or (base and VARIANT_RE.fullmatch(base) is None):
                raise ProtocolError("invalid_rules", "Invalid variant or inheritance key", 422)
            if name in names or len(names) >= 64:
                raise ProtocolError(
                    "invalid_rules", "Duplicate or excessive variant definitions", 422
                )
            if name in HIDDEN_VARIANTS or base in HIDDEN_VARIANTS:
                raise ProtocolError(
                    "hidden_information",
                    "Hidden-information analysis needs a dedicated adapter",
                    422,
                )
            names.append(name)
        elif "=" not in line or not names:
            raise ProtocolError("invalid_rules", "Invalid rule assignment", 422)
        else:
            key, value = (part.strip() for part in line.split("=", 1))
            if not key or not value:
                raise ProtocolError("invalid_rules", "Empty rule assignment", 422)
            if key not in RULE_KEYS:
                raise ProtocolError(
                    "incompatible_rules",
                    "The pinned rules library does not recognize this rule",
                    422,
                )
            if key in ("maxRank", "maxFile"):
                size = (
                    ord(value) - ord("a") + 1
                    if key == "maxFile" and len(value) == 1 and value.isalpha()
                    else (int(value) if value.isascii() and value.isdigit() else 0)
                )
                if not 1 <= size <= 10:
                    raise ProtocolError(
                        "invalid_rules", "Board dimensions exceed this rules backend", 422
                    )
            if key == "startFen":
                fields = value.split()
                if len(fields) < 4 or len(value) > 4096 or not value.isascii():
                    raise ProtocolError("invalid_rules", "Invalid starting position", 422)
                ranks = fields[0].split("[", 1)[0].split("/")
                if not 1 <= len(ranks) <= 11 or any(width(rank) > 10 for rank in ranks[:10]):
                    raise ProtocolError(
                        "invalid_rules", "Starting position exceeds native geometry", 422
                    )
                baseline = "/".join(["10"] * min(len(ranks), 10)) + "[] w - - 0 1"
                admit_fen(value, baseline)
            if key.casefold() in ("evalfile", "variantpath", "syzygypath") or (
                "nnue" in key.casefold() and any(char in value for char in "/\\:")
            ):
                raise ProtocolError(
                    "invalid_rules", "Rule definitions cannot select local files", 422
                )
    if not names:
        raise ProtocolError("invalid_rules", "No variant definition was supplied", 422)
    return tuple(names)


def has_gating(request: SearchRequest) -> bool:
    ancestry: dict[str, str] = {}
    explicit: dict[str, bool] = {}
    section = ""
    if request.rules is not None:
        for raw in request.rules.ini.splitlines():
            match = re.fullmatch(r"\[([^\]:]+)(?::([^\]]+))?\]", raw.strip())
            if match is not None:
                section, base = match.groups()
                if base:
                    ancestry[section] = base
            elif section and "=" in raw:
                key, value = (part.strip() for part in raw.split("=", 1))
                if key == "gating" and value in ("true", "false"):
                    explicit[section] = value == "true"
    ancestor = request.engine_variant
    for _ in range(64):
        if ancestor in explicit:
            return explicit[ancestor]
        if ancestor in ("seirawan", "shouse"):
            return True
        if ancestor not in ancestry:
            return False
        ancestor = ancestry[ancestor]
    return False


def admit_alice_fen(fen: str) -> None:
    """Accept Pychess's compact two-layer FEN before touching the native library."""
    fields = fen.split()
    ranks = fields[0].split("/")
    if (
        len(fields) != 6
        or len(ranks) != 8
        or fields[3] != "-"
        or fields[1] not in ("w", "b")
        or any(
            re.fullmatch(r"(?:[1-8]|\|?[pnbrqkPNBRQK])+", rank) is None or width(rank) != 8
            for rank in ranks
        )
        or any(not value.isascii() or not value.isdigit() or len(value) > 9 for value in fields[4:])
        or int(fields[5]) < 1
        or re.fullmatch(r"-|K?Q?k?q?", fields[2]) is None
    ):
        raise ProtocolError("invalid_position", "Invalid compact Alice FEN", 422)
    pieces = Counter(char for char in fields[0] if char.isalpha())
    if (
        pieces["K"] != 1
        or pieces["k"] != 1
        or any(char in "pP" for rank in (ranks[0], ranks[-1]) for char in rank)
    ):
        raise ProtocolError("invalid_position", "Invalid Alice material", 422)
    for roles in ("PNBRQK", "pnbrqk"):
        pawn, knight, bishop, rook, queen, _king = roles
        surplus = sum(
            max(0, pieces[role] - base)
            for role, base in ((knight, 2), (bishop, 2), (rook, 2), (queen, 1))
        )
        if (
            sum(pieces[role] for role in roles) > 16
            or pieces[pawn] > 8
            or surplus > 8 - pieces[pawn]
        ):
            raise ProtocolError("invalid_position", "Alice material exceeds engine limits", 422)


def validate_position(request: SearchRequest) -> bool:
    variant = request.engine_variant
    if ALICE:
        if (
            request.variant != "alice"
            or variant != "alice"
            or request.rules is not None
            or request.chess960
        ):
            raise ProtocolError("unsupported_variant", "Alice supports only its fixed rules", 422)
        admit_alice_fen(request.initial_fen)
        admit_alice_fen(request.current_fen)
    elif request.variant == "alice" or variant == "alice":
        raise ProtocolError("unsupported_variant", "Configure the optional Alice adapter", 422)
    if request.variant in HIDDEN_VARIANTS or variant in HIDDEN_VARIANTS:
        raise ProtocolError(
            "hidden_information", "Hidden-information analysis needs a dedicated adapter", 422
        )
    if request.rules is not None:
        sections = rule_sections(request.rules.ini)
        if variant not in sections or any(name in pyffish.variants() for name in sections):
            raise ProtocolError(
                "rules_mismatch", "Custom rules must define a fresh exact engine key", 422
            )
        pyffish.load_variant_config(request.rules.ini)
        if any(name not in pyffish.variants() for name in sections):
            raise ProtocolError("invalid_rules", "A definition or inherited base was rejected", 422)
    if variant not in pyffish.variants():
        raise ProtocolError(
            "unsupported_variant",
            "These rules are unavailable; custom rules require exact definitions",
            422,
        )
    if pyffish.two_boards(variant):
        raise ProtocolError(
            "two_board_adapter_required",
            "Two-board analysis needs its dedicated scheduling adapter",
            422,
        )
    start = pyffish.start_fen(variant)
    root = admit_fen(request.initial_fen, start)
    current = admit_fen(request.current_fen, start)
    gating_family = has_gating(request)
    for fen in (root, current):
        status = pyffish.validate_fen(fen, variant, request.chess960)
        if status != pyffish.FEN_OK and not request.chess960 and gating_family:
            status = pyffish.validate_fen(fen, variant, True)
        if status != pyffish.FEN_OK:
            raise ProtocolError("invalid_position", "Position is invalid for these rules", 422)
    history: list[str] = []
    for move in request.moves:
        if move not in pyffish.legal_moves(variant, root, history, request.chess960):
            raise ProtocolError("invalid_history", "Move history contains an illegal move", 422)
        history.append(move)
    # Ordinary chess views omit optional ~ markers after promotion. Preserve explicit
    # markers; native FEN always retains rule-critical pocket/shogi promotion state.
    show_promoted = "~" in current.split()[0]
    resulting = pyffish.get_fen(variant, root, history, request.chess960, False, show_promoted)
    expected = pyffish.get_fen(variant, current, [], request.chess960, False, show_promoted)
    if resulting != expected:
        raise ProtocolError(
            "position_mismatch", "Selected position does not match its exact move history", 422
        )
    return (
        not pyffish.legal_moves(variant, root, history, request.chess960)
        or (pyffish.is_immediate_game_end(variant, root, history, request.chess960)[0])
    )


def normalize(request: SearchRequest, moves: list[str]) -> list[str]:
    history = list(request.moves)
    valid: list[str] = []
    for move in moves[:MAX_PV_MOVES]:
        if not isinstance(move, str) or move not in pyffish.legal_moves(
            request.engine_variant, request.initial_fen, history, request.chess960
        ):
            break
        valid.append(move)
        history.append(move)
    return valid


def main() -> None:
    request: SearchRequest | None = None
    for raw in sys.stdin.buffer:
        try:
            if len(raw) > MAX_BODY_BYTES + 1024:
                raise ProtocolError("invalid_request", "Rules message exceeds the input limit")
            message = json.loads(raw)
            operation = message.get("op")
            if operation == "start" and request is None:
                candidate = SearchRequest.parse(message["request"])
                terminal = validate_position(candidate)
                request = candidate
                result = {"ok": True, "terminal": terminal}
            elif operation == "pv" and request is not None:
                moves = message.get("moves")
                if not isinstance(moves, list) or len(moves) > MAX_PV_MOVES:
                    raise ProtocolError("invalid_request", "Invalid PV input")
                result = {"ok": True, "moves": normalize(request, moves)}
            elif operation == "capabilities" and request is None:
                result = {
                    "ok": True,
                    "variants": [
                        v
                        for v in pyffish.variants()
                        if (
                            v == "alice"
                            if ALICE
                            else not pyffish.two_boards(v)
                            and v not in HIDDEN_VARIANTS
                            and v != "alice"
                        )
                    ],
                    "version": list(pyffish.version()),
                }
            else:
                raise ProtocolError("invalid_request", "Invalid rules operation")
        except ProtocolError as exc:
            result = {"ok": False, "code": exc.code, "message": str(exc), "status": exc.status}
        except (ValueError, TypeError, KeyError, OverflowError):
            result = {
                "ok": False,
                "code": "invalid_position",
                "message": "Rules backend rejected this input",
                "status": 422,
            }
        print(json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
