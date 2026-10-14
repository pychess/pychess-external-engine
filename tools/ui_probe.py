"""Fresh-browser native UI check against a Pychess checkout with an in-memory test DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from aiohttp import web
from playwright.async_api import ViewportSize, async_playwright

from pychess_external_engine.app import STATE, create_app
from pychess_external_engine.config import EngineConfig
from pychess_external_engine.registration import Registration
from tools.browser_probe import set_permission

SERVER_CODE = """
import sys, asyncio, json
sys.argv.append('pytest')
from aiohttp import web
from mongomock_motor import AsyncMongoMockClient
from server import make_app
from bot_accounts import create_bot_token, ENGINE_TOKEN_SCOPES
from pychess_global_app_state_utils import get_app_state
from user import User
import test_logger
test_logger.init_test_logger()
async def main():
    app = make_app(db_client=AsyncMongoMockClient(tz_aware=True), simple_cookie_storage=True)
    runner = web.AppRunner(app, access_log=None)
    try:
        await runner.setup()
        state = get_app_state(app)
        owner = 'engine-probe'
        state.users[owner] = User(state, username=owner)
        state.users[owner].game_category_set = True
        await state.db.user.insert_one({'_id': owner, 'username_lower': owner, 'enabled': True,
            'ct': 'all'})
        _, api_token = await create_bot_token(state, owner, 'Probe', scopes=ENGINE_TOKEN_SCOPES)
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        seed = {'port': runner.addresses[0][1], 'owner': owner, 'apiToken': api_token}
        print(json.dumps(seed), flush=True)
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
    registration: Registration | None = None
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
        seed = json.loads(line)
        port = seed["port"]
        local_origin = f"http://127.0.0.1:{port}"
        origin = "https://www.pychess.org"
        report["testOrigin"] = origin
        report["siteDelivery"] = "Local checkout served through Playwright HTTPS routing"
        token = secrets.token_urlsafe(32)
        app = create_app(
            EngineConfig(command=(args.engine,), options=tuple(tuple(o) for o in args.setoption)),
            token=token,
            origins=(origin,),
            authority="",
        )
        bridge = web.AppRunner(
            app, access_log=None, handler_cancellation=True, shutdown_timeout=0.2
        )
        await bridge.setup()
        await web.TCPSite(bridge, "127.0.0.1", 0).start()
        address = f"http://127.0.0.1:{bridge.addresses[0][1]}"
        app[STATE].authority = address.removeprefix("http://")
        registration = Registration(
            local_origin, seed["apiToken"], address, app[STATE].capabilities["engine"], token
        )
        await registration.start()
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
            page = None
            try:
                context = await browser.new_context()
                await context.add_cookies(
                    [
                        {
                            "name": "AIOHTTP_SESSION",
                            "url": origin,
                            "value": json.dumps(
                                {
                                    "session": {"user_name": seed["owner"]},
                                    "created": int(time.time()),
                                }
                            ),
                        }
                    ]
                )

                async def serve_checkout(route) -> None:
                    # Playwright's route.fetch buffers responses; unrelated header
                    # SSE would never finish. Native NDJSON uses real loopback HTTP.
                    if route.request.headers.get("accept") == "text/event-stream":
                        await route.fulfill(
                            status=200, content_type="text/event-stream", body=": probe\n\n"
                        )
                        return
                    response = await route.fetch(
                        url=local_origin + route.request.url.removeprefix(origin),
                        max_redirects=0,
                        headers={
                            **route.request.headers,
                            "Host": urlsplit(origin).netloc,
                            "X-Forwarded-Proto": "https",
                        },
                    )
                    await route.fulfill(response=response)

                await context.route(origin + "/**", serve_checkout)
                await context.add_init_script("localStorage.setItem('infiniteAnalysis', 'true')")
                if args.without_wasm:
                    await context.route("**/static/stockfish*.js", lambda route: route.abort())
                # Use an actual website origin for cookie/redirect semantics;
                # synthetic HTTPS routing is reserved for the native transport UI.
                account_context = await browser.new_context()
                try:
                    await account_context.add_cookies(
                        [
                            {
                                "name": "AIOHTTP_SESSION",
                                "url": local_origin,
                                "value": json.dumps(
                                    {
                                        "session": {"user_name": seed["owner"]},
                                        "created": int(time.time()),
                                    }
                                ),
                            }
                        ]
                    )
                    account_page = await account_context.new_page()
                    await account_page.goto(
                        local_origin + "/account/engines", wait_until="domcontentloaded"
                    )
                    await account_page.locator("#engine-token-description").fill("UI setup check")
                    await account_page.get_by_role(
                        "button", name="Create token", exact=True
                    ).click()
                    await account_page.wait_for_selector("#new-engine-token")
                    if len(await account_page.locator("#new-engine-token").input_value()) != 43:
                        raise AssertionError("Account setup did not provide an engine token")
                    await account_page.reload(wait_until="domcontentloaded")
                    if await account_page.locator("#new-engine-token").count():
                        raise AssertionError("Account token was displayed more than once")
                    report["accountTokenSetup"] = "passed"
                finally:
                    await account_context.close()
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
                report["phase"] = "open settings"
                await page.locator("#bars").click()
                report["phase"] = "discover registrations"
                await page.wait_for_function(
                    "id => [...document.querySelector('#analysis-engine-source').options]"
                    ".some(option => option.value === id)",
                    arg=registration.id,
                )
                if args.expect_unsupported:
                    if not await page.locator(
                        f"#analysis-engine-source option[value='{registration.id}']"
                    ).is_disabled():
                        raise AssertionError("Unsupported engine could be selected")
                    if app[STATE].started or app[STATE].active is not None:
                        raise AssertionError("Unsupported analysis acquired native work")
                    report["unsupportedVariantDisabled"] = "passed"
                    report["result"] = "passed"
                    return report
                await page.locator("#analysis-engine-source").select_option(registration.id)
                report["phase"] = "connect selected engine"
                await page.wait_for_function(
                    "name => document.querySelector('#analysis-engine-name').textContent"
                    ".includes(name) && "
                    "document.querySelector('.nnue').textContent === 'NATIVE' && "
                    "!document.querySelector('#engine-enabled').disabled",
                    arg=app[STATE].capabilities["engine"]["name"],
                    timeout=60000 if args.headed else 15000,
                )
                report["pairing"] = "passed"
                chosen_name = await page.locator(
                    "#analysis-engine-source option:checked"
                ).text_content()
                caps = app[STATE].capabilities["engine"]
                expected_name = caps["name"]
                if chosen_name != expected_name:
                    raise AssertionError("Engine dropdown did not show the selected engine's name")
                if await page.locator(
                    "#external-engine-address, #external-engine-token, #external-engine-connect"
                ).count():
                    raise AssertionError("Manual connection controls remain in the analysis panel")
                report["accountDiscovery"] = "passed"
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
                        await page.locator("#analysis-engine-source").focus()
                        data = await page.evaluate("""() => {
                          const root = document.querySelector('.external-engine-settings');
                          const input = document.querySelector('#analysis-engine-source');
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
                                "External settings failed layout/focus check: " + json.dumps(data)
                            )
                        style_checks.append({"viewport": viewport, "theme": theme, **data})
                report["styles"] = style_checks
                report["stylesheets"] = await page.evaluate(
                    "[...document.styleSheets].filter(s => s.href)"
                    ".map(s => new URL(s.href).pathname)"
                )
                await page.locator("#analysis-engine-source").select_option("browser")
                await asyncio.wait_for(app[STATE].idle.wait(), 3)
                await page.locator("#analysis-engine-source").select_option(registration.id)
                await page.wait_for_function(
                    "document.querySelector('.nnue').textContent === 'NATIVE' && "
                    "document.querySelector('info#info').textContent.includes('Depth')"
                )
                report["sourceSwitching"] = "passed"
                report["phase"] = "competing tab"
                await page.locator("#bars").click()
                other = await context.new_page()
                await other.goto(url, wait_until="domcontentloaded")
                try:
                    await other.wait_for_function(
                        "document.querySelector('#external-engine-status')?.textContent"
                        ".includes('Another tab')",
                        timeout=15000,
                    )
                except Exception:
                    report["competingTabDiagnostics"] = {
                        "firstTab": await page.evaluate("""() => ({
                            source: document.querySelector('#analysis-engine-source')?.value,
                            status: document.querySelector('#external-engine-status')?.textContent,
                            evaluation: document.querySelector('info#info')?.textContent,
                        })"""),
                        "secondTab": await other.evaluate("""() => ({
                            source: document.querySelector('#analysis-engine-source')?.value,
                            status: document.querySelector('#external-engine-status')?.textContent,
                            statusHidden: document.querySelector('#external-engine-status')?.hidden,
                            evaluation: document.querySelector('info#info')?.textContent,
                            retryHidden: document.querySelector('#external-engine-retry')?.hidden,
                        })"""),
                        "nativeActive": app[STATE].controller.active is not None,
                    }
                    raise
                report["competingTab"] = "passed"
                report["phase"] = "tab-close cleanup"
                await page.close()
                await asyncio.wait_for(app[STATE].idle.wait(), 3)
                report["tabCloseCleanup"] = "passed"
                report["phase"] = "reconnect after closing tab"
                await other.locator("#bars").click()
                await other.locator("#external-engine-retry").click()
                await other.locator("#bars").click()
                await other.wait_for_function(
                    "document.querySelector('info#info').textContent.includes('Depth')",
                    timeout=15000,
                )
                report["reconnect"] = "passed"
                report["phase"] = "optional bridge restart"
                if args.restart_bridge:
                    old_search = app[STATE].controller.active
                    assert old_search is not None
                    old_process = app[STATE].controller.engine.process
                    old_released = app[STATE].released
                    await bridge.cleanup()
                    assert old_process is not None and old_process.returncode is not None
                    await other.wait_for_function(
                        "document.querySelector('#external-engine-status').textContent"
                        ".includes('failed') || "
                        "document.querySelector('#external-engine-status').textContent.includes('Unable')"
                    )
                    report["bridgeStop"] = "passed"
                    token = secrets.token_urlsafe(32)
                    app = create_app(
                        EngineConfig(
                            command=(args.engine,), options=tuple(tuple(o) for o in args.setoption)
                        ),
                        token=token,
                        origins=(origin,),
                        authority=urlsplit(address).netloc,
                    )
                    bridge = web.AppRunner(
                        app, access_log=None, handler_cancellation=True, shutdown_timeout=0.2
                    )
                    await bridge.setup()
                    await web.TCPSite(bridge, "127.0.0.1", urlsplit(address).port).start()
                    await other.locator("#bars").click()
                    await other.locator("#external-engine-retry").click()
                    await other.wait_for_function(
                        "document.querySelector('#external-engine-status').textContent"
                        ".includes('select your engine again')"
                    )
                    if app[STATE].started:
                        raise AssertionError("An expired token acquired the restarted engine")
                    report["rotatedTokenRejectsOldPairing"] = "passed"
                    old_registration = registration
                    registration = Registration(
                        local_origin,
                        seed["apiToken"],
                        address,
                        app[STATE].capabilities["engine"],
                        token,
                    )
                    await registration.start()
                    await old_registration.close()
                    await other.locator("#external-engine-retry").click()
                    await other.locator("#bars").click()
                    await other.wait_for_function(
                        "document.querySelector('info#info').textContent.includes('Depth') && "
                        "document.querySelector('.pvbox pv-san')"
                    )
                    active = app[STATE].controller.active
                    if active is None or active.search_id == old_search.search_id:
                        raise AssertionError("Reconnect did not acquire a fresh search")
                    report["restartReconnection"] = "passed"
                    report["releasedBeforeRestart"] = old_released
                report["phase"] = "forget engine"
                await other.locator("#bars").click()
                await other.locator("#external-engine-forget").click()
                await other.wait_for_function(
                    "localStorage.getItem('analysisExternalEngineId') === null && "
                    "document.querySelector('#analysis-engine-source').value === 'browser'"
                )
                await asyncio.wait_for(app[STATE].idle.wait(), 3)
                report["forget"] = "passed"
                if cdp is not None:
                    await registration.close()
                    registration = Registration(
                        local_origin,
                        seed["apiToken"],
                        address,
                        app[STATE].capabilities["engine"],
                        token,
                    )
                    await registration.start()
                    await other.reload(wait_until="domcontentloaded")
                    await set_permission(cdp, other, origin, "denied")
                    await other.wait_for_selector("#bars", state="visible")
                    await other.locator("#bars").click()
                    await other.wait_for_function(
                        "id => [...document.querySelector('#analysis-engine-source').options]"
                        ".some(option => option.value === id)",
                        arg=registration.id,
                    )
                    await other.locator("#analysis-engine-source").select_option(registration.id)
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
                report.pop("phase", None)
            except Exception:
                if page is not None and not page.is_closed():
                    await page.screenshot(
                        path="/tmp/external-account-ui-debug.png",
                        mask=[page.locator("#new-engine-token")],
                    )
                raise
            finally:
                await browser.close()
    except Exception as exc:
        report["result"] = "failed"
        report["error"] = type(exc).__name__ + ": " + str(exc).splitlines()[0]
    finally:
        if bridge is not None:
            await bridge.cleanup()
        if registration is not None:
            await registration.close()
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
    parser.add_argument(
        "--setoption", nargs=2, action="append", default=[], metavar=("NAME", "VALUE")
    )
    parser.add_argument("--browser", choices=("chromium", "edge", "firefox"), default="edge")
    parser.add_argument("--variant", default="chess")
    parser.add_argument("--fen", help="Start the test at this explicit variant FEN")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--without-wasm", action="store_true")
    parser.add_argument("--restart-bridge", action="store_true")
    parser.add_argument("--expect-unsupported", action="store_true")
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
