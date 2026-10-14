"""Diagnostic: real macOS Safari HTTPS-to-loopback request, using SafariDriver.

Run only on macOS with Safari remote automation enabled. No production writes.
This checks transport and pairing, not native UCI or the Pychess UI.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from urllib.parse import urlsplit

from aiohttp import web
from selenium import webdriver
from selenium.webdriver.safari.options import Options

from tools.transport_probe import STATE, create_app


async def check(site: str) -> dict[str, object]:
    origin_parts = urlsplit(site)
    if origin_parts.scheme != "https":
        raise ValueError("--site must be HTTPS")
    origin = f"{origin_parts.scheme}://{origin_parts.netloc}"
    token = secrets.token_urlsafe(32)
    app = create_app(token=token, origins=(origin,), authority="", max_seconds=10)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    listener = web.TCPSite(runner, "127.0.0.1", 0)
    await listener.start()
    port = runner.addresses[0][1]
    app[STATE].authority = f"127.0.0.1:{port}"
    address = f"http://127.0.0.1:{port}"

    def browser_check() -> dict[str, object]:
        options = Options()
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
                const valid = await query(token);
                const invalid = await query('not-a-valid-token');
                done({validStatus: valid.status, prototype: valid.body.prototype,
                      invalidStatus: invalid.status});
              })().catch(error => done({error: String(error)}));
            """
            result = driver.execute_async_script(script, address, token)
            if "error" in result:
                raise AssertionError(result["error"])
            if result != {"validStatus": 200, "prototype": True, "invalidStatus": 401}:
                raise AssertionError(f"Unexpected transport results: {result}")
            return {"browser": "Safari", "version": driver.capabilities.get("browserVersion"),
                    "site": site, "context": context, "transport": "passed",
                    "pairing": "passed", "scope": "prototype capabilities only"}
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
    parser.add_argument("--site", default="https://www.pychess.org/analysis/chess")
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
