"""The web interface in a real browser (Chromium through Playwright): every
page opens without a JavaScript error, with a router that is offline.

Needs `pip install playwright && playwright install chromium`; skipped otherwise.
"""
import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest

from podkop_manager.agent import AgentError
from podkop_manager.db import DB
from podkop_manager.hub import Hub
from podkop_manager.web import create_app

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
PAGES = ["#/settings", "#/r/1", "#/r/1/usage", "#/r/1/rules", "#/r/1/global", "#/r/1/configs",
         "#/r/1/reliability", "#/r/1/logs", "#/r/1/history"]


class OfflineAgent:
    async def call(self, *args, **kwargs):
        raise AgentError("unreachable", "cannot connect to 192.0.2.1: test")


class TestHub(Hub):
    def _start_router(self, row):
        rt = super()._start_router(row)
        rt.agent = OfflineAgent()
        return rt


@pytest.fixture
def server(tmp_path):
    import uvicorn
    db = DB(tmp_path / "m.db")
    db.add_router("home", "192.0.2.1", 22, "ssh-ed25519 AAAA host")
    db.add_link_key(1, "vless://u@own.example:443#own", "own-link", 5)
    hub = TestHub(db, tmp_path / "id_ed25519", ROOT)
    app = create_app(db, hub, "ssh-ed25519 AAAA test", "secret-pass")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=lambda: asyncio.run(srv.serve()), daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=10)


def test_every_page_opens_without_script_errors(server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - no browser installed here
            pytest.skip(f"no Chromium for Playwright: {str(e).splitlines()[0]}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource")
                and errors.append(f"console: {m.text}"))
        page.goto(f"{server}/login")
        page.fill("#user", "admin")
        page.fill("#pw", "secret-pass")
        page.click("button[type=submit]")
        page.wait_for_url(f"{server}/**")
        page.wait_for_selector("#side a")           # the sidebar is drawn by app.js
        for hash_ in PAGES:
            page.goto(f"{server}/{hash_}")
            page.wait_for_function("document.querySelector('#main .page') !== null", timeout=10000)
            page.wait_for_timeout(300)             # the page's data loads and draws
            assert page.locator("#main").inner_text().strip(), hash_
        assert errors == [], errors
        # Keys and usage: a key's row opens to its details, the add dialog opens
        page.goto(f"{server}/#/r/1/usage")
        page.wait_for_selector("text=own-link")
        page.click("[aria-label='Show details of own-link']")
        page.wait_for_selector(".key-details")
        page.click("text=Add key")
        page.wait_for_selector("#nk-link")
        assert errors == [], errors
        # the Settings page shows this router and its agent update dialog opens
        page.goto(f"{server}/#/settings")
        page.wait_for_selector("text=home")
        # the page redraws when the router's status arrives: a plain click can find the button replaced
        page.locator("text=Update agent").first.dispatch_event("click")
        page.wait_for_selector("text=Update from this manager")
        assert errors == [], errors
        browser.close()
