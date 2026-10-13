# Development

## Setup and checks

```bash
uv sync --extra dev
uv run ruff format --target-version py313 .
uv run ruff check .
uv run pyrefly check --min-severity warn
```

Run a targeted unittest module for a local change, for example:

```bash
uv run python -m unittest discover -s tests -p test_rules.py -v
```

For shared lifecycle/protocol/rules changes and release validation:

```bash
uv run python -m unittest discover -s tests -v
```

Tests use controlled fake UCI subprocesses for races, delayed output, hangs, crashes
and output bounds. Rules tests use the required native libraries. Tests requiring
actual search executables skip until the corresponding environment variables are set:

```bash
PYCHESS_TEST_ENGINE=/path/to/fairy-stockfish \
PYCHESS_TEST_ALICE_ENGINE=/path/to/alice-stockfish \
PYCHESS_TEST_ALICE_NETWORK=/path/to/Alice_v1.nnue \
  uv run python -m unittest discover -s tests -v
```

Ordinary native tests disable NNUE. Alice tests use the supplied network; without
`PYCHESS_TEST_ALICE_NETWORK` they use zero diagnostic evaluation. Check skipped tests
before reporting native coverage. Consult [architecture.md](architecture.md) and
[protocol.md](protocol.md) before changing ownership, rules or move/score handling.

## Native compatibility

```bash
uv run python -m tools.compatibility_probe --engine /path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --output /tmp/native-compatibility.json
```

This uses the production controller and isolated rules worker at depth 2/MultiPV 2,
retains complete history and verifies every PV token. Update
[compatibility.md](compatibility.md) when completed checks change supported coverage.

## Browser checks

Probes use temporary apps and fresh browser profiles. Install Playwright's browser
where needed, for example `uv run python -m playwright install firefox`. Chromium can
use a system binary; Edge uses its installed channel. Keep normal browser security
and permissions enabled. Reports omit pairing tokens and should be saved outside
maintained docs, such as `/tmp`, or attached to the relevant review/release.

For native streaming from the deployed HTTPS analysis page:

```bash
uv run python -m tools.browser_probe --browser edge \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false
```

For the Firefox manual Allow transport check:

```bash
uv run python -m tools.browser_probe --browser firefox --headed --manual-permission \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false
```

That probe waits for the user to allow access. Do not claim denial or native UI coverage
from an Allow-only transport result. The fake transport helper remains useful for
isolating HTTP/browser issues; its events are marked `prototype: true` and are never
real evaluations.

Build the sibling Pychess frontend with `yarn dev` and prepare its development virtualenv,
including the Mongo mock dependencies. Verify the UI with:

```bash
uv run python -m tools.ui_probe --browser edge --pychess ../pychess-variants \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false
```

The UI probe serves a local checkout with an in-memory database through synthetic
HTTPS routing and contacts the real local bridge. It does not write to production.
Useful cases include `--variant orda`, `--variant cwda --without-wasm`, explicit
`--variant capablanca --fen '...'`, and `--restart-bridge`. Run
`--variant alice --expect-unsupported` without an Alice engine for capability rejection.
For Alice UI:

```bash
uv run python -m tools.ui_probe --browser edge --variant alice --without-wasm \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false \
  --alice-engine /path/to/alice-stockfish \
  --alice-setoption EvalFile /path/to/Alice_v1.nnue
```

Study transitions, historical definitions, Practice and computer permissions use real
local Study websockets and a temporary database:

```bash
uv run python -m tools.study_probe --browser edge --pychess ../pychess-variants \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false
```

Website changes must also follow that repository's AGENTS.md and quality gates.

## Packaging and releases

Build without project-specific dependency sources and validate metadata:

```bash
uv build --no-sources
uvx twine check --strict dist/pychess_external_engine-<version>-py3-none-any.whl \
  dist/pychess_external_engine-<version>.tar.gz
```

Replace `<version>` with the actual package version. Before a requested publication,
run clean installation checks on the exact built artifacts:

```bash
PYCHESS_TEST_ENGINE=/path/to/fairy-stockfish \
PYCHESS_TEST_ALICE_ENGINE=/path/to/alice-stockfish \
PYCHESS_TEST_ALICE_NETWORK=/path/to/Alice_v1.nnue \
  uv run python -m tools.install_probe \
    --artifact dist/pychess_external_engine-<version>-py3-none-any.whl
```

The probe verifies installed-code imports of both rules libraries, tests and the
installed CLI's pairing/active-search shutdown on Python 3.11–3.14. Repeated `--python`
selects interpreters. Use a source archive to verify a source installation.

Inspect wheel/source contents for license/notice, required dependencies and expected
source docs/tests/tools. Engines/networks are not bundled. The source archive includes
AGENTS.md and the lockfile; development tools are not installed in the application
wheel. Upload only the exact verified distributions. Never overwrite a published
version or tag, and keep credentials outside source and reports.

Documentation-only changes need Markdown rendering, link checks and diff review.
README or manifest changes also need a temporary build and metadata/content checks;
use `uv build --no-sources --out-dir /tmp/<build-directory>` to preserve release artifacts.
