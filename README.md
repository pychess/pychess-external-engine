# Pychess External Engine

Analyse positions on [Pychess](https://www.pychess.org) with native engines running
on your own computer. Use your own UCI-compatible engine for its supported variants.
The required `pyffish` and `pyffish-alice` libraries validate rules and moves;
they do not choose your analysis engine.

Account-token registration is unreleased and needs the matching Pychess integration.
The published `0.1.0a1` uses the earlier manual connection flow; see its
[release README](https://github.com/pychess/pychess-external-engine/tree/v0.1.0a1).

## Install and run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and obtain your
engine executable. Create an engine token at Pychess **Account → External engines**
and save it to a local file. From this checkout, run:

```bash
uvx --python 3.13 --from . pychess-external-engine \
  --engine /path/to/your-uci-engine --token-file /path/to/your-token
```

Engines and NNUE networks are supplied locally. For Alice, configure a compatible
engine and any network it needs as described in the
[usage guide](https://github.com/pychess/pychess-external-engine/blob/main/docs/installation.md).

## Connect to Pychess

1. Leave the app running after it reports that your engine is registered.
2. Open Pychess analysis engine settings and select your engine by name.
3. Allow local network access if your browser asks, then enable analysis.

The default address is `http://127.0.0.1:9670`. Stop the app with Ctrl+C; restarting
refreshes its registration automatically. Select the browser engine to switch back;
the remove button removes an external registration. Only one tab can use the native
engine at a time. Account tokens stay in the local app; search traffic stays local.

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
