# Attribution and distribution

This application follows the external-engine design studied in Lichess's
[external-engine](https://github.com/lichess-org/external-engine),
[lila-engine](https://github.com/lichess-org/lila-engine), and
[browser adapter](https://github.com/lichess-org/lila/blob/master/ui/lib/src/ceval/engines/externalEngine.ts).
The Python implementation is written for Pychess's local-only architecture.
No upstream implementation files have been copied at this stage.

Application source is licensed under AGPL-3.0-or-later; see [LICENSE](LICENSE).
The included license text is the standard GNU AGPL v3 text also used by Pychess.

The rules dependency [pyffish](https://github.com/fairy-stockfish/Fairy-Stockfish)
is provided by Fairy-Stockfish under its own GPL license. Native engine executables
and networks are configured by the user and are not bundled with this package.
Preserve notices and fulfill upstream source/license obligations when adapting code
or distributing dependencies or engine binaries.
