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
Clean-install and release checks remain open.
