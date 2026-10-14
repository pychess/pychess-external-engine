# Install and use the local engine app

The published alpha is `0.1.0a1` and uses the previous manual connection flow.
Account-token registration in this checkout is unreleased and requires the matching
Pychess frontend/server changes. The instructions below describe that new flow.

## Requirements

Use Linux x86_64 with a compatible native UCI executable. The initial browser matrix
is Chromium 154 or Edge 155 on Linux. Firefox's transport prototype passed, but its
native UI and denial flow remain experimental. Safari, Windows and macOS are not
verified for this release. See [compatibility](compatibility.md) for browser and
engine coverage.

Clean installations are verified on CPython 3.11.13, 3.12.9, 3.13.13 and 3.14.1.
The supported interpreter range is Python 3.11–3.14; newer versions remain unverified.
The package requires pinned pyffish 0.0.90 and pyffish-alice 0.0.3. If a matching
binary wheel is unavailable, dependency installation
may require a C++ compiler and Python development headers. The application wheel
itself contains Python code; native engines and networks are not bundled.

## Recommended: uvx

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then launch the
source checkout with your own compatible UCI engine and an account engine token:

```bash
uvx --python 3.13 --from . pychess-external-engine \
  --engine /absolute/path/to/your-uci-engine --token-file /absolute/path/to/your-token
```

uv manages Python and the isolated dependencies. Until this flow is released,
run from this source checkout. Both rules libraries are required dependencies. uv can
download a matching Python when missing. Engines and networks are not downloaded
by this package. Keep the terminal running, select the engine in analysis settings,
and stop with Ctrl+C. Fairy-Stockfish and Alice-Stockfish below are tested examples;
users choose and supply their own compatible UCI executables and networks.

## Account token

Sign in to Pychess and open **Account → External engines** (`/account/engines`).
Create a token for this local installation and copy it once into a local file.
Pass that file using `--token-file`, or set `PYCHESS_API_TOKEN` in the app's environment.
Use a separate token for each installation. Engine tokens permit registration and
discovery without BOT gameplay access; no account upgrade is needed.

## Install from this source checkout

Use an isolated environment. From the `pychess-external-engine` checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/pychess-external-engine --help
```

Both rules libraries are included. If using a built wheel, replace `.` with its path.
To install source from an archive, unpack it and run the same commands inside the
unpacked directory. Keep the source checkout when
you want the reproducible probes under `tools/`; those development tools are not
installed as part of the application wheel.

Users with `uv` can instead run `uv sync` and prefix commands with `uv run`.
Contributors use `uv sync --extra dev`.

## Configure and start

Make the native executable runnable and choose absolute paths. First inspect its
advertised capabilities:

```bash
uv run pychess-external-engine \
  --engine /absolute/path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --check-engine
```

Then start the bridge:

```bash
uv run pychess-external-engine \
  --engine /absolute/path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --max-threads 4 --max-hash 256 \
  --token-file /absolute/path/to/your-token
```

The terminal reports that the engine is registered with Pychess. Leave the application
running while analysing. Native discovery and loopback binding precede registration.
Stop it with Ctrl+C; active searches and subprocesses are released and the registration
is removed. Restarting refreshes the registration automatically.

Fairy-Stockfish can search ordinary supported variants without NNUE. To use a
compatible network, configure the engine's advertised network option locally, for
example `--setoption EvalFile /absolute/path/to/network.nnue`, and enable NNUE.
Follow that engine's naming/variant requirements: the bridge does not download or
choose networks. Never put executable paths, network paths or pairing tokens in a
Pychess page URL, PGN or Study.

Configuration switches:

| Switch | Meaning |
| --- | --- |
| `--engine`, repeated `--engine-arg` | Local executable and its argument array; no shell invocation |
| `--token-file` or `PYCHESS_API_TOKEN` | Engine-scoped account token used only for website registration |
| `--site` | Pychess website origin; defaults to `https://www.pychess.org`; HTTP loopback is allowed for development |
| Repeated `--setoption NAME VALUE` | Locally configured, advertised engine options |
| `--max-threads` | CPU thread ceiling; defaults to detected CPU count |
| `--max-hash` | Hash ceiling in MiB; defaults to 512 |
| `--max-search-seconds` | Search wall-clock ceiling; defaults to 900 seconds |
| `--idle-seconds` | Idle native-process lifetime; defaults to 300 seconds; a later search relaunches it |
| `--port` | Loopback HTTP port; defaults to 9670 |
| Repeated `--allow-origin` | Extra exact allowed frontend origins; the configured site is always allowed |
| `--check-engine` | Discover configured engine options/capabilities and exit |

Threads, Hash, MultiPV, UCI_Variant, UCI_Chess960 and UCI_AnalyseMode are managed by
the bridge. Configure their ceilings through the dedicated options rather than
`--setoption`. MultiPV requests are capped at 20 and by the engine's bounds. Browser
resource requests can only reduce locally configured ceilings. Only one search owns
the bridge at a time, even when an optional Alice engine is configured.

For development, add the actual frontend origin, for example
`--allow-origin http://localhost:8080`. A scheme, host or port difference creates a
different origin. The local app binds to IPv4 loopback. LAN addresses and remote
engines are outside this implementation. Use `--site http://localhost:8080` when
registering with a local development website.

## Pair and analyse

1. Start the app with your own engine and account token, then sign in to Pychess.
2. Open analysis engine settings and select your registered engine by name.
3. Grant local-network/device-service permission if the browser asks.
4. Enable analysis using the normal engine switch; moves and tree navigation replace the current search.
5. Select the browser engine to switch back. The remove button deletes the registration.

Only the selected engine ID and source are stored in browser preferences. The website
stores an expiring, owner-only registration with a separate local connection credential;
the account token stays in the local app. Search traffic goes directly to loopback.
A reload restores a connection only after permitted access; otherwise select your engine. A second
tab receives an engine-busy message and cannot replace the first tab's search.
Stop analysis or close the owner tab before retrying in another tab.

Analysis obeys the existing ongoing-game, anti-cheat, Study computer-permission,
concealed-content and Practice restrictions. External capability is independent of
WASM search support. Study historical rules remain tied to their exact definitions.
Transient native evaluations are displayed locally and do not become saved server
analysis/advice.

## Custom variants and Alice

The frontend supplies the minimum exact INI inheritance chain plus its UTF-8 SHA-256
hash. The bridge validates the hash, rule names, geometry/material and full move
history, checks native acceptance, then loads those exact bytes before discovering
the engine's custom capability. Historical Study definitions replace conflicting
local definitions for that search. Changing the rules digest restarts the process.

A locally configured `--setoption VariantPath /absolute/path/to/variants.ini` can
advertise additional ordinary variants, but it does not replace the exact definitions
sent by the page. Unknown rule keys, stale/missing inheritance, incompatible definitions
and arbitrary file-path directives are rejected. Pinned rules-library compatibility
can limit a newer native engine's custom features.

Alice rules are included automatically. Configure a separate Alice engine:

```bash
uv run pychess-external-engine \
  --engine /absolute/path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --alice-engine /absolute/path/to/alice-stockfish \
  --alice-setoption EvalFile /absolute/path/to/Alice_v1.nnue \
  --token-file /absolute/path/to/your-token
```

Use repeated `--alice-engine-arg` and `--alice-setoption` for its local configuration.
Startup qualifies Alice with a legal shallow search. A failed Alice configuration
stops startup; omit Alice options to run the ordinary bridge.
Alice uses compact `|` FEN and fixed rules; Alice Chess960/custom definitions are
unsupported. The tested Alice-Stockfish 1.0 uses its Legacy Alice_v1.nnue network.
Its NNUE-disabled mode produces zero diagnostic scores, useful for tests but not
meaningful positional analysis.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Unable to connect | Start the app, wait for its HTTP startup banner, check the IPv4 address/port, and allow local network access for the page origin |
| Engine not listed | Sign in with the token's owner account, start the app, wait for registration, then reload the page |
| Invalid or revoked account token | Create an engine token in account settings and update the local token file/environment |
| Another tab is using the engine | Stop analysis in that tab or close it, then Retry; there is no job queue |
| Cannot analyse this variant | Check the actual native/rules capabilities; install/configure Alice separately when needed |
| Variant definition rejected | Use compatible engine/rules versions and the exact saved definition; unsupported keys require a newer verified rules adapter |
| Analysis fails after pairing | Check executable/network options locally, then reconnect; hung/crashed engines are stopped and ownership is released |
| Browser controls absent | Use the updated Pychess frontend; this local package does not deploy website changes |
| Port already in use | Stop the old app or choose another `--port`; registration uses the configured port automatically |
| Permission denied | Change the browser's site permission and select the engine again or Retry; the app does not repeatedly prompt |
| Safari/WebKit cannot connect | This release does not support them; a trusted local-TLS or extension fallback remains future work |

For development and repeatable checks, see [development.md](development.md). Preserve upstream licenses when distributing
engines, dependencies or networks; [NOTICE.md](../NOTICE.md) records attribution.
