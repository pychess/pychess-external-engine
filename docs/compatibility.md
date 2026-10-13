# Verified engine and variant coverage

Compatibility is the intersection of native engine support, the pinned rules library,
and the page's analysis permissions. A UCI handshake or a variant name alone is
insufficient. Custom definitions are validated and loaded before rediscovery; an
unsupported rule key or engine option causes rejection before search.

## Engine receipts

| Engine / rules backend | Verified cases | Evidence |
| --- | --- | --- |
| Fairy-Stockfish 051026 LB / pyffish 0.0.90, NNUE disabled | Chess including Black-to-move scores, Chess960 castling, Embassy castling, Shogi drops/promotions, Kyoto demotion, Janggi pass, Xiangqi/Grand ten-rank moves, Crazyhouse promoted captures and pocket state, S-Chess gating, full repetition history, terminal mate | [14-case native report](results/native-variants-linux-2026-10-13.json) |
| Same ordinary engine with exact supplied rules | Orda, CWDA resolved FIDE/Clobberers profile, historical custom snapshots and ancestor changes | [Orda UI](results/edge-ui-orda-linux-2026-10-13.json), [CWDA UI](results/chromium-ui-cwda-linux-2026-10-13.json), [Study transitions](results/edge-study-linux-2026-10-13.json), native-rules tests |
| Alice-Stockfish 1.0 / pyffish-alice 0.0.3 / Legacy Alice_v1.nnue | Fixed Alice rules, compact looking-glass FEN, complete history and legal PVs; transfer, castling, promotion, Black-to-move native search, ordinary/Alice switching | [Alice UI](results/edge-ui-alice-linux-2026-10-13.json), Alice rules/native HTTP tests |

These are representative shallow compatibility checks, not exhaustive variant-rule
verification or engine-strength measurements. Other single-board variants may be
available when both engine and rules library support them, but are not claimed as
verified here. The frontend also checks exact definitions for site/custom variants.

Alice Chess960, Alice custom rules, two-board scheduling, hidden-information search
and native USI/UCCI are unsupported. Practice continues to use its browser backend.
A Chess-only UCI engine can analyse Chess; it cannot acquire other variants by name
alone. Tests also verify missing variant, Chess960 and custom-rule options reject
before sending a position or starting search.

## Reproduce

```bash
uv run python -m tools.compatibility_probe --engine /path/to/fairy-stockfish \
  --setoption 'Use NNUE' false --output /tmp/native-compatibility.json
```

The probe uses the production controller and isolated rules worker, keeps the initial
FEN and complete move history, searches to depth 2 with MultiPV 2, and verifies every
returned PV token. It closes native and rules subprocesses when finished. The pinned
rules backend distinguishes optional repetition claims from immediate terminal
positions; claimable positions remain searchable with their full history.

Browser/OS evidence is recorded separately in [browser-compatibility.md](browser-compatibility.md).

## Clean installation before the dependency change

On Linux x86_64, isolated wheel installs with the Alice extra passed all 73 tests on
CPython 3.11.13, 3.12.9, 3.13.13 and 3.14.1. Each check confirms imports come from
installed site-packages, runs CLI help, starts the installed CLI, pairs to its HTTP
API, begins native search, and verifies Ctrl+C releases its native/rules subprocesses.
See [the wheel receipt](results/install-wheel-linux-2026-10-13.json).

A clean source-archive install also passes on Python 3.13.13 without the Alice extra:
73 tests collected, the six optional Alice checks skipped; ordinary native checks and
CLI pairing/shutdown pass. See [the source receipt](results/install-sdist-linux-2026-10-13.json).
The artifact hashes identify those tested builds. Final documentation can be added
without changing their runtime modules; [artifact inspection](results/artifact-inspection-linux-2026-10-13.json)
records that comparison and the final wheel/source hashes.

The tested interpreter range is CPython 3.11–3.14. Other operating systems,
architectures and interpreter versions remain unverified. The Linux CI matrix checks these four versions.

See [installation.md](installation.md) for installation, pairing, resources, networks,
custom rules, Alice, permissions and troubleshooting. Publication and deployment
are separate actions.

## Required Alice dependency

The first PyPI release requires `pyffish-alice==0.0.3` alongside `pyffish==0.0.90`.
Default wheel/source installs and uvx runs include both rules backends. A separate
Alice executable/network remains local configuration for Alice search. The earlier
receipts above identify preview builds with an Alice extra; they remain historical
evidence. Release receipts check plain package installation without any Alice extra.
