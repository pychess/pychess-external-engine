# Browser transport verification

## Method

The probe starts a temporary paired loopback application and a fresh browser profile.
It loads the deployed `https://www.pychess.org/analysis/chess` page and runs cross-origin
requests from that page. It does not disable web security or local-network checks.
Chromium permission allow/deny states are set for that origin in the disposable test
context. These checks prove transport behaviour, not a user-facing permission prompt
or an integrated native-engine UI.

Checks cover authenticated capabilities, incorrect pairing, incremental NDJSON output,
competing tabs, abort and tab-close cleanup, replacement search identity, and denied
loopback permission. The deployed page currently serves `COOP: same-origin` and
`COEP: require-corp`, without a CSP or Permissions-Policy in the observed response.
Chromium remains a secure, cross-origin-isolated context with SharedArrayBuffer.
No Pychess header changes have been needed for the passing Chromium transport probe.

## Results on 2026-10-13

| Browser/build | OS | Result | Remaining verification |
| --- | --- | --- | --- |
| Chromium 154.0.8037.57 | Linux x86_64 | All automated transport and allow/deny checks passed | Real user prompt and later WASM/native-engine UI checks |
| Playwright Firefox 155.0 | Linux x86_64 | Headed probe passed after the user chose Allow: paired streaming, competing tabs, abort/tab-close cleanup, two released streams | Permission denial and later WASM/native-engine UI checks |
| Playwright WebKit 26.6 | Linux x86_64 | HTTP `127.0.0.1` and `localhost` fetches rejected as “Not allowed to request resource” | Real Safari/macOS check and transport fallback evaluation |
| Microsoft Edge 155.0.4283.45 | Linux x86_64 | All automated transport and allow/deny checks passed | Real user prompt and later WASM/native-engine UI checks |
| Safari | — | Not tested | Real Safari/macOS check; Linux WebKit is not Safari |

The first-release browser matrix remains open. HTTP loopback is proven for Chromium,
Edge, and Firefox on this Linux machine, but universal browser support has not been
established. The Firefox result was supplied by the user from the headed probe;
see [the recorded report](results/firefox-linux-2026-10-13.json). If Safari also blocks this transport, evaluate
explicit local TLS with a trusted certificate or the planned extension/native-messaging
adapter before adding it to the supported matrix. Neither fallback is implemented.

The installed Edge binary was verified separately with the same automated checks;
see [its recorded report](results/edge-linux-2026-10-13.json). Safari is unavailable
on this Ubuntu machine, as confirmed by the user. Do not require its result before
implementing the native engine layer, and keep Safari outside the verified matrix
until a real macOS check and any necessary fallback are complete.

Chromium and Edge also passed the production native API with Fairy-Stockfish
`051026 LB` and NNUE disabled: validated PVs streamed from the HTTPS page, competing
tabs were rejected, and abort/tab-close released real search work. Permission denial
still blocked access. See the [Chromium native report](results/chromium-native-linux-2026-10-13.json)
and [Edge native report](results/edge-native-linux-2026-10-13.json). These are transport
and native API checks; engine-selection UI integration remains separate work.

The integrated UI now passes in Edge for Chess and Orda, and Chromium for CWDA with
WASM search deliberately blocked. The UI probe serves the built local Pychess
checkout through a synthetic HTTPS origin with an in-memory database; it is separate
from the earlier checks against the deployed page. Source selection, native PV-click
navigation, competing tabs, tab-close cleanup, reconnect, forget and permission-denial
messages pass. Settings fit desktop/mobile viewports in light/dark themes, with focus
and browser-only NNUE-control visibility verified through computed styles.

Reports: [Edge Chess](results/edge-ui-chess-linux-2026-10-13.json),
[Edge Orda](results/edge-ui-orda-linux-2026-10-13.json), and
[Chromium CWDA](results/chromium-ui-cwda-linux-2026-10-13.json).
Wider variants, Study transitions, bridge restarts and Firefox UI remain open.

An explicit E-file-king Capablanca position resolves to `embassy` and passes the same
Edge UI checks; see [the castling-profile report](results/edge-ui-embassy-linux-2026-10-13.json).
Use `tools.ui_probe --variant capablanca --fen '...'` to repeat that probe.

## Reproduce

From this checkout, use an environment with `aiohttp` and `playwright` installed.
Install the required test browser with `python -m playwright install firefox` or
`python -m playwright install webkit`; Chromium can use an installed system binary.

```bash
python -m tools.browser_probe --browser chromium --output /tmp/chromium-probe.json
python -m tools.browser_probe --browser edge --output /tmp/edge-probe.json
python -m tools.browser_probe --browser firefox --headed --manual-permission --output /tmp/firefox-probe.json
python -m tools.browser_probe --browser webkit --output /tmp/webkit-probe.json
```

The headed command opens a disposable browser and waits up to 60 seconds for Allow.
The test requires the configured HTTPS site to be reachable. For a different served
HTTPS development page, use `--site https://.../analysis/chess`. Reports contain no
pairing token and failed checks exit nonzero. The application closes with the test.

Automated permission overrides are test infrastructure, not a production strategy.
The actual UI must ask users to connect, let browsers enforce permissions, and explain
denial or an unavailable bridge without repeatedly prompting.
