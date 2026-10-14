# Architecture

Pychess External Engine combines loopback HTTP, rules validation and UCI subprocess
management in one application on the user's computer. The native engine searches;
`pyffish` and `pyffish-alice` validate positions, histories and principal variations.
Pychess serves the frontend through its existing application. Evaluations stream
directly to the browser, requiring no extra hosted server. An engine-scoped account
token registers connection metadata with the existing Pychess website.

Each run configures one user-supplied UCI executable and one set of local options.
Discovery determines its advertised variants; the bridge selects the matching rules
library internally. Executable names do not select an engine profile.

```text
Pychess website -> browser analysis controller
                       |
                  paired loopback HTTP / NDJSON
                       |
                  local Python app
                    |          |
             isolated rules   native UCI engine
                 worker         subprocess
```

See [protocol.md](protocol.md) for the HTTP contract and
[development.md](development.md) for repeatable checks.

## Application boundaries

| Module | Responsibility |
| --- | --- |
| `cli.py`, `config.py` | Local executable/options, resource ceilings and startup |
| `access.py`, `app.py` | Host/Origin admission, pairing, capabilities and disconnect-aware streaming |
| `search.py` | One-search ownership, output parsing, correlation and bounded cleanup |
| `uci.py`, `native_rules.py` | Discovery, UCI I/O, exact rules loading and native acceptance checks |
| `rules.py`, `rules_worker.py`, `rule_keys.py` | Bounded IPC, isolated native validation and the pinned rule-name schema |
| `protocol.py` | Strict request schema, size bounds and event identity |

The browser supplies position/history, exact rule definitions and bounded resource
requests. Only local configuration chooses executable arguments, files and networks.
Both HTTP routes require a local bearer credential and an allowed origin. That
credential rotates per app start and is published only to the owner's account
registration. The account API token is separate and never sent to browser analysis;
the account creation page shows it once so the user can configure the local app.

`registration.py` sends engine capabilities, loopback address and the local credential
after the HTTP port is bound, then renews the registration every 30 seconds. Pychess
expires registrations after 90 seconds without renewal. Revocation/removal/replacement
stops renewal and closes the local bridge and its subprocesses. Ordinary shutdown
removes only that process's registration; an old process cannot remove its replacement.
The website receives no analysis requests, positions, history, custom rules or PVs
through this registration API.

## Search ownership and cleanup

One owner covers all tabs and variants. Competing requests receive `engine_busy`;
there is no queue. The browser aborts on navigation, source changes and destruction,
and ignores late events by request identity even if their first move is still legal.
A tab may briefly retry its own cleanup; it cannot replace another tab's search.

Before replacement, the controller stops and drains through `bestmove`, then waits
for readiness. Hung/crashed engines are killed and reaped. Disconnect polling releases
work even when no engine output is arriving. Handshake, stop, search and output limits
bound the lifetime of faulty subprocesses. Idle shutdown closes the engine while
leaving the bridge available to launch another search.

## Exact rules and native isolation

Each search gets a fresh rules process, preventing accumulated variant registrations
and containing native crashes. Admission checks geometry/material before native calls,
replays every history move legally and compares canonical FENs. Root/history preserve
repetition, castling, pockets and promotion state; optional draw claims do not discard
that history. Returned PVs contain only a legal prefix.

Custom definitions include their inheritance chain and UTF-8 SHA-256. The pinned
schema rejects unknown keys that the native parser might silently ignore. Native
acceptance is checked before loading. Its diagnostic copy suppresses XBoard image-table
checks; search still loads the original hashed bytes unchanged. Changing the hash
restarts the engine; exact page rules take precedence over conflicting local VariantPath
registrations. Historical Study names are resolved to immutable frontend aliases.

## Alice and other engines

Every install includes both rules libraries. Alice requests use a dedicated rules
worker with the same single locally configured UCI executable. Startup discovers
advertised variants and qualifies an advertised Alice capability with a legal
shallow search. Compact Alice FEN keeps `|` before looking-glass-board pieces; moves
retain ordinary coordinate tokens. Variant restrictions limit Alice to fixed rules
without Chess960 or custom definitions.

Each run owns one executable configuration and its options. Rules workers are selected
internally for each search; no separate Alice executable or options are configured.
An engine's sole advertised default variant is not redundantly set, avoiding
single-value combo-option setter failures. A compatible Alice NNUE network
is necessary for meaningful evaluation; NNUE-disabled Alice scores are diagnostic zero.

Chess-only UCI engines default to Chess when no variant option exists. Missing optional
resource options are not sent. Native USI/UCCI need separate adapters.

## Website integration

The sibling [Pychess repository](https://github.com/pychess/pychess-variants) owns the
search backend interface, account engine discovery, engine selector and score/PV display.
External capability is separate from WASM search, rules replay and Fishnet. The
frontend preserves ongoing-game, anti-cheat, Study permissions and Practice ownership;
transient external results do not become saved server analysis.
