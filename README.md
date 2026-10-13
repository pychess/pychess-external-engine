# Pychess External Engine

Local native-engine analysis for the Pychess website.

## Status

The paired native HTTP API is implemented. It launches configured UCI executables,
discovers capabilities, and owns serialized searches with structured MultiPV events
and bounded cancellation/restart/idle handling. An isolated `pyffish` worker validates
positions, histories and PVs with geometry and material limits. Custom definitions
are checked with the native engine, loaded before discovery, and replaced on
rules-hash changes. The sibling Pychess checkout now provides source selection,
pairing and native evaluation/PV display for single-board analysis. Deployment and
release preparation remain separate steps.

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
with exact custom rules included. Alice has an optional dedicated engine/rules
adapter. Two-board analysis, hidden-information variants, and USI/UCCI adapters
remain deferred.

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

Start the local bridge (without `--check-engine`):

```bash
uv run pychess-external-engine --engine /path/to/fairy-stockfish \
  --setoption 'Use NNUE' false
```

It binds to `127.0.0.1:9670` and prints a temporary pairing token. Restart to rotate
the token. Use `--port` or repeated `--allow-origin` to configure local access;
explicit origins replace the default Pychess origins. `--max-search-seconds` and
`--idle-seconds` set the search ceiling and idle process lifetime. Networks and their
paths are configured locally through engine options; the example disables NNUE.
The HTTP contract is in [docs/protocol.md](docs/protocol.md). In the updated Pychess
analysis page, open engine settings, enter the address/token and choose Connect.
Enable analysis with the existing engine switch. Disconnect switches to the browser
engine; Forget also removes the browser's saved pairing token. The development
browser probe can exercise the native API independently:

```bash
uv run python -m tools.browser_probe --browser edge \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false
```

The probe runs its own temporary bridge and browser profile. Chromium and Edge on
Linux pass native streaming, competing tabs, abort/tab-close cleanup and permission
denial from the deployed HTTPS analysis page. Firefox's recorded check used the
transport prototype; a native Firefox UI check remains pending.

Verify the integrated UI using a built sibling Pychess checkout and its development
virtualenv (including its Mongo mock and Playwright dependencies):

```bash
uv run python -m tools.ui_probe --browser edge --pychess ../pychess-variants \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false
```

This probe starts a Pychess test server with an in-memory database and the real local
bridge. It serves the checkout through a synthetic HTTPS origin using Playwright
routing; it does not modify the deployed website or disable browser security.
It checks native PV navigation, competing tabs, tab closure, reconnect, forget,
permission denial, and mobile/desktop light/dark settings. Use `--variant orda`
for exact site rules or `--variant cwda --without-wasm` to verify independence from
browser search. Add `--restart-bridge` to verify shutdown, token rotation and re-pairing.
Use `--variant alice --expect-unsupported` without an Alice engine to verify
capability rejection in the UI. See [browser compatibility](docs/browser-compatibility.md) for evidence.

Verify Study chapter changes, historical rules snapshots, Practice ownership, and
computer permissions with a temporary Study database:

```bash
uv run python -m tools.study_probe --browser edge --pychess ../pychess-variants \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false
```

## Optional Alice engine

Install the optional rules library and configure a separate Alice-capable UCI engine:

```bash
uv sync --extra dev --extra alice
uv run pychess-external-engine --engine /path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --alice-engine /path/to/alice-stockfish \
  --alice-setoption EvalFile /path/to/Alice_v1.nnue
```

Alice options and executable arguments use `--alice-setoption` and `--alice-engine-arg`.
They stay on the local computer. Startup requires the Alice rules library, an engine
advertising `alice`, and a shallow search returning a legal best move. A missing or
incompatible Alice configuration stops startup with an error; omit Alice options to
run the ordinary engine alone. The bridge closes the previous native process before
switching engines, with one search owner shared across all variants and tabs.

Alice uses fixed rules, compact FEN with `|` before looking-glass-board pieces, and
ordinary coordinate move tokens. The adapter validates full move histories and PVs
with pinned `pyffish-alice` 0.0.3. Alice custom definitions, legacy 16-wide FEN input,
and Alice Chess960 are outside this adapter's capability. NNUE files must match the
chosen Alice engine. The tested Alice-Stockfish 1.0 binary uses `Alice_v1.nnue` in
Legacy evaluation mode; disabling its NNUE selects zero diagnostic evaluation,
which is useful for lifecycle tests but not meaningful positional evaluation.

To repeat the integrated Edge check:

```bash
uv run python -m tools.ui_probe --browser edge --variant alice --without-wasm \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false \
  --alice-engine /path/to/alice-stockfish \
  --alice-setoption EvalFile /path/to/Alice_v1.nnue
```

For native unit checks, add `PYCHESS_TEST_ALICE_ENGINE` and optionally
`PYCHESS_TEST_ALICE_NETWORK` alongside `PYCHESS_TEST_ENGINE`. Without the network
variable, Alice tests use zero diagnostic evaluation.

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
Production rules validation uses a separate process for each search, keeping custom
registrations and native failures out of the bridge process. Tests cover exact rules
hashes, inherited snapshots, Chess960, pockets, promotion state, extended coordinates,
S-Chess gating and malformed inputs. Native-engine compatibility with supplied custom
definitions is checked separately before search. The pinned library's supported
rule-name schema also rejects keys it would otherwise silently ignore. Historical
custom rules replace local `VariantPath` definitions for that search. A changed rules
hash restarts the native engine to avoid stale or accumulating registrations.

See [verified variant/engine coverage](docs/compatibility.md) for the representative
native and UI checks and their limits.

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
