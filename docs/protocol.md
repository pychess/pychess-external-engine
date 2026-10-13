# Loopback analysis protocol v1

This contract governs paired native searches between the Pychess frontend and the
local application. See [architecture.md](architecture.md) for process ownership.

## Connection

Default address: `http://127.0.0.1:9670`. Bind only to loopback; the locally configured
port may change. Do not accept remote addresses in the frontend connection UI.
Browser support and transport restrictions are documented in
[compatibility.md](compatibility.md).

Both endpoints require an explicitly allowed HTTP(S) `Origin`, an exact local
`Host` authority, and `Authorization: Bearer <pairing-token>`. A fresh 256-bit token
is generated on each application start. Restarting disconnects searches and rotates
the token; browser reconnection must ask for the new token rather than silently retry
authentication. No cookies or Pychess account/API tokens are used.

Allow unauthenticated preflight for the two known routes and only their required
methods/headers. Echo the approved origin rather than `*`, use `Vary: Origin`, and
return `Cache-Control: no-store`. Requests omit credentials. Older PNA preflights
receive `Access-Control-Allow-Private-Network: true` only for approved origins.
The frontend uses `targetAddressSpace: "loopback"` where supported and presents
permission/connection failures without automatically retrying a permission prompt.

## Endpoints

| Route | Method | Response |
| --- | --- | --- |
| `/v1/capabilities` | GET | Authenticated engine/rules capabilities, local resource limits, protocol version, and score convention |
| `/v1/analyse` | POST | `application/x-ndjson` stream with started, evaluation, completed, or error events |

Pre-stream failures use JSON `{ "protocolVersion": 1, "error": { "code": "..." } }`
and meaningful HTTP status: 400 invalid request, 401 pairing required, 403 rejected
origin/host, 409 engine busy, 413 oversized body, 415 incorrect content type,
422 unsupported rules/position, and 503 unavailable engine. An error after streaming
starts is an error event followed by stream closure.

## Analysis request

```json
{
  "protocolVersion": 1,
  "sessionId": "random-tab-session",
  "searchId": "unique-search-id",
  "variant": "chess",
  "engineVariant": "chess",
  "chess960": false,
  "initialFen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "moves": ["e2e4"],
  "currentFen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
  "threads": 1,
  "hash": 16,
  "multiPv": 1,
  "search": {"depth": 18}
}
```

For custom rules add `rules: { "ini": "...", "sha256": "..." }`. Include the exact
inheritance chain and Study snapshot. Hash the UTF-8 bytes of the exact INI text;
the bridge verifies the digest before registration. Capability discovery reports
whether the engine/rules backend can load these definitions. Do not equate absence
of a not-yet-loaded custom name with inability to load custom rules.

`initialFen` and `moves` retain repetition/history semantics. Compare their resolved
position with `currentFen` using the rules backend's canonicalization, not raw
string equality: en-passant and castling notation can differ. Reject a genuine
mismatch. Use the resolved engine variant and Chess960 mode consistently.

Move tokens are extended Fairy-Stockfish coordinates, not chess-only UCI types.
Preserve multi-digit ranks, fairy pieces, drops, promotion/demotion suffixes, gating,
and compound moves. Newlines/NULs cannot enter UCI commands.

Session and search IDs are 1–64 ASCII letters/digits/underscores/hyphens. Generate a
fresh session per tab and a fresh search ID for every search. The browser additionally
associates each search with the selected analysis node and rules snapshot.

## Evaluation events

Every event includes `protocolVersion: 1` and the originating `searchId`. A started
event acknowledges engine ownership. Evaluation events contain elapsed milliseconds,
nodes, depth, and available PVs; each PV includes its depth, move array, MultiPV
index, and either `cp` or `mate`. Retain bound information when applicable.
Scores are **side-to-move relative**, advertised as `scorePov: "sideToMove"`.

Completed events include a reason such as search limit or terminal position and
optional bestmove/ponder. Error events carry a stable error code and a bounded,
credential-free message. Heartbeats, if needed, are separate events rather than fake
evaluations. All prototype events include `prototype: true`; production clients
must reject them as analysis data.

The engine parser accepts different `info` field orders and partial MultiPV updates.
Validate PV prefixes against the original search position/history and rules.
Never attribute delayed output to a replacement position because its first move
happens to remain legal there.

## Ownership, cancellation, and bounds

One engine process serves one search at a time. Any concurrent request, including
one from the same tab, receives `409 engine_busy`. The frontend aborts its old stream
and retries a replacement only within a short bounded cleanup window. Another tab
cannot steal ownership. There is no unbounded pending-work queue.

An aborted request, closed tab, disconnected connection, reached limit, or engine
failure releases the lease. Stop/drain the previous search through `bestmove` before
changing options or sending a new position. Use bounded stop/readiness deadlines and
replace a stuck subprocess. Session/rules transitions control `ucinewgame` and hash reuse.

Initial production bounds, exposed in capabilities and adjustable downwards locally:

- Request body: 256 KiB; INI: 64 KiB; FEN: 4096 ASCII characters; move history: 1000 tokens.
- Engine output line: 64 KiB; returned PV: 64 moves; requested MultiPV: at most 20.
- Threads and Hash: constrained by locally configured and advertised engine limits;
  default maximum Hash is 512 MiB. Optional unsupported options are not sent.
- Search: positive depth, movetime, or nodes; specify at least one supported limit.
  A locally configured wall-clock ceiling applies even to effectively infinite searches.
- Handshake/readiness/stop deadlines: finite local configuration; failures restart
  the engine and report an error rather than leaving a lease held.

## Optional Alice adapter

When configured and qualified, `engine.variants` includes `alice`, and
`engine.adapters.alice` contains its own engine name, resource ceilings and variant
list (`["alice"]`). Its `chess960` and `customRules` flags are both false.
`rules.aliceVersion` records the optional rules library version. Clients must use
these adapter capabilities for Alice rather than infer support from the primary
engine's custom-rule flag. An ordinary bridge omits this adapter.

Alice requests require both `variant` and `engineVariant` to be `alice`,
`chess960: false`, no `rules` payload, and Pychess compact FEN. A `|` before a piece
marks its layer; coordinate moves stay unchanged. Both FENs and the complete history
are checked with the isolated Alice library. One owner covers both adapters, and
switching closes the previous native engine before launching the next.
