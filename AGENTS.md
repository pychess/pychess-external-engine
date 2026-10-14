# AGENTS.md

## Project

Pychess External Engine is a local Python/aiohttp bridge between Pychess browser
analysis and native UCI engines. Python supports 3.11–3.14. Both `pyffish` and
`pyffish-alice` are required dependencies; engine executables and networks are
configured locally. Packaging metadata and dependency pins live in `pyproject.toml`.

Use the repository rather than conversation history as the source of truth:

- `README.md`: minimal end-user installation and pairing.
- `docs/installation.md`: configuration and troubleshooting.
- `docs/architecture.md`: ownership, rules isolation and frontend integration.
- `docs/protocol.md`: HTTP contract and score/move conventions.
- `docs/compatibility.md`: verified coverage and limitations.
- `docs/development.md`: setup, tests, browser probes and packaging.
- `docs/roadmap.md`: future enhancements.

Inspect `src/pychess_external_engine/`, `tests/` and `tools/` for implementation
ownership. `tools/` contains development probes, not installed application commands.
The sibling `pychess-variants` repository owns the website frontend and its own
AGENTS.md/testing policy.

## Design constraints

- Keep the bridge and engines on the user's computer. No public broker or extra
  hosted Pychess process is needed.
- Bind to IPv4 loopback, validate exact Host/Origin, require pairing for engine
  operations and preserve browser local-network permissions. Keep tokens out of
  URLs, reports and PGNs. Account API tokens stay in local configuration and are
  sent only to the configured website's registration API. The separate loopback
  credential is stored in an expiring, owner-only website registration and page
  memory; never expose it in public data or browser preferences.
- Executables, arguments, network paths and engine options come from local
  configuration. Browser requests cannot choose commands or local files.
  Users choose and supply their own compatible UCI engines; do not require,
  bundle or download Fairy-Stockfish or Alice-Stockfish as analysis executables.
- One search owns the bridge across all variants and tabs. Stop/drain through
  bestmove before replacement; bound time/output, reap subprocesses on failure,
  and retain request identity checks through asynchronous cleanup.
- Isolate native rules in a worker per search. Preserve the initial FEN and full
  history, exact UTF-8 rules hashes, inherited definitions and legal PV prefixes.
  Changing rule hashes or native adapters must not reuse stale registrations.
- Scores are side-to-move relative. Preserve extended coordinates, drops,
  promotions/demotions, gating and Alice layer markers without chess-only parsing.
- Discover actual engine/rules capabilities. Alice rules are always installed;
  Alice search requires its native engine/network. Do not infer Chess960, custom
  Alice rules, hidden-information, two-board or Practice support from a variant name.
- Preserve website ongoing-game, anti-cheat, Study visibility/computer-permission
  and Practice ownership restrictions when changing integration.

## Verification

Set up with `uv sync --extra dev`. After Python changes run:

```bash
uv run ruff format --target-version py313 .
uv run ruff check .
uv run pyrefly check --min-severity warn
```

Run targeted unittest modules by default. Use full discovery for shared lifecycle,
protocol and rules changes. CI runs full discovery against installed wheel/source
artifacts on Python 3.11–3.14. For releases, use the successful tag workflow's checks
and installation receipts; do not repeat successful CI checks locally unless changes,
failures or missing coverage justify it. Native tests require explicitly configured
executables; see `docs/development.md`. Never describe skipped native tests or a
successful UCI handshake as verified search support. Browser changes need relevant
probes using fresh profiles and normal browser security.

For documentation-only edits, render Markdown, check links and review the diff;
unit tests are unnecessary. README/MANIFEST changes also need a temporary package
build, metadata checks and archive-content inspection.

## Documentation and releases

Keep durable design, supported behaviour and repeatable commands in docs. Update
compatibility when testing changes its coverage. Keep milestone logs, raw probe
transcripts and temporary credentials out of maintained documentation; historical
material is available in git and release tags.

Preserve LICENSE/NOTICE attribution. When publishing is requested, use a new package
version for changed artifacts and publish through `.github/workflows/release.yml` by
pushing its matching `v<version>` tag from a commit already merged into `main`.
The release workflow calls the reusable CI workflow to validate and test the exact
distributions, then publishes with PyPI Trusted Publishing. Follow
`docs/development.md` for one-time configuration and local diagnostics; do not
manually upload or create a release tag just to test CI.
Keep publishing credentials outside the repository. Published versions and tags
are immutable.
