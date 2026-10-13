# Install and use the local engine app

The published alpha is `0.1.0a1`. Connection controls require Pychess's external-engine
frontend integration.

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
versioned alpha with your native engine:

```bash
uvx --python 3.13 pychess-external-engine@0.1.0a1 \
  --engine /absolute/path/to/fairy-stockfish --setoption 'Use NNUE' false
```

uv manages Python and the isolated dependencies; there is no project checkout or
manual virtualenv setup. Both rules libraries are required dependencies. uv can
download a matching Python when missing. Engines and networks are not downloaded
by this release. Keep the terminal running, pair the browser, and stop with Ctrl+C.

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
uvx --python 3.13 pychess-external-engine@0.1.0a1 \
  --engine /absolute/path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --check-engine
```

Then start the bridge:

```bash
uvx --python 3.13 pychess-external-engine@0.1.0a1 \
  --engine /absolute/path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --max-threads 4 --max-hash 256
```

The terminal prints `http://127.0.0.1:9670` and a temporary pairing token. Leave the
application running while analysing. Engine discovery happens before the HTTP
service becomes ready. Stop it with Ctrl+C; active searches and native/rules
subprocesses are released. Restarting rotates the token.

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
| Repeated `--setoption NAME VALUE` | Locally configured, advertised engine options |
| `--max-threads` | CPU thread ceiling; defaults to detected CPU count |
| `--max-hash` | Hash ceiling in MiB; defaults to 512 |
| `--max-search-seconds` | Search wall-clock ceiling; defaults to 900 seconds |
| `--idle-seconds` | Idle native-process lifetime; defaults to 300 seconds; a later search relaunches it |
| `--port` | Loopback HTTP port; defaults to 9670 |
| Repeated `--allow-origin` | Exact permitted HTTP(S) origin; overrides the default `https://www.pychess.org` / `https://pychess.org` list |
| `--check-engine` | Discover configured engine options/capabilities and exit |

Threads, Hash, MultiPV, UCI_Variant, UCI_Chess960 and UCI_AnalyseMode are managed by
the bridge. Configure their ceilings through the dedicated options rather than
`--setoption`. MultiPV requests are capped at 20 and by the engine's bounds. Browser
resource requests can only reduce locally configured ceilings. Only one search owns
the bridge at a time, even when an optional Alice engine is configured.

For development, add the actual frontend origin, for example
`--allow-origin http://localhost:8080`. A scheme, host or port difference creates a
different origin. The local app binds to IPv4 loopback; use `127.0.0.1` in the pairing
address. LAN addresses and remote engines are outside this implementation.

## Pair and analyse

1. Open an analysis page in the updated Pychess frontend and open engine settings.
2. Select the external engine source, enter the local address and token, and click Connect.
3. Grant local-network/device-service permission if the browser asks.
4. Enable analysis using the normal engine switch; moves and tree navigation replace the current search.
5. Use Disconnect to return to browser analysis. Forget also removes the saved token.

Connection details are stored in this browser's local Pychess-origin preferences.
They are not sent to Heroku or synchronized through your Pychess account. A reload
restores a connection only after permitted access; otherwise click Connect. A second
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
uvx --python 3.13 pychess-external-engine@0.1.0a1 \
  --engine /absolute/path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --alice-engine /absolute/path/to/alice-stockfish \
  --alice-setoption EvalFile /absolute/path/to/Alice_v1.nnue
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
| Current pairing token requested | Copy the token from the current app instance; restarting invalidates the previous token |
| Another tab is using the engine | Stop analysis in that tab or close it, then retry Connect; there is no job queue |
| Cannot analyse this variant | Check the actual native/rules capabilities; install/configure Alice separately when needed |
| Variant definition rejected | Use compatible engine/rules versions and the exact saved definition; unsupported keys require a newer verified rules adapter |
| Analysis fails after pairing | Check executable/network options locally, then reconnect; hung/crashed engines are stopped and ownership is released |
| Browser controls absent | Use the updated Pychess frontend; this local package does not deploy website changes |
| Port already in use | Stop the old app or choose another `--port`, then update the pairing address |
| Permission denied | Change the browser's site permission and click Connect; the application does not repeatedly prompt |
| Safari/WebKit cannot connect | This release does not support them; a trusted local-TLS or extension fallback remains future work |

For development and repeatable checks, see [development.md](development.md). Preserve upstream licenses when distributing
engines, dependencies or networks; [NOTICE.md](../NOTICE.md) records attribution.
