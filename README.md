# Pychess External Engine

Local native-engine analysis for the Pychess website.

## Status

Planning workspace created on 2026-10-13. The application is not implemented yet;
there are no installation or runnable CLI commands at this stage.

The design and implementation checklist are maintained in
[Pychess's external-engine plan](../pychess-variants/docs/external-engine.md)
in the sibling checkout. Its repository path is `docs/external-engine.md` in
[pychess-variants](https://github.com/pychess/pychess-variants).

## Design

One Python application runs on the user's computer and combines a local HTTP bridge,
UCI subprocess management, and `pyffish` rules validation. The Pychess analysis page
connects directly to the bridge through a paired loopback connection and displays
streamed evaluations from the user's native engine.

No additional Heroku process or public broker is required. Engine executables,
networks, resource limits, and pairing are configured locally. Native engines perform
the search; `pyffish` validates positions, histories, and principal variations.

The first implementation step is to prove HTTPS-to-loopback streaming and cancellation
in supported browsers. Ordinary single-board Fairy-Stockfish analysis comes first,
with exact custom rules included in the protocol design. Alice, two-board analysis,
hidden-information variants, and USI/UCCI adapters need separate capability checks.

## References

- [Lichess external-engine](https://github.com/lichess-org/external-engine)
- [Lichess lila-engine](https://github.com/lichess-org/lila-engine)
- [Fairy-Stockfish variant standards](https://fairy-stockfish.github.io/chess-variant-standards/)

This workspace currently contains documentation only. Preserve upstream licenses and
attribution when implementation code is adapted from these projects.
