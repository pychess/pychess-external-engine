"""Check local native analysis across real Study chapter and historical-rules switches."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import time
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright

from pychess_external_engine.app import STATE, create_app
from pychess_external_engine.config import EngineConfig

SERVER_CODE = """
import sys, asyncio, json
sys.argv.append('pytest')
from datetime import datetime, UTC, timedelta
from aiohttp import web
from mongomock_motor import AsyncMongoMockClient
from server import make_app
from pychess_global_app_state_utils import get_app_state
from study.builder import StudyChapterBuilder
from study.storage import create_study_from_draft, add_chapter_from_draft
from fairy import FairyBoard
import test_logger
test_logger.init_test_logger()

async def main():
    app=make_app(db_client=AsyncMongoMockClient(tz_aware=True), simple_cookie_storage=True)
    runner=web.AppRunner(app, access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner,'127.0.0.1',0).start()
        state=get_app_state(runner.app)
        owner='external_probe_owner'
        await state.db.user.insert_one({'_id':owner,'enabled':True,
            'createdAt':datetime.now(UTC)-timedelta(days=30),
            'lang':'en','theme':'dark','ct':'all','perfs':{},'pperfs':{}})
        builder=StudyChapterBuilder(state,owner)
        first=await builder.blank_or_fen(variant='chess',name='Chess')
        study, chapter=await create_study_from_draft(state,owner,first,
            name='External engine probe',visibility='public')
        chapters=[{'id':chapter.id,'name':'Chess','variant':'chess'}]
        orda_draft=await builder.blank_or_fen(variant='orda',name='Orda')
        orda=await add_chapter_from_draft(state,study,orda_draft,activate_shared=False)
        chapters.append({'id':orda.id,'name':'Orda','variant':'orda'})
        root=FairyBoard.start_fen('chess')
        for index, enabled in enumerate(('false','true')):
            name='externalprobe'
            ini=f'[{name}:chess]\\ndoubleStep = {enabled}\\nstartFen = {root}\\n'
            game_id='extOld0'+str(index)
            await state.db.game.insert_one({'_id':game_id,'v':name,'vini':ini,'z':0,'if':root})
            draft=await builder.from_analysis(variant=name,initial_fen=root,game_id=game_id,
                tree_payload={'nodes':[]},name='Snapshot '+enabled)
            saved=await add_chapter_from_draft(state,study,draft,activate_shared=False)
            chapters.append({'id':saved.id,'name':'Snapshot '+enabled,
                'variant':name,'doubleStep':enabled})
        practice=await add_chapter_from_draft(state,study,await builder.blank_or_fen(
            variant='chess',mode='practice',name='Practice'),activate_shared=False)
        chapters.append({'id':practice.id,'name':'Practice','variant':'chess','blocked':True})
        blocked_study, blocked=await create_study_from_draft(state,owner,first,
            name='Computer disabled',visibility='public',settings={'computer':'nobody'})
        print(json.dumps({'port':runner.addresses[0][1],'owner':owner,'study':study.id,'chapters':chapters,
            'blockedStudy':f'/study/{blocked_study.id}/{blocked.id}'}),flush=True)
        await asyncio.to_thread(sys.stdin.buffer.read,1)
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__+': '+str(exc)}),flush=True)
        raise
    finally:
        await runner.cleanup()
asyncio.run(main())
"""


async def run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.pychess).expanduser().resolve()
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(root / "server"), str(root / "tests"))),
        "STUDY_ENABLED_CHAPTER_MODES": "normal,practice,conceal,gamebook",
    }
    server = await asyncio.create_subprocess_exec(
        str(root / ".venv" / "bin" / "python"),
        "-c",
        SERVER_CODE,
        cwd=root,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    bridge = None
    report: dict[str, object] = {
        "browser": "edge",
        "siteDelivery": "Local checkout with in-memory Study database",
    }
    try:
        assert server.stdout is not None
        async with asyncio.timeout(30):
            seed = json.loads(await server.stdout.readline())
        if "error" in seed:
            raise RuntimeError(seed["error"])
        origin = f"http://127.0.0.1:{seed['port']}"
        token = secrets.token_urlsafe(32)
        app = create_app(
            EngineConfig(command=(args.engine,), options=tuple(tuple(o) for o in args.setoption)),
            token=token,
            origins=(origin,),
            authority="",
        )
        bridge = web.AppRunner(app, access_log=None, handler_cancellation=True)
        await bridge.setup()
        await web.TCPSite(bridge, "127.0.0.1", 0).start()
        address = f"http://127.0.0.1:{bridge.addresses[0][1]}"
        app[STATE].authority = address.removeprefix("http://")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, channel="msedge")
            report["version"] = browser.version
            try:
                context = await browser.new_context()
                session_data = {
                    "session": {"user_name": seed["owner"]},
                    "created": int(time.time()),
                }
                await context.add_cookies(
                    [{"name": "AIOHTTP_SESSION", "value": json.dumps(session_data), "url": origin}]
                )
                await context.add_init_script("localStorage.setItem('infiniteAnalysis','true')")
                page = await context.new_page()
                await page.goto(origin + f"/study/{seed['study']}/{seed['chapters'][0]['id']}")
                await page.wait_for_selector("#bars", state="visible", timeout=15000)
                await page.locator("#bars").click()
                await page.locator("#external-engine-address").fill(address)
                await page.locator("#external-engine-token").fill(token)
                await page.locator("#external-engine-connect").click()
                await page.wait_for_function(
                    "document.querySelector('.nnue').textContent === 'NATIVE'"
                )
                await page.locator("#bars").click()
                await page.locator('label[for="engine-enabled"]').click()
                switches = []
                report["chapterSwitches"] = switches
                previous = ""
                snapshots: list[str] = []
                session_id = None
                for index in (0, 1, 2, 3, 2, 0):
                    chapter = seed["chapters"][index]
                    if switches:
                        await page.locator(
                            f'.study-chapters a[data-study-chapter-id="{chapter["id"]}"]'
                        ).click()
                    await page.wait_for_function(
                        "id => document.querySelector('.study-chapters a[aria-current=page]')"
                        "?.dataset.studyChapterId === id",
                        arg=chapter["id"],
                    )
                    async with asyncio.timeout(15):
                        while True:
                            active = app[STATE].controller.active
                            if active is not None and active.search_id != previous:
                                break
                            await asyncio.sleep(0.02)
                    active = app[STATE].controller.active
                    assert active is not None
                    await page.wait_for_function(
                        "document.querySelector('info#info').textContent.includes('Depth')"
                    )
                    if session_id is None:
                        session_id = active.session_id
                    elif active.session_id != session_id:
                        raise AssertionError("Study chapter replaced the tab session identity")
                    if index in (2, 3):
                        if active.rules is None or not active.engine_variant.startswith(
                            "studysnap_"
                        ):
                            raise AssertionError(
                                f"Historical snapshot request: {active.variant}, "
                                f"{active.engine_variant}, rules={active.rules is not None}"
                            )
                        if f"doubleStep = {chapter['doubleStep']}" not in active.rules.ini:
                            raise AssertionError(
                                "Historical rule value changed during chapter navigation"
                            )
                        snapshots.append(active.rules.sha256)
                        board = page.locator("#mainboard cg-board")
                        box = await board.bounding_box()
                        assert box is not None
                        square = {"x": box["width"] * 4.5 / 8, "y": box["height"] * 6.5 / 8}
                        await board.click(position=square)
                        await page.wait_for_selector("#mainboard square.move-dest")
                        count = await page.locator("#mainboard square.move-dest").count()
                        if count != (2 if chapter["doubleStep"] == "true" else 1):
                            raise AssertionError("Browser replay retained a different pawn rule")
                        await board.click(position=square)
                    switches.append(
                        {
                            "chapter": chapter["name"],
                            "engineVariant": active.engine_variant,
                            "rulesHash": active.rules.sha256 if active.rules else None,
                        }
                    )
                    previous = active.search_id
                if snapshots[0] == snapshots[1] or snapshots[0] != snapshots[2]:
                    raise AssertionError("Historical snapshot hashes were not stable and distinct")
                report["chapterSwitches"] = switches
                report["historicalSnapshots"] = "passed"
                practice = seed["chapters"][4]
                await page.locator(
                    f'.study-chapters a[data-study-chapter-id="{practice["id"]}"]'
                ).click()
                await page.wait_for_selector(".study-practice", timeout=15000)
                await asyncio.wait_for(app[STATE].idle.wait(), 3)
                await page.locator("#bars").click()
                if not await page.locator("#external-engine-connect").is_disabled():
                    raise AssertionError("Practice offered an external engine")
                report["practiceRemainsBrowserOwned"] = "passed"
                await page.goto(origin + seed["blockedStudy"])
                await page.wait_for_selector("#bars", state="visible")
                await page.locator("#bars").click()
                if not await page.locator("#external-engine-connect").is_disabled():
                    raise AssertionError("Study computer permission was bypassed")
                await asyncio.wait_for(app[STATE].idle.wait(), 3)
                report["computerPermission"] = "passed"
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
    parser.add_argument(
        "--setoption", nargs=2, action="append", default=[], metavar=("NAME", "VALUE")
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    report = asyncio.run(run(args))
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output, end="")
    if report["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
