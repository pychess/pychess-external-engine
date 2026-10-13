# Attribution and distribution

This application follows the external-engine design studied in Lichess's
[external-engine](https://github.com/lichess-org/external-engine),
[lila-engine](https://github.com/lichess-org/lila-engine), and
[browser adapter](https://github.com/lichess-org/lila/blob/master/ui/lib/src/ceval/engines/externalEngine.ts).
The Python implementation is written for Pychess's local-only architecture.
Adapted admission guards and the derived rule schema are identified below.

The FEN geometry and material admission guards are adapted from Pychess's
`server/utils.py` and `server/study/engine.py` under the project's AGPL license;
source comments identify those adaptations.

The versioned rule-name schema in `rule_keys.py` is derived from the `parser.cpp`
and `piece.cpp` files in the pyffish 0.0.90 source distribution, Fairy-Stockfish,
Copyright Fabian Fichter and contributors, GPL-3.0-or-later. The native checker
uses a diagnostic copy with XBoard image tables disabled; the search engine loads
the original hashed definition unchanged.

Application source is licensed under AGPL-3.0-or-later; see [LICENSE](LICENSE).
The included license text is the standard GNU AGPL v3 text also used by Pychess.

The rules dependency [pyffish](https://github.com/fairy-stockfish/Fairy-Stockfish)
and the `pyffish-alice` rules library are provided by Fairy-Stockfish
under their own GPL licenses. Native engine executables
and networks are configured by the user and are not bundled with this package.
Preserve notices and fulfill upstream source/license obligations when adapting code
or distributing dependencies or engine binaries.
