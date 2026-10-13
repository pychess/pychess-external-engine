# Pychess External Engine

Local native-engine analysis for the Pychess website.

## Status

Phase 2 is in progress. The package launches configured UCI executables, discovers
capabilities, and owns serialized searches with structured MultiPV events and bounded
cancellation/restart/idle handling. Real native searches are verified through tests.
Production rules admission and the paired HTTP search binding are next; the separate
loopback prototype still returns fake results. Pychess UI integration has not started.

The design and implementation checklist are maintained in
[Pychess's external-engine plan](../pychess-variants/docs/external-engine.md)
in the sibling checkout. Its repository path is `docs/external-engine.md` in
[pychess-variants](https://github.com/pychess/pychess-variants).

## Design

One Python application runs on the user's computer and combines a local HTTP bridge,
UCI subprocess management, and `pyffish` rules validation. The Pychess analysis page
connects directly to the bridge through a paired loopback connection and displays
streamed evaluations from the user's native engine.

No additional Heroku process or public broker is required. Engine executables,
networks, resource limits, and pairing are configured locally. Native engines perform
the search; `pyffish` validates positions, histories, and principal variations.

The first implementation step is to prove HTTPS-to-loopback streaming and cancellation
in supported browsers. Ordinary single-board Fairy-Stockfish analysis comes first,
with exact custom rules included in the protocol design. Alice, two-board analysis,
hidden-information variants, and USI/UCCI adapters need separate capability checks.

## Development installation and engine discovery

Python 3.11 or later is required. The development environment is currently verified
on Python 3.13.13; broader interpreter/OS installation checks remain a release task.

```bash
uv sync --extra dev
uv run pychess-external-engine --engine /path/to/fairy-stockfish --check-engine
```

To include site-defined variants and configure local engine options:

```bash
uv run pychess-external-engine --engine /path/to/fairy-stockfish \
  --setoption VariantPath /path/to/pychess-variants/variants.ini \
  --setoption 'Use NNUE' false --check-engine
```

The executable is launched with an argument array, without a shell. Use repeated
`--engine-arg` options for executable arguments and `--max-threads`/`--max-hash` for
local resource ceilings. Discovery applies configured options, waits for readiness,
then re-queries UCI capabilities so newly loaded custom variants are advertised.
Search-controlled options such as Threads and UCI_Variant cannot be configured with
`--setoption`; other configured options must be advertised by the engine.

At this development stage the installed CLI requires `--check-engine` and exits
after discovery. The prototype below remains explicitly fake.

Development checks:

```bash
uv run ruff format --target-version py313 --check .
uv run ruff check .
uv run pyrefly check
uv run python -m unittest discover -s tests -v
```

Run the native smoke checks with your Fairy-Stockfish executable:

```bash
PYCHESS_TEST_ENGINE=/path/to/fairy-stockfish \
  uv run python -m unittest discover -s tests -v
```

These checks disable NNUE and search trusted starting positions at depth 2 with
MultiPV 2 for Chess, Shogi, Xiangqi, and Grand Chess. They verify legal best moves
and PV prefixes with `pyffish`. Fake-engine tests cover cancellation, delayed stop
output, competing ownership, hung engines, crashes, output bounds, and idle races.
The production search controller requires a rules backend; test doubles are not
an admission API and are not exposed to browser requests.

Source is licensed under AGPL-3.0-or-later. See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE)
for upstream attribution and distribution notes. Engines and networks are user-supplied.

## Transport prototype

Run from this checkout with Python 3.11 or later and `aiohttp` installed:

```bash
uv run --with aiohttp python -m tools.transport_probe
```

The prototype binds only to `127.0.0.1:9670`, prints a temporary pairing token,
and allows `https://www.pychess.org` and `https://pychess.org`. Restarting rotates
the token. Use `--port` or repeated `--allow-origin` options to change local settings.
Specifying allowed origins replaces the defaults.

Requests to `GET /v1/capabilities` and `POST /v1/analyse` require an allowed `Origin`
and `Authorization: Bearer <pairing-token>`. The analysis request contains
`protocolVersion: 1`, `sessionId`, and `searchId`. Its streamed results are explicitly
marked `prototype: true` and must never be used as chess evaluations.

Only one stream can be active. Concurrent requests receive `409 engine_busy`;
aborting the active request releases ownership. Streams have a 30-second limit.
This prototype establishes transport semantics; the complete engine protocol is
specified in [docs/protocol.md](docs/protocol.md) for implementation in phases 2–4.

See [browser compatibility](docs/browser-compatibility.md) for the verified Chromium
transport, Firefox's successful headed permission check, and the Linux WebKit limitation.
The browser probe uses a temporary local application and a fresh browser profile:

```bash
python -m tools.browser_probe --browser chromium
python -m tools.browser_probe --browser firefox --headed --manual-permission
```

These commands require `playwright` in addition to `aiohttp`; install its Firefox
test browser with `python -m playwright install firefox` before the headed check.

Run the transport tests:

```bash
uv run --with aiohttp python -m unittest discover -s tests -v
```

## References

- [Lichess external-engine](https://github.com/lichess-org/external-engine)
- [Lichess lila-engine](https://github.com/lichess-org/lila-engine)
- [Fairy-Stockfish variant standards](https://fairy-stockfish.github.io/chess-variant-standards/)

Preserve upstream licenses and attribution when adapting implementation code from
these projects.
