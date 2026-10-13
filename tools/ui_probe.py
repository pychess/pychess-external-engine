"""Fresh-browser native UI check against a Pychess checkout with an in-memory test DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web
from playwright.async_api import ViewportSize, async_playwright

from pychess_external_engine.app import STATE, create_app
from pychess_external_engine.config import EngineConfig
from tools.browser_probe import set_permission

SERVER_CODE = """
import sys, asyncio, json
sys.argv.append('pytest')
from aiohttp import web
from mongomock_motor import AsyncMongoMockClient
from server import make_app
import test_logger
test_logger.init_test_logger()
async def main():
    runner = web.AppRunner(make_app(db_client=AsyncMongoMockClient(tz_aware=True)), access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        print(json.dumps({'port': runner.addresses[0][1]}), flush=True)
        await asyncio.to_thread(sys.stdin.buffer.read, 1)
    finally:
        await runner.cleanup()
asyncio.run(main())
"""


async def check(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.pychess).expanduser().resolve()
    interpreter = root / ".venv" / "bin" / "python"
    if not interpreter.exists():
        raise ValueError("The Pychess checkout needs its development virtualenv and built assets")
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(root / "server"), str(root / "tests"))),
    }
    server = await asyncio.create_subprocess_exec(
        str(interpreter),
        "-c",
        SERVER_CODE,
        cwd=root,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    bridge: web.AppRunner | None = None
    report: dict[str, object] = {
        "platform": platform.platform(),
        "browser": args.browser,
        "variant": args.variant,
        "wasmSearchDisabled": args.without_wasm,
    }
    try:
        assert server.stdout is not None
        async with asyncio.timeout(20):
            line = await server.stdout.readline()
        port = json.loads(line)["port"]
        local_origin = f"http://127.0.0.1:{port}"
        origin = "https://www.pychess.org"
        report["testOrigin"] = origin
        report["siteDelivery"] = "Local checkout served through Playwright HTTPS routing"
        token = secrets.token_urlsafe(32)
        app = create_app(
            EngineConfig(command=(args.engine,), options=tuple(tuple(o) for o in args.setoption)),
            alice_config=EngineConfig(
                command=(args.alice_engine,), options=tuple(tuple(o) for o in args.alice_setoption)
            )
            if args.alice_engine
            else None,
            token=token,
            origins=(origin,),
            authority="",
        )
        bridge = web.AppRunner(app, access_log=None, handler_cancellation=True)
        await bridge.setup()
        await web.TCPSite(bridge, "127.0.0.1", 0).start()
        address = f"http://127.0.0.1:{bridge.addresses[0][1]}"
        app[STATE].authority = address.removeprefix("http://")
        async with async_playwright() as playwright:
            kind = playwright.firefox if args.browser == "firefox" else playwright.chromium
            browser = await kind.launch(
                headless=not args.headed,
                channel="msedge" if args.browser == "edge" else None,
                executable_path=(shutil.which("chromium-browser") or shutil.which("chromium"))
                if args.browser == "chromium"
                else None,
            )
            report["version"] = browser.version
            try:
                context = await browser.new_context()

                async def serve_checkout(route) -> None:
                    response = await route.fetch(
                        url=local_origin + route.request.url.removeprefix(origin)
                    )
                    await route.fulfill(response=response)

                await context.route(origin + "/**", serve_checkout)
                await context.add_init_script("localStorage.setItem('infiniteAnalysis', 'true')")
                if args.without_wasm:
                    await context.route("**/static/stockfish*.js", lambda route: route.abort())
                page = await context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error).splitlines()[0]))
                url = origin + "/analysis/" + args.variant
                if args.fen:
                    url += "?" + urlencode({"fen": args.fen})
                await page.goto(url, wait_until="domcontentloaded")
                cdp = None
                if args.browser in ("chromium", "edge"):
                    cdp = await browser.new_browser_cdp_session()
                    await set_permission(cdp, page, origin, "granted")
                elif args.headed:
                    print("If Firefox asks for local network access, choose Allow.", flush=True)
                await page.wait_for_selector("#bars", state="visible", timeout=15000)
                await page.locator("#bars").click()
                await page.locator("#external-engine-address").fill(address)
                await page.locator("#external-engine-token").fill(token)
                await page.locator("#external-engine-connect").click()
                await page.wait_for_function(
                    "name => document.querySelector('#analysis-engine-name').textContent"
                    ".includes(name) && "
                    "document.querySelector('.nnue').textContent === 'NATIVE' && "
                    "!document.querySelector('#engine-enabled').disabled",
                    arg="Alice-Stockfish" if args.variant == "alice" else "Fairy-Stockfish",
                    timeout=60000 if args.headed else 15000,
                )
                report["pairing"] = "passed"
                await page.locator("#bars").click()
                await page.locator('label[for="engine-enabled"]').click()
                await page.wait_for_function(
                    "document.querySelector('info#info').textContent.includes('Depth') && "
                    "document.querySelector('.pvbox pv-san')",
                    timeout=15000,
                )
                report["nativeEvaluation"] = "passed"
                active = app[STATE].controller.active
                assert active is not None
                report["resolvedEngineVariant"] = active.engine_variant
                report["exactRulesSupplied"] = active.rules is not None
                before = await page.locator("#fullfen").input_value()
                await page.locator(".pvbox pv-san").first.click()
                await page.wait_for_function(
                    "before => document.querySelector('#fullfen').value !== before",
                    arg=before,
                )
                await page.wait_for_function(
                    "document.querySelector('info#info').textContent.includes('Depth')"
                )
                report["pvNavigation"] = "passed"
                await page.locator("#bars").click()
                style_checks = []
                viewports: tuple[ViewportSize, ...] = (
                    {"width": 1280, "height": 900},
                    {"width": 390, "height": 844},
                )
                for viewport in viewports:
                    await page.set_viewport_size(viewport)
                    for theme in ("light", "dark"):
                        await page.evaluate("theme => document.body.dataset.theme = theme", theme)
                        await page.locator("#external-engine-token").focus()
                        data = await page.evaluate("""() => {
                          const root = document.querySelector('.external-engine-settings');
                          const input = document.querySelector('#external-engine-token');
                          const fields = [...root.querySelectorAll('input, select')];
                          const rows = [...root.querySelectorAll('.external-engine-field')];
                          const browser = document.querySelector('.browser-engine-settings');
                          return {
                            fits: fields.every(e => e.getBoundingClientRect().width <=
                              root.getBoundingClientRect().width + 1),
                            stacked: rows.every(e =>
                              getComputedStyle(e).flexDirection === 'column'),
                            browserSettingsHidden: getComputedStyle(browser).display === 'none',
                            inputColor: getComputedStyle(input).color,
                            inputBackground: getComputedStyle(input).backgroundColor,
                            focused: document.activeElement === input
                          };
                        }""")
                        if not all(
                            data[key]
                            for key in ("fits", "stacked", "browserSettingsHidden", "focused")
                        ):
                            raise AssertionError(
                                "External settings failed a rendered layout/focus check"
                            )
                        style_checks.append({"viewport": viewport, "theme": theme, **data})
                report["styles"] = style_checks
                report["stylesheets"] = await page.evaluate(
                    "[...document.styleSheets].filter(s => s.href)"
                    ".map(s => new URL(s.href).pathname)"
                )
                other = await context.new_page()
                await other.goto(url, wait_until="domcontentloaded")
                await other.wait_for_function(
                    "document.querySelector('#external-engine-status')?.textContent"
                    ".includes('Another tab')",
                    timeout=15000,
                )
                report["competingTab"] = "passed"
                await page.close()
                await asyncio.wait_for(app[STATE].idle.wait(), 3)
                report["tabCloseCleanup"] = "passed"
                await other.locator("#bars").click()
                await other.locator("#external-engine-connect").click()
                await other.locator("#bars").click()
                await other.wait_for_function(
                    "document.querySelector('info#info').textContent.includes('Depth')",
                    timeout=15000,
                )
                report["reconnect"] = "passed"
                await other.locator("#bars").click()
                await other.locator("#external-engine-forget").click()
                await other.wait_for_function(
                    "localStorage.getItem('externalEngineConnection') === null && "
                    "document.querySelector('#analysis-engine-source').value === 'browser'"
                )
                await asyncio.wait_for(app[STATE].idle.wait(), 3)
                report["forget"] = "passed"
                if cdp is not None:
                    await set_permission(cdp, other, origin, "denied")
                    await other.locator("#external-engine-token").fill(token)
                    await other.locator("#external-engine-connect").click()
                    await other.wait_for_function(
                        "document.querySelector('#external-engine-status').textContent"
                        ".includes('Unable to connect')"
                    )
                    report["deniedPermission"] = "passed"
                    await cdp.detach()
                else:
                    report["deniedPermission"] = "pending manual verification"
                if page_errors:
                    raise AssertionError("Browser reported a runtime error")
                report["releasedSearches"] = app[STATE].released
                report["result"] = "passed"
            finally:
                await browser.close()
    except Exception as exc:
        report["result"] = "failed"
        report["error"] = type(exc).__name__ + ": " + str(exc).splitlines()[0]
    finally:
        if bridge is not None:
            await bridge.cleanup()
        if server.stdin is not None:
            server.stdin.close()
        try:
            async with asyncio.timeout(5):
                await server.wait()
        except TimeoutError:
            server.kill()
            await server.wait()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pychess", default="../pychess-variants")
    parser.add_argument("--engine", required=True)
    parser.add_argument("--alice-engine")
    parser.add_argument("--alice-setoption", nargs=2, action="append", default=[])
    parser.add_argument(
        "--setoption", nargs=2, action="append", default=[], metavar=("NAME", "VALUE")
    )
    parser.add_argument("--browser", choices=("chromium", "edge", "firefox"), default="edge")
    parser.add_argument("--variant", default="chess")
    parser.add_argument("--fen", help="Start the test at this explicit variant FEN")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--without-wasm", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = asyncio.run(check(args))
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output, end="")
    if report["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
