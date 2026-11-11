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
         "#/r/1/reliability", "#/r/1/vpn", "#/r/1/logs", "#/r/1/history"]


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


class OnlineAgent:
    """A router that answers: podkop runs these settings and accepts any change."""
    config = {"sections": [
        {"name": "settings", "type": "settings", "anonymous": False, "options": {}},
        {"name": "main", "type": "section", "anonymous": False, "options": {
            "connection_type": "proxy", "proxy_config_type": "url", "proxy_string": "vless://a@h:1#A",
            "user_domains": ["a.com"]}}]}

    async def call(self, name, *args, payload=None, **kwargs):
        if name == "status":
            return {"agent": {"version": "0.10.0", "capabilities": ["diagnostics", "eligibility", "heal", "resolve", "upgrade"]},
                    "podkop": {"running": False, "busy": False, "enabled": True, "paused": False, "version": "0.7.24"},
                    "fingerprint": "fp1", "singbox": {"version": "1.13", "features": []}, "router": {}, "pins": [],
                    "probe": {"state": "none"}, "heal": None}
        if name == "config-get":
            return {"config": self.config, "fingerprint": "fp1"}
        if name == "validate":
            return {"problems": []}
        raise AgentError("unsupported", f"{name}: not in this test")


def test_a_change_is_scheduled_from_the_apply_dialog(tmp_path):
    import copy
    import uvicorn
    from playwright.sync_api import sync_playwright

    class OnlineHub(Hub):
        def _start_router(self, row):
            rt = super()._start_router(row)
            rt.agent = OnlineAgent()
            return rt

    db = DB(tmp_path / "m.db")
    db.add_router("home", "192.0.2.1", 22, "ssh-ed25519 AAAA host")
    draft = copy.deepcopy(OnlineAgent.config)
    draft["sections"][1]["options"]["user_domains"] = ["a.com", "b.com"]
    db.set_draft(1, draft, "fp1")
    hub = OnlineHub(db, tmp_path / "id_ed25519", ROOT)
    app = create_app(db, hub, "ssh-ed25519 AAAA test", "secret-pass")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=lambda: asyncio.run(srv.serve()), daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"no Chromium for Playwright: {str(e).splitlines()[0]}")
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.goto(f"{base}/login")
            page.fill("#user", "admin")
            page.fill("#pw", "secret-pass")
            page.click("button[type=submit]")
            page.wait_for_selector("#side a")
            page.goto(f"{base}/#/r/1/rules")
            page.locator("text=Review and apply").first.dispatch_event("click")
            page.wait_for_selector("text=podkop accepts these settings")
            page.click("[aria-label='When'] >> text=Later")
            page.wait_for_selector("#apply-due")
            page.screenshot(path=str(tmp_path / "schedule-dialog.png"))
            page.click(".dialog-foot >> text=Schedule")
            page.wait_for_selector("text=Scheduled for")
            v = db.scheduled(1)
            assert len(v) == 1 and v[0]["changes"] == 1 and db.draft(1) is None
            page.keyboard.press("Escape")
            page.goto(f"{base}/#/r/1/history")
            page.wait_for_selector("#h-sched")
            page.wait_for_selector("text=Apply now")
            page.screenshot(path=str(tmp_path / "schedule-history.png"), full_page=True)
            page.locator("text=Cancel").first.dispatch_event("click")
            page.wait_for_selector("text=cancelled")
            assert db.scheduled(1) == [] and errors == [], errors
            browser.close()
    finally:
        srv.should_exit = True
        t.join(timeout=10)


def test_vpn_page_shows_the_tunnel_sets_its_dns_and_saves_devices(tmp_path):
    import copy
    import uvicorn
    from playwright.sync_api import sync_playwright

    calls = []

    class VpnAgent(OnlineAgent):
        config = copy.deepcopy(OnlineAgent.config)
        config["sections"].append({"name": "vpn_1", "type": "section", "anonymous": False, "options": {
            "connection_type": "vpn", "interface": "pkvpn", "user_domain_list_type": "dynamic", "user_domains": ["corp.example"]}})
        state = {"installed": True, "configured": True, "enabled": True, "running": True, "up": True, "address": "10.9.0.2/24",
                 "remote": "vpn.example:1194", "device": "pkvpn", "username": "me", "password_set": True, "allow": {"vpn_1": ["192.168.1.5/32"]},
                 "log": ["Initialization Sequence Completed"], "dns": "10.9.0.1"}

        async def call(self, name, *args, payload=None, **kwargs):
            if name == "vpn":
                if args[0] == "set":
                    calls.append(payload)
                    self.state = dict(self.state, **{k: v for k, v in payload.items() if k in ("allow", "enabled")})
                return self.state
            return await super().call(name, *args, payload=payload, **kwargs)

    class VpnHub(Hub):
        def _start_router(self, row):
            rt = super()._start_router(row)
            rt.agent = VpnAgent()
            return rt

    db = DB(tmp_path / "m.db")
    db.add_router("home", "192.0.2.1", 22, "ssh-ed25519 AAAA host")
    hub = VpnHub(db, tmp_path / "id_ed25519", ROOT)
    app = create_app(db, hub, "ssh-ed25519 AAAA test", "secret-pass")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=lambda: asyncio.run(srv.serve()), daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"no Chromium for Playwright: {str(e).splitlines()[0]}")
            page = browser.new_page(viewport={"width": 1280, "height": 1100})
            errors = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.goto(f"{base}/login")
            page.fill("#user", "admin")
            page.fill("#pw", "secret-pass")
            page.click("button[type=submit]")
            page.wait_for_selector("#side a")
            page.goto(f"{base}/#/r/1/vpn")
            page.wait_for_selector("text=Connected, 10.9.0.2/24")
            # the list saved per rule by 1.48.0 is shown as the tunnel's
            box = page.locator("textarea[aria-label='Devices allowed to use the VPN']")
            assert box.input_value() == "192.168.1.5/32"
            box.fill("192.168.1.5\n192.168.1.6")
            page.click("text=Save devices")
            page.wait_for_selector("text=Devices saved")
            assert calls[-1] == {"allow": {"*": ["192.168.1.5/32", "192.168.1.6/32"]}}
            page.click("text=Use it for the VPN rules")
            page.wait_for_selector("text=The VPN rules resolve their names through it")
            page.goto(f"{base}/#/r/1/rules/vpn_1")
            page.wait_for_selector("text=Review and apply")           # the DNS went into the draft
            assert not errors, errors
            browser.close()
    finally:
        srv.should_exit = True
        t.join(timeout=10)
