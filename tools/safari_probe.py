"""Diagnostic: real macOS Safari HTTPS-to-loopback request, using SafariDriver.

Run only on macOS with Safari remote automation enabled. No production writes.
This checks transport and pairing, not native UCI or the Pychess UI.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from importlib import import_module
from urllib.parse import urlsplit

from aiohttp import web

from tools.transport_probe import STATE, create_app
from tools.site_safety import require_nonproduction_https


async def check(site: str) -> dict[str, object]:
    require_nonproduction_https(site)
    origin_parts = urlsplit(site)
    origin = f"{origin_parts.scheme}://{origin_parts.netloc}"
    token = secrets.token_urlsafe(32)
    app = create_app(token=token, origins=(origin,), authority="", max_seconds=10)
    requests_seen: list[dict[str, object]] = []

    @web.middleware
    async def record_requests(request: web.Request, handler: object) -> web.StreamResponse:
        event: dict[str, object] = {
            "method": request.method,
            "path": request.path,
            "origin": request.headers.get("Origin"),
            "preflight": request.headers.get("Access-Control-Request-Method"),
        }
        requests_seen.append(event)
        response = await handler(request)  # type: ignore[operator]
        event["status"] = response.status
        return response

    # Put tracing ahead of the access middleware, including rejected preflights.
    app.middlewares.insert(0, record_requests)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    listener = web.TCPSite(runner, "127.0.0.1", 0)
    await listener.start()
    port = runner.addresses[0][1]
    app[STATE].authority = f"127.0.0.1:{port}"
    address = f"http://127.0.0.1:{port}"

    def browser_check() -> dict[str, object]:
        # Selenium is supplied only to the Safari workflow via `uv run --with selenium`.
        # Import it at runtime so the ordinary project type checks do not require it.
        webdriver = import_module("selenium.webdriver")
        options_module = import_module("selenium.webdriver.safari.options")
        options = options_module.Options()
        driver = webdriver.Safari(options=options)
        try:
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(25)
            driver.get(site)
            context = driver.execute_script(
                "return {secure: isSecureContext, isolated: crossOriginIsolated}"
            )
            if not context["secure"]:
                raise AssertionError("HTTPS site is not a secure context")
            # SafariDriver executes this in real Safari; no permission bypass is used.
            script = """
              const [address, token, done] = arguments;
              (async () => {
                const query = async credential => {
                  const response = await fetch(address + '/v1/capabilities', {
                    credentials: 'omit', cache: 'no-store',
                    headers: {'Authorization': 'Bearer ' + credential},
                    signal: AbortSignal.timeout(12000)
                  });
                  return {status: response.status, body: await response.json()};
                };
                let stage = 'valid credential';
                try {
                  const valid = await query(token);
                  stage = 'invalid credential';
                  const invalid = await query('not-a-valid-token');
                  done({validStatus: valid.status, prototype: valid.body.prototype,
                        invalidStatus: invalid.status});
                } catch (error) {
                  done({error: String(error), stage, name: error?.name ?? null});
                }
              })().catch(error => done({error: String(error), stage: 'setup'}));
            """
            result = driver.execute_async_script(script, address, token)
            if "error" in result:
                raise AssertionError(
                    json.dumps(
                        {
                            "browser": driver.capabilities.get("browserName"),
                            "version": driver.capabilities.get("browserVersion"),
                            "context": context,
                            "fetch": result,
                            "requestsSeen": list(requests_seen),
                        }
                    )
                )
            if result != {"validStatus": 200, "prototype": True, "invalidStatus": 401}:
                raise AssertionError(f"Unexpected transport results: {result}")
            return {
                "browser": "Safari",
                "version": driver.capabilities.get("browserVersion"),
                "site": site,
                "context": context,
                "transport": "passed",
                "pairing": "passed",
                "scope": "prototype capabilities only",
            }
        finally:
            driver.quit()

    try:
        # Selenium is synchronous: leave the aiohttp loop running during browser calls.
        return await asyncio.wait_for(asyncio.to_thread(browser_check), timeout=75)
    finally:
        await runner.cleanup()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site", required=True, help="Non-production HTTPS fixture with COOP/COEP headers"
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(check(args.site))
        result["result"] = "passed"
    except Exception as exc:
        result = {"result": "failed", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, indent=2))
    if result["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
