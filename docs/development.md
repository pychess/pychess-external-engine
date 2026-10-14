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

For shared lifecycle/protocol/rules changes:

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
HTTPS routing and contacts the real local bridge. It creates a temporary signed-in
account and engine-scoped token, then registers the bridge through the real website
API. No account token or loopback credential is included in reports. Unrelated header
SSE is stubbed because routed Playwright responses buffer whole bodies; native
NDJSON goes directly to loopback without interception. It does not write to production.
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

### Automated release

[Python checks](../.github/workflows/ci.yml) runs on branch pushes, pull requests and
manual runs, and can be called by another workflow. It checks formatting, lint and
types, builds a wheel and source archive once, validates versions/metadata/contents,
and tests fresh installs of both artifacts on Python 3.11–3.14. Each installation
runs the full unit suite,
imports both rules libraries and exercises installed CLI registration and shutdown
during a search using the fake UCI fixture. Native tests skip without configured
executables; receipts report these skips and the fixture engine explicitly.
Native-engine and browser compatibility remain separate, explicitly configured checks.

[PyPI release](../.github/workflows/release.yml) runs on `v*` tag pushes and calls
the same CI workflow at the tagged commit. The CI file contains only validation;
the release file owns the publishing job and its PyPI permissions. Tagged commits
run validation once through the release workflow, using the same build/test definitions
as ordinary CI. Artifacts remain in the same workflow run, so publishing downloads
the exact distributions checked by that run.

Only a tag push in this repository can publish. The tag must match `pyproject.toml`,
the runtime version and `uv.lock`; its commit must already be on `main`. The publish
job waits for all checks, verifies SHA-256 hashes and uploads the tested distributions
through Trusted Publishing without rebuilding. Branches and pull requests run CI only;
manual runs of either workflow validate without publishing. Installation receipts
are retained as workflow artifacts.
Use those results for release validation rather than repeating green CI locally.

One-time configuration:

1. Create a GitHub environment named `pypi`, allowing deployment only from `v*` tags.
2. In the project's [PyPI Publishing settings](https://pypi.org/manage/project/pychess-external-engine/settings/publishing/),
   add a GitHub Trusted Publisher with owner `pychess`, repository
   `pychess-external-engine`, workflow filename `release.yml`, and environment `pypi`.
   Replace any earlier publisher configured for `ci.yml`; only `release.yml` publishes.

No PyPI API token is needed in GitHub secrets. See the
[PyPI Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

For each release, update the version in `pyproject.toml` and
`src/pychess_external_engine/__init__.py`, run `uv lock`, update installation examples
and compatibility claims as appropriate, and merge into `main`. After CI passes,
create and push the matching annotated tag, for example for a future `0.1.1`:

```bash
git tag -a v0.1.1 -m 'Release 0.1.1'
git push origin refs/tags/v0.1.1
```

Watch the tag's workflow to confirm publication. Published versions and tags are
immutable; do not move a tag or reuse a version. A failed validation can be rerun for
the same unchanged tag. If upload partially succeeds, verify the published file hashes
before recovering; the workflow fails on existing files rather than silently skipping
them. Do not create or move release tags for workflow testing: a branch push or manual
run exercises validation without publishing.

### Local packaging diagnostics

Use these commands when developing release tooling or diagnosing a workflow failure.
Build into a fresh temporary directory without project-specific dependency sources:

```bash
release_dist=$(mktemp -d)
uv build --no-sources --out-dir "$release_dist"
uv run python -m tools.release_check --dist "$release_dist"
uvx twine check --strict "$release_dist"/*.whl "$release_dist"/*.tar.gz
```

Replace `<version>` with the actual package version. To repeat an installation check
with explicitly configured native executables:

```bash
PYCHESS_TEST_ENGINE=/path/to/fairy-stockfish \
PYCHESS_TEST_ALICE_ENGINE=/path/to/alice-stockfish \
PYCHESS_TEST_ALICE_NETWORK=/path/to/Alice_v1.nnue \
  uv run python -m tools.install_probe \
    --artifact "$release_dist/pychess_external_engine-<version>-py3-none-any.whl"
```

The probe verifies installed-code imports of both rules libraries, tests and the
installed CLI's registration/active-search shutdown on Python 3.11–3.14. Repeated
`--python` selects interpreters. Use a source archive to verify a source installation.
Without a configured executable, CLI lifecycle checks use the fake fixture and
native search tests skip; this does not verify native search compatibility.

The distribution checker verifies license/notice, required rules dependencies, CLI
entry point, runtime source and source docs/tests/tools against the checkout. It
rejects stale or unexpected files and writes `SHA256SUMS`. Engines/networks are not
bundled. The source archive includes AGENTS.md and the lockfile; development tools
are not installed in the application wheel.

Documentation-only changes need Markdown rendering, link checks and diff review.
README or manifest changes also need a temporary build and metadata/content checks;
use `uv build --no-sources --out-dir /tmp/<build-directory>` to preserve release artifacts.
