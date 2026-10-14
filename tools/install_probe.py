"""Install built artifacts in disposable environments and run the package's tests."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import signal
import socket
import sys
import tempfile
from pathlib import Path

from aiohttp import ClientSession, web


async def command(*argv: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    output, _ = await process.communicate()
    text = output.decode(errors="replace")
    if process.returncode:
        raise RuntimeError(f"{argv[0]} exited {process.returncode}:\n{text}")
    return text


async def cli_check(executable: Path, directory: Path) -> str:
    engine = os.environ.get("PYCHESS_TEST_ENGINE")
    engine_args = (
        ["--engine", engine, "--setoption", "Use NNUE", "false"]
        if engine
        else [
            "--engine",
            str(executable.parent / "python"),
            "--engine-arg",
            str(Path(__file__).resolve().parents[1] / "tests/fake_engine.py"),
            "--engine-arg=--mode",
            "--engine-arg=wait",
        ]
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    metadata: dict[str, str] = {}

    async def register(request: web.Request) -> web.Response:
        body = await request.json()
        metadata["token"] = body["clientSecret"]
        return web.json_response({"id": "install1"})

    async def stop(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    website = web.Application()
    website.router.add_post("/api/external-engine", register)
    website.router.add_post("/api/external-engine/install1/stop", stop)
    runner = web.AppRunner(website, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    site = f"http://127.0.0.1:{runner.addresses[0][1]}"
    process = await asyncio.create_subprocess_exec(
        str(executable),
        *engine_args,
        "--port",
        str(port),
        "--allow-origin",
        "http://localhost:9999",
        "--site",
        site,
        cwd=directory,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYCHESS_API_TOKEN": "test_" + "x" * 40},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert process.stdout is not None
        async with asyncio.timeout(15):
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise AssertionError("Installed CLI exited before pairing")
                if line.startswith(b"Registered "):
                    break
        token = metadata["token"]
        headers = {"Origin": "http://localhost:9999", "Authorization": "Bearer " + token}
        address = f"http://127.0.0.1:{port}"
        async with ClientSession() as client:
            async with client.get(address + "/v1/capabilities", headers=headers) as response:
                if response.status != 200 or (await response.json())["protocolVersion"] != 1:
                    raise AssertionError("Installed CLI failed paired capabilities")
            fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            data = {
                "protocolVersion": 1,
                "sessionId": "install",
                "searchId": "shutdown",
                "variant": "chess",
                "engineVariant": "chess",
                "chess960": False,
                "initialFen": fen,
                "currentFen": fen,
                "moves": [],
                "threads": 1,
                "hash": 16,
                "multiPv": 1,
                "search": {"depth": 128},
            }
            async with client.post(address + "/v1/analyse", headers=headers, json=data) as response:
                if response.status != 200:
                    raise AssertionError("Installed CLI rejected native search")
                event = json.loads(await response.content.readline())
                if event["type"] != "started":
                    raise AssertionError("Installed CLI did not start search")
                children = Path(f"/proc/{process.pid}/task/{process.pid}/children")
                pids = children.read_text().split() if children.exists() else []
                process.send_signal(signal.SIGINT)
                async with asyncio.timeout(12):
                    await process.wait()
                if process.returncode not in (0, 130):
                    raise AssertionError("Installed CLI shutdown failed")
                if any(Path(f"/proc/{pid}").exists() for pid in pids):
                    raise AssertionError("Installed CLI retained native subprocesses")
        return "passed"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        await runner.cleanup()


async def check(artifact: Path, versions: list[str]) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    receipt: dict[str, object] = {
        "platform": platform.platform(),
        "artifact": artifact.name,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "aliceDependency": True,
        "checks": [],
    }
    checks = []
    with tempfile.TemporaryDirectory(prefix="pychess-install-") as name:
        directory = Path(name)
        for version in versions:
            print(f"Checking {artifact.name} on Python {version}", file=sys.stderr, flush=True)
            environment = directory / version
            await command("uv", "venv", "--python", version, str(environment), cwd=directory)
            python = environment / "bin/python"
            await command(
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                str(artifact),
                cwd=directory,
            )
            metadata = json.loads(
                await command(
                    str(python),
                    "-c",
                    "import json,platform,importlib.metadata; "
                    "import pychess_external_engine,pyffish,pyffish_alice; "
                    "print(json.dumps(dict(python=platform.python_version(),"
                    "package=importlib.metadata.version('pychess-external-engine'),"
                    "rulesVersion=list(pyffish.version()),aliceRulesVersion=list(pyffish_alice.version()),"
                    "file=pychess_external_engine.__file__)))",
                    cwd=directory,
                )
            )
            if "site-packages" not in metadata.pop("file"):
                raise AssertionError("Tests would use checkout source rather than installed code")
            await command(str(environment / "bin/pychess-external-engine"), "--help", cwd=directory)
            # Only tools/test helpers are on this path. Package source lives under src/,
            # so production imports and worker subprocesses still use the installed wheel.
            env = {**os.environ, "PYTHONPATH": str(root)}
            output = await command(
                str(python),
                "-m",
                "unittest",
                "discover",
                "-s",
                str(root / "tests"),
                cwd=directory,
                env=env,
            )
            match = re.search(r"Ran (\d+) tests in ([\d.]+)s", output)
            if match is None or not re.search(r"^OK(?: \(.*\))?$", output, re.MULTILINE):
                raise AssertionError("Missing successful test summary")
            cli = await cli_check(environment / "bin/pychess-external-engine", directory)
            skipped = re.search(r"OK \(skipped=(\d+)\)", output)
            checks.append(
                {
                    **metadata,
                    "tests": int(match[1]),
                    "skipped": int(skipped[1]) if skipped is not None else 0,
                    "testSeconds": float(match[2]),
                    "installedCode": True,
                    "cliHelp": "passed",
                    "cliStartupPairingShutdown": cli,
                    "cliEngine": "native"
                    if os.environ.get("PYCHESS_TEST_ENGINE")
                    else "fake UCI fixture",
                    "result": "passed",
                }
            )
    receipt["checks"] = checks
    receipt["result"] = "passed"
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--python", action="append", dest="versions")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = asyncio.run(
        check(args.artifact.resolve(), args.versions or ["3.11", "3.12", "3.13", "3.14"])
    )
    text = json.dumps(receipt, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
