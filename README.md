# Pychess External Engine

Analyse positions on [Pychess](https://www.pychess.org) with native engines running
on your own computer. This local Python app connects your browser to Fairy-Stockfish
and Alice-Stockfish. Both rules libraries are included automatically.

## Install and run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and obtain a
compatible Fairy-Stockfish executable, then run the first alpha:

```bash
uvx --python 3.13 pychess-external-engine@0.1.0a1 \
  --engine /path/to/fairy-stockfish --setoption 'Use NNUE' false
```

Engines and NNUE networks are supplied locally. For Alice, configure Alice-Stockfish
and its network as described in the
[usage guide](https://github.com/pychess/pychess-external-engine/blob/main/docs/installation.md).

## Connect to Pychess

1. Leave the app running and copy its displayed address and pairing token.
2. Open Pychess analysis engine settings, choose the external engine and click Connect.
3. Allow local network access if your browser asks, then enable analysis.

The default address is `http://127.0.0.1:9670`. Stop the app with Ctrl+C; restarting
creates a new token. Disconnect returns to browser analysis; Forget removes the
saved pairing details. Only one tab can use the native engine at a time.

This alpha is verified on Linux x86_64 with Chromium and Edge. Firefox native UI,
Safari and other operating systems remain unverified. Connection controls require
Pychess's external-engine frontend integration.

For configuration, resource limits and troubleshooting, see the
[usage guide](https://github.com/pychess/pychess-external-engine/blob/main/docs/installation.md).
See [compatibility](https://github.com/pychess/pychess-external-engine/blob/main/docs/compatibility.md)
for tested engines and variants.

[PyPI](https://pypi.org/project/pychess-external-engine/) ·
[Issues](https://github.com/pychess/pychess-external-engine/issues) ·
[AGPL-3.0-or-later](https://github.com/pychess/pychess-external-engine/blob/main/LICENSE) ·
[Attribution](https://github.com/pychess/pychess-external-engine/blob/main/NOTICE.md)
