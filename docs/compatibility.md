# Compatibility

The verification basis is release `0.1.0`, the current source's single-engine native/UI
checks, and earlier transport coverage from `0.1.0a1`. Availability requires matching
native engine support, rules-library support and website analysis permission. A handshake,
a variant name or successful replay alone does not establish search support.

## Platforms and browsers

| Component | Verified coverage |
| --- | --- |
| Operating system | Linux x86_64; other OS/architectures remain unverified |
| Python | CPython 3.11–3.14; clean installs tested on 3.11.13, 3.12.9, 3.13.13 and 3.14.1 |
| Chromium 154.0.8037.57 | Native HTTPS-to-loopback transport, permission allow/deny and integrated UI |
| Edge 155.0.4283.45 | Native transport/UI, permission allow/deny, ownership, cleanup and restart |
| Firefox 155.0 | Native transport with manual Allow and integrated UI passed on Linux: pairing/discovery, evaluation, PV navigation, desktop/mobile light/dark layouts, source switching, competing-tab ownership, tab-close cleanup, reconnect and removal. Explicit permission denial remains unverified |
| Linux WebKit 26.6 | HTTP loopback rejected; this does not establish real Safari behaviour |
| Safari | Unverified; no supported fallback implemented |

Firefox 155 shares the verified Linux native integration tier with Chromium and
Edge. The Firefox checks used Fairy-Stockfish 260826 LB; Alice-specific Firefox
UI and permission-denial behavior have not been verified. The local UI probe
serves a checkout through intercepted HTTPS routing, not the production server.

The browser probes preserved normal security, cross-origin isolation and
SharedArrayBuffer. Tested Pychess responses used COOP `same-origin` and COEP
`require-corp`; no header change was needed. Recheck served headers and permissions
when changing transport or browser coverage. Browser permission overrides used by
tests do not replace user consent in the actual UI.

## Engines and variants

| Engine / rules | Verified representative cases |
| --- | --- |
| Fairy-Stockfish 051026 LB / pyffish 0.0.90, NNUE disabled | Chess, Black-to-move scores, Chess960/Embassy castling, Shogi drops/promotions, Kyoto demotion, Janggi pass, Xiangqi/Grand ten-rank moves, Crazyhouse promoted captures/pockets, S-Chess gating, repetition history and terminal mate |
| Same engine with exact custom definitions | Orda, CWDA FIDE/Clobberers resolution, historical Study snapshots and changed inherited rules |
| Alice-Stockfish 1.0 / pyffish-alice 0.0.3 / Legacy Alice_v1.nnue | Standalone engine startup/search, fixed Alice rules, compact layer FEN/history, legal PVs, transfers, castling/promotion and Black-to-move search |

The native checks use representative shallow searches, not exhaustive rule or
engine-strength verification. Other single-board variants can be available when both
backends support them, but are not claimed as verified here. The pinned rules schema
can limit custom features advertised by a newer native engine.

Missing variant, Chess960 or custom-definition options fail before `position`/`go`.
Alice Chess960, Alice custom rules, legacy 16-wide Alice FEN, two-board scheduling,
hidden-information search and native USI/UCCI adapters are unsupported. Practice
retains its browser backend.

## Integration coverage

Native UI checks cover PV navigation, source selection, competing tabs, abort/tab-close
cleanup, reconnect/removal, permission denial and bridge restart/token rotation. The
account-registration flow also verifies owner discovery and the engine
dropdown in Edge, including Alice without WASM search. Exact
rules checks cover CWDA without WASM search and Capablanca's initial Embassy profile.
Study checks switch ordinary/custom chapters and historical versions of one public
variant name, verifying actual board rules, Practice ownership and computer permissions.
Settings were checked at desktop/mobile sizes in light/dark themes. Alice UI uses its
NNUE network with WASM search blocked.

The source's single-engine interface uses the same `--engine` and `--setoption`
arguments for Fairy-Stockfish and Alice-Stockfish. Fresh Edge checks cover native
search, discovery, PV navigation, ownership, permission denial and restart for each
executable against the local Pychess checkout. PyPI `0.1.0` predates this interface.

Plain wheel installs include both rules libraries and were verified across the four
Python versions above. Source installation, public uvx installation, CLI discovery,
pairing and shutdown during active search are also checked. Runtime and browser
checks are repeatable through [development.md](development.md).
