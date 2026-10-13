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
| Playwright Firefox 155.0 | Linux x86_64 | `loopback-network` is in prompt state; headless fetch waits for permission | Run headed probe and manually Allow; Playwright cannot grant this permission |
| Playwright WebKit 26.6 | Linux x86_64 | HTTP `127.0.0.1` and `localhost` fetches rejected as “Not allowed to request resource” | Real Safari/macOS check and transport fallback evaluation |
| Edge | — | Not tested | Real Edge check; Chromium evidence alone is insufficient |
| Safari | — | Not tested | Real Safari/macOS check; Linux WebKit is not Safari |

The first-release browser matrix remains open. HTTP loopback is proven for Chromium,
but universal browser support has not been established. Do not claim Firefox or
Safari support from rules/engine tests. If Safari also blocks this transport, evaluate
explicit local TLS with a trusted certificate or the planned extension/native-messaging
adapter before adding it to the supported matrix. Neither fallback is implemented.

## Reproduce

From this checkout, use an environment with `aiohttp` and `playwright` installed.
Install the required test browser with `python -m playwright install firefox` or
`python -m playwright install webkit`; Chromium can use an installed system binary.

```bash
python -m tools.browser_probe --browser chromium --output /tmp/chromium-probe.json
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
