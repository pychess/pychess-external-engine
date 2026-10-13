# Pychess External Engine

Local native-engine analysis for the Pychess website.

## Status

Phase 1 is in progress. A loopback transport prototype supports pairing, CORS,
fake streamed evaluations, cancellation, and exclusive ownership between tabs.
It does not run an engine or integrate with the Pychess UI yet.

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
transport, Firefox's pending manual permission check, and the Linux WebKit limitation.
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

This workspace currently contains documentation only. Preserve upstream licenses and
attribution when implementation code is adapted from these projects.
