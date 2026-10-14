"""Opt-in browser verification against an HTTPS page; uses fresh browser profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web
from playwright.async_api import (
    Browser,
    CDPSession,
    Page,
    async_playwright,
)
from playwright.async_api import (
    Error as BrowserError,
)

from pychess_external_engine.app import STATE as NATIVE_STATE
from pychess_external_engine.app import create_app as create_native_app
from pychess_external_engine.config import EngineConfig
from tools.transport_probe import STATE, create_app
from tools.site_safety import require_nonproduction_https

START_STREAM = """async ({address, token, analysis, searchId}) => {
  const controller = new AbortController();
  const response = await fetch(address + '/v1/analyse', {
    method: 'POST', credentials: 'omit', cache: 'no-store',
    targetAddressSpace: 'loopback', signal: controller.signal,
    headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'},
    body: JSON.stringify({...analysis, sessionId: 'browser-probe', searchId})
  });
  if (!response.ok) throw new Error('Stream HTTP ' + response.status);
  const reader = response.body.getReader();
  window.externalTransportProbe = {controller, reader};
  let buffer = '';
  const events = [];
  while (events.length < 2) {
    const {value, done} = await reader.read();
    if (done) throw new Error('Stream ended before evaluation');
    buffer += new TextDecoder().decode(value);
    const lines = buffer.split('\\n');
    buffer = lines.pop();
    for (const line of lines) if (line) events.push(JSON.parse(line));
  }
  return events;
}"""

GET_CAPABILITIES = """async ({address, token}) => {
  const response = await fetch(address + '/v1/capabilities', {
    credentials: 'omit', cache: 'no-store', targetAddressSpace: 'loopback',
    headers: {Authorization: 'Bearer ' + token}
  });
  return {status: response.status, body: await response.json()};
}"""


async def set_permission(cdp: CDPSession, page: Page, origin: str, setting: str) -> None:
    contexts = await cdp.send("Target.getBrowserContexts")
    if len(contexts["browserContextIds"]) != 1:
        raise AssertionError("Permission probe expects exactly one fresh browser context")
    # Keep this browser-level session alive: detaching clears permission overrides.
    await cdp.send(
        "Browser.setPermission",
        {
            "permission": {"name": "loopback-network"},
            "setting": setting,
            "origin": origin,
            "embeddedOrigin": origin,
            "browserContextId": contexts["browserContextIds"][0],
        },
    )
    await page.wait_for_function(
        "async setting => "
        "(await navigator.permissions.query({name: 'loopback-network'})).state === setting",
        arg=setting,
        timeout=3000,
    )


async def check_browser(
    browser: Browser,
    kind: str,
    site: str,
    *,
    manual_permission: bool = False,
    engine_config: EngineConfig | None = None,
) -> dict[str, object]:
    require_nonproduction_https(site)
    parsed = urlsplit(site)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    token = secrets.token_urlsafe(32)
    if engine_config is None:
        app = create_app(token=token, origins=(origin,), authority="", max_seconds=30)
        state = app[STATE]
    else:
        app = create_native_app(engine_config, token=token, origins=(origin,), authority="")
        state = app[NATIVE_STATE]
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    listener = web.TCPSite(runner, "127.0.0.1", 0)
    await listener.start()
    # AppRunner exposes actual listener addresses without reaching into private sockets.
    port = runner.addresses[0][1]
    state.authority = f"127.0.0.1:{port}"
    address = f"http://127.0.0.1:{port}"
    context = await browser.new_context()

    async def serve_fixture(route):
        # Every browser request is intercepted; unexpected URLs fail closed.
        if route.request.url == site:
            await route.fulfill(
                status=200, content_type="text/html",
                headers={
                    "Cross-Origin-Opener-Policy": "same-origin",
                    "Cross-Origin-Embedder-Policy": "require-corp",
                    "Cache-Control": "no-store",
                },
                body="<!doctype html><html><head><title>Local probe</title></head><body></body></html>",
            )
        else:
            await route.abort()

    await context.route("**/*", serve_fixture)
    permission_session: CDPSession | None = None
    report: dict[str, object] = {
        "browser": kind,
        "version": browser.version,
        "site": site,
        "backend": "native" if engine_config else "prototype",
    }
    try:
        page = await context.new_page()
        response = await page.goto(site, wait_until="domcontentloaded", timeout=30000)
        if response is None or response.status != 200:
            raise AssertionError("HTTPS test page failed to load")
        headers = await response.all_headers()
        report["headers"] = {
            key: value
            for key, value in headers.items()
            if key
            in (
                "cross-origin-opener-policy",
                "cross-origin-embedder-policy",
                "content-security-policy",
                "permissions-policy",
            )
        }
        isolation = await page.evaluate(
            "({secure: isSecureContext, isolated: crossOriginIsolated, "
            "sharedArrayBuffer: typeof SharedArrayBuffer === 'function'})"
        )
        if not isolation["secure"]:
            raise AssertionError("HTTPS test page is not a secure context")
        report["context"] = isolation
        if kind in ("chromium", "edge") and not manual_permission:
            permission_session = await browser.new_browser_cdp_session()
            await set_permission(permission_session, page, origin, "granted")
        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        args = {
            "address": address,
            "token": token,
            "analysis": {
                "protocolVersion": 1,
                "variant": "chess",
                "engineVariant": "chess",
                "chess960": False,
                "initialFen": fen,
                "moves": [],
                "currentFen": fen,
                "search": {"depth": 128},
                "threads": 1,
                "hash": 16,
                "multiPv": 1,
            },
        }
        if manual_permission:
            print(
                "When the browser asks to access device apps/services, choose Allow.",
                flush=True,
            )
        async with asyncio.timeout(60 if manual_permission else 10):
            capabilities = await page.evaluate(GET_CAPABILITIES, args)
        if capabilities["status"] != 200 or bool(capabilities["body"].get("prototype")) != (
            engine_config is None
        ):
            raise AssertionError("Paired capabilities failed")
        unauthorized = await page.evaluate(GET_CAPABILITIES, {**args, "token": "incorrect"})
        if unauthorized["status"] != 401:
            raise AssertionError("Incorrect pairing was not rejected")
        events = await page.evaluate(START_STREAM, {**args, "searchId": "browser-search-1"})
        if events[0]["type"] != "started" or events[1]["type"] != "evaluation":
            raise AssertionError("Expected streamed started/evaluation events")
        report["stream"] = "passed"
        if engine_config is not None:
            if events[1].get("prototype") or not events[1]["pvs"][0]["moves"]:
                raise AssertionError("Native evaluation did not contain a validated PV")
            report["engine"] = capabilities["body"]["engine"]["name"]
        # A second page is a real competing tab, rather than a second HTTP-only client.
        other_page = await context.new_page()
        await other_page.goto(site, wait_until="domcontentloaded", timeout=30000)
        busy = await other_page.evaluate(
            """async ({address, token, analysis}) => {
              const response = await fetch(address + '/v1/analyse', {
                method: 'POST', credentials: 'omit', targetAddressSpace: 'loopback',
                headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'},
                body: JSON.stringify({
                  ...analysis, sessionId: 'second-tab', searchId: 'busy'
                })
              });
              return {status: response.status, body: await response.json()};
            }""",
            args,
        )
        if busy["status"] != 409 or state.active != (
            "browser-probe",
            "browser-search-1",
        ):
            raise AssertionError("Competing tab replaced the stream owner")
        report["competingTab"] = "passed"
        await page.evaluate("window.externalTransportProbe.controller.abort()")
        await asyncio.wait_for(state.idle.wait(), 2)
        report["abortCleanup"] = "passed"
        replacement = await page.evaluate(START_STREAM, {**args, "searchId": "browser-search-2"})
        if replacement[0]["searchId"] != "browser-search-2":
            raise AssertionError("Replacement stream had stale search identity")
        await page.close()
        await asyncio.wait_for(state.idle.wait(), 2)
        report["tabCloseCleanup"] = "passed"
        if permission_session is not None:
            await set_permission(permission_session, other_page, origin, "denied")
            result = await other_page.evaluate(
                """async args => {
                  try {
                    await fetch(args.address + '/v1/capabilities', {
                      targetAddressSpace: 'loopback', credentials: 'omit',
                      headers: {Authorization: 'Bearer ' + args.token},
                      signal: AbortSignal.timeout(3000)
                    });
                    return 'unexpected-success';
                  } catch (error) { return error.name; }
                }""",
                args,
            )
            if result != "TypeError":
                raise AssertionError("Denied loopback permission did not block fetch: " + result)
            report["deniedPermission"] = "passed"
        else:
            report["deniedPermission"] = "pending manual verification"
        if state.active is not None or state.started != state.released:
            raise AssertionError("Stream ownership was not fully released")
        report["releasedStreams"] = state.released
        report["result"] = "passed"
        return report
    except (AssertionError, BrowserError, TimeoutError) as exc:
        report["result"] = "failed"
        report["error"] = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return report
    finally:
        if permission_session is not None:
            await permission_session.detach()
        await context.close()
        await runner.cleanup()


async def run(args: argparse.Namespace) -> None:
    reports: list[dict[str, object]] = []
    async with async_playwright() as playwright:
        kind = getattr(playwright, "chromium" if args.browser == "edge" else args.browser)
        executable = (
            shutil.which("chromium-browser") or shutil.which("chromium")
            if args.browser == "chromium"
            else None
        )
        browser = await kind.launch(
            headless=not args.headed,
            executable_path=executable,
            channel="msedge" if args.browser == "edge" else None,
        )
        try:
            reports.append(
                await check_browser(
                    browser,
                    args.browser,
                    args.site,
                    manual_permission=args.manual_permission,
                    engine_config=EngineConfig(
                        command=(args.engine,),
                        options=tuple(tuple(option) for option in args.setoption),
                    )
                    if args.engine
                    else None,
                )
            )
        finally:
            await browser.close()
    output = json.dumps({"platform": platform.platform(), "checks": reports}, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output, end="")
    if any(report["result"] != "passed" for report in reports):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser",
        choices=("chromium", "edge", "firefox", "webkit"),
        default="chromium",
    )
    parser.add_argument("--site", default="https://probe.pychess.invalid/analysis/chess")
    parser.add_argument("--output")
    parser.add_argument("--engine", help="Test the production API with this native UCI executable")
    parser.add_argument(
        "--setoption", nargs=2, action="append", default=[], metavar=("NAME", "VALUE")
    )
    parser.add_argument("--headed", action="store_true", help="Open a disposable visible browser")
    parser.add_argument(
        "--manual-permission",
        action="store_true",
        help="Wait up to 60 seconds for manual Allow",
    )
    args = parser.parse_args()
    try:
        require_nonproduction_https(args.site)
    except ValueError as exc:
        parser.error(str(exc))
    if args.manual_permission and not args.headed:
        parser.error("manual permission verification requires --headed")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
