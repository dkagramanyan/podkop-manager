"""The web interface in a real browser (Chromium through Playwright): every
page opens without a JavaScript error, with a router that is offline.

Needs `pip install playwright && playwright install chromium`; skipped otherwise.
"""
import asyncio
import json
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
PAGES = ["#/settings", "#/r/1", "#/r/1/usage", "#/r/1/rules", "#/r/1/global", "#/r/1/configs", "#/r/1/devices",
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
                    "probe": {"state": "none"}, "heal": None,
                    # the banner on Overview and the switch on the rule's page are drawn
                    "protected": ["main"], "safe_mode": {"on": True, "since": 1, "reason": "test", "trial": True}}
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

    calls, tests = [], []

    class VpnAgent(OnlineAgent):
        config = copy.deepcopy(OnlineAgent.config)
        config["sections"].append({"name": "vpn_1", "type": "section", "anonymous": False, "options": {
            "connection_type": "vpn", "interface": "pkvpn", "user_domain_list_type": "dynamic", "user_domains": ["corp.example"]}})
        state = {"installed": True, "configured": True, "enabled": True, "running": True, "up": True, "address": "10.9.0.2/24",
                 "remote": "vpn.example:1194", "device": "pkvpn", "username": "me", "password_set": True, "allow": {"vpn_1": ["192.168.1.5/32"]},
                 "log": ["Initialization Sequence Completed"], "dns": "10.9.0.1"}

        async def call(self, name, *args, payload=None, **kwargs):
            if name == "vpn":
                if args[0] == "test":       # the name exists only in the VPN's DNS
                    tests.append(payload)
                    return {"ok": True, "reason": "", "error": ""} if payload["dns"] == "10.9.0.1" else \
                        {"ok": False, "reason": "dns", "error": "lookup corp.example: empty result"}
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
            # checked with the draft's settings before anything is applied: not found, but the VPN's DNS knows it
            page.wait_for_selector("text=corp.example: the name is not found by podkop's DNS")
            page.wait_for_selector("text=It opens when the name is resolved by the VPN's DNS server, 10.9.0.1")
            page.click("text=Use it for the VPN rules")
            page.wait_for_selector("text=The VPN rules resolve their names through it")
            page.wait_for_selector("text=corp.example opens through the VPN")
            assert {"host": "corp.example", "dns": ""} in tests and {"host": "corp.example", "dns": "10.9.0.1"} in tests
            page.goto(f"{base}/#/r/1/rules/vpn_1")
            page.wait_for_selector("text=Review and apply")           # the DNS went into the draft
            assert not errors, errors
            browser.close()
    finally:
        srv.should_exit = True
        t.join(timeout=10)


class DevicesAgent(OnlineAgent):
    """agent 0.28: the device list and "where does it go"."""
    async def call(self, name, *args, payload=None, **kwargs):
        if name == "status":
            st = await super().call(name)
            st["agent"]["capabilities"] += ["devices", "route", "zapret"]
            st["protected"], st["safe_mode"] = [], {"on": False}
            return st
        if name == "devices":
            return {"devices": [{"ip": "192.168.1.20", "mac": "aa:bb:cc:00:00:20", "name": "tv", "fixed": False, "lease": True}]}
        if name == "route":
            return {"target": payload["target"], "kind": "domain", "from": "", "final": "direct-out", "unread": [], "nft_set": None,
                    "seen": None, "dns": {"address": "198.18.0.5", "fakeip": True},
                    "match": {"rule": 3, "outbound": "main-out", "list": "main-user-domains-ruleset", "every": False}}
        return await super().call(name, *args, payload=payload, **kwargs)


def test_devices_page_and_where_does_it_go(tmp_path):
    import uvicorn
    from playwright.sync_api import sync_playwright

    class DevicesHub(Hub):
        def _start_router(self, row):
            rt = super()._start_router(row)
            rt.agent = DevicesAgent()
            return rt

    db = DB(tmp_path / "m.db")
    db.add_router("home", "192.0.2.1", 22, "ssh-ed25519 AAAA host")
    hub = DevicesHub(db, tmp_path / "id_ed25519", ROOT)
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
            page.goto(f"{base}/#/r/1/devices")
            page.wait_for_selector("text=tv")
            page.select_option("[aria-label='Traffic of tv']", "main")
            page.wait_for_selector("text=address from DHCP, it may change")
            page.screenshot(path=str(tmp_path / "devices.png"), full_page=True)
            page.goto(f"{base}/#/r/1/rules")          # at once: the draft is saved half a second later
            page.wait_for_selector("text=Where does it go?")
            for _ in range(40):
                d = db.draft(1)
                if d and d["config"]["sections"][1]["options"].get("fully_routed_ips"):
                    break
                time.sleep(0.1)
            assert db.draft(1)["config"]["sections"][1]["options"]["fully_routed_ips"] == ["192.168.1.20"]
            page.click("text=Where does it go?")
            page.fill("[aria-label='Name or address']", "a.com")
            page.click("text=Ask the router")
            page.wait_for_selector("text=Rule main takes it (its own domains).")
            page.screenshot(path=str(tmp_path / "route.png"))
            assert errors == [], errors
            browser.close()
    finally:
        srv.should_exit = True
        t.join(timeout=10)


def test_rule_servers_show_flags_and_sort_by_ping(tmp_path):
    import copy
    import uvicorn
    from playwright.sync_api import sync_playwright
    from test_podkop import ROWS

    class AutoAgent(OnlineAgent):
        config = copy.deepcopy(OnlineAgent.config)
        config["sections"][1]["options"] = {"connection_type": "proxy", "proxy_config_type": "urltest",
                                            "urltest_proxy_links": ["vless://a@h:1#A"]}

        async def call(self, name, *args, payload=None, **kwargs):
            if name == "status":
                st = await super().call(name)
                st["protected"], st["safe_mode"] = [], {"on": False}
                return st
            return await super().call(name, *args, payload=payload, **kwargs)

    class AutoHub(Hub):
        def _start_router(self, row):
            rt = super()._start_router(row)
            rt.agent = AutoAgent()
            return rt

    db = DB(tmp_path / "m.db")
    db.add_router("home", "192.0.2.1", 22, "ssh-ed25519 AAAA host")
    from podkop_manager import podkop
    db.set_auto_rule(1, "main", podkop.check_auto_rule({"countries": ["PL"], "top": 2, "fallback": 1}))
    db.finish_probe_run(db.add_probe_run(1, "manual"), "done", {"finished": "2026-10-17T21:00:00Z", "rows": ROWS})
    app = create_app(db, AutoHub(db, tmp_path / "id_ed25519", ROOT), "ssh-ed25519 AAAA test", "secret-pass")
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
            page = browser.new_page(viewport={"width": 1500, "height": 1400})
            errors = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.goto(f"{base}/login")
            page.fill("#user", "admin")
            page.fill("#pw", "secret-pass")
            page.click("button[type=submit]")
            page.wait_for_selector("#side a")
            page.goto(f"{base}/#/r/1/rules/main")
            page.click("details[data-panel=probe] > summary")          # closed at first: its summary shows the filter
            page.wait_for_selector("text=Matches now: 3 servers")
            rows = lambda: page.locator(".servers table tr td:first-child").all_inner_texts()      # noqa: E731
            picked = rows()
            assert "\U0001F1F5\U0001F1F1 PL" in page.locator(".servers table").inner_text()      # the flag of Poland
            page.click(".servers table th button")
            medians = [float(x.split()[0]) for x in page.locator(".servers table tr td:nth-child(5)").all_inner_texts()]
            assert medians == sorted(medians) and sorted(rows()) == sorted(picked)
            page.locator(".servers").screenshot(path=str(tmp_path / "servers.png"))
            page.click(".servers table th button")
            assert rows() == picked and errors == [], errors
            # the nightly probe is not on a rule's page: it has its card at the bottom of Keys and usage
            assert page.locator("#probe-sched, [id^=sched-]").count() == 0
            assert page.locator("a:has-text('Keys and usage')").count() >= 1
            page.goto(f"{base}/#/r/1/usage")
            page.wait_for_selector("#nightly-probe h2:has-text('Nightly probe')")
            page.wait_for_selector("#probe-sched")
            page.fill("#probe-sched", "03:30")
            page.locator("#probe-sched").dispatch_event("change")
            page.wait_for_selector("#nightly-probe :text('Every night at 03:30')")
            assert db.probe_settings(1)["schedule"] == "03:30"
            assert errors == [], errors
            browser.close()
    finally:
        srv.should_exit = True
        t.join(timeout=10)


def test_zapret_first_on_a_rules_page_finds_a_strategy_and_shows_in_the_list(tmp_path):
    import copy
    import uvicorn
    from playwright.sync_api import sync_playwright
    from podkop_manager import zapret

    zapret.POLL = 0.05
    sets, checks = [], []

    class ZapretAgent(OnlineAgent):
        config = copy.deepcopy(OnlineAgent.config)
        for n in ("meta", "github"):
            config["sections"].append({"name": n, "type": "section", "anonymous": False, "options": {
                "connection_type": "proxy", "proxy_config_type": "outbound", "user_domain_list_type": "dynamic", "user_domains": [f"{n}.example"]}})
        rules = {}

        async def call(self, name, *args, payload=None, **kwargs):
            if name == "status":
                st = await super().call(name, *args, payload=payload, **kwargs)
                st["agent"] = {"version": "0.29.0", "capabilities": ["zapret"]}
                st["proxies"] = {"groups": [{"name": f"{k}-zapret-out", "now": f"{k}-zapret-direct", "all": []} for k in self.rules], "servers": []}
                return st
            if name == "zapret" and args[0] == "status":
                return {"arch": "aarch64", "installed": "v1.0.7", "queue": True, "table": bool(self.rules), "rules": self.rules,
                        "running": list(self.rules), "refused": [], "using": {k: "zapret" for k in self.rules}}
            if name == "zapret" and args[0] == "check":
                checks.append(payload)
                return {"job": "1-1"}
            if name == "job":
                return {"state": "done", "baseline": [{"url": "u", "ok": False}],
                        "results": [{"name": "fake md5", "args": ["--payload=tls_client_hello"], "ok": True, "urls": [{"ok": True, "ms": 300}]},
                                    {"name": "slow one", "args": ["--payload=tls_client_hello", "--x=1"], "ok": True, "urls": [{"ok": True, "ms": 900}]}]}
            if name == "zapret" and args[0] == "set":
                sets.append(payload)
                self.rules = {k: dict(v, n=i + 1) for i, (k, v) in enumerate(payload["rules"].items())}
                return {}
            return await super().call(name, *args, payload=payload, **kwargs)

    class ZapretHub(Hub):
        def _start_router(self, row):
            rt = super()._start_router(row)
            rt.agent = ZapretAgent()
            return rt

    db = DB(tmp_path / "m.db")
    db.add_router("home", "192.0.2.1", 22, "ssh-ed25519 AAAA host")
    hub = ZapretHub(db, tmp_path / "id_ed25519", ROOT)
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
            assert page.locator("#side a:has-text('Zapret')").count() == 0          # no menu of its own
            page.goto(f"{base}/#/r/1/rules/meta")
            page.wait_for_selector("text=Zapret first")
            # the switch is at the top, with the rule's other switches; its settings are in a panel
            assert page.locator(".editor .switches [aria-label='Zapret first']").count() == 1
            assert page.locator("details[data-panel=zapret] .panel-sum").inner_text() == "off"
            page.click("details[data-panel=zapret] > summary")
            assert page.locator("select[aria-label=Service]").input_value() == "meta"     # the rule's service, by its name
            assert "https://www.instagram.com/" in page.locator("textarea[aria-label='Check addresses']").input_value()
            page.click("button:has-text('Find a strategy and turn on')")
            page.wait_for_selector("text=Now: direct, through zapret", timeout=20000)
            assert "null" not in page.locator("[aria-label='Zapret first']").inner_text()
            assert checks[-1]["urls"][0] == "https://www.instagram.com/"
            assert sets[-1]["rules"] == {"meta": {"args": ["--payload=tls_client_hello"], "url": "https://www.instagram.com/",
                                                  "name": "fake md5", "service": "meta"}}
            # the other working strategy can be picked by hand
            page.click("summary:has-text('Strategies tried')")
            page.click("button:has-text('Use this')")
            page.wait_for_selector("text=Strategy: slow one")
            assert "slow one" in page.locator("details[data-panel=zapret] .panel-sum").text_content()      # (hidden while open)
            # the rules list shows it (after the next status)
            page.goto(f"{base}/#/r/1")
            page.wait_for_timeout(300)
            page.goto(f"{base}/#/r/1/rules/github")
            page.wait_for_selector(".rule .tag:has-text('zapret')", timeout=20000)
            # github has no service: "Other", no addresses until one is given
            assert page.locator("select[aria-label=Service]").input_value() == "other"
            # turning it off
            page.goto(f"{base}/#/r/1/rules/meta")
            page.wait_for_selector("text=Strategy: slow one")
            page.click("label.check:has-text('Zapret first') input")
            page.wait_for_selector("text=through its servers only")
            assert sets[-1] == {"rules": {}, "interval": 30}
            # a closed part stays closed, on other rules and after a reload; "Expand all" opens every one
            page.click("details[data-panel=matches] > summary")
            page.wait_for_function("JSON.parse(localStorage.getItem('podkop-rule-panels') || '{}').matches === false")
            page.reload()
            page.wait_for_selector("details[data-panel=matches]")
            assert not page.locator("details[data-panel=matches]").evaluate("d => d.open")
            assert page.locator("details[data-panel=zapret]").evaluate("d => d.open")
            page.click("button:has-text('Expand all')")
            assert page.locator("details.panel").evaluate_all("ds => ds.every(d => d.open)")
            page.wait_for_selector("button:has-text('Collapse all')")
            # a zapret file through Configs → Import file goes to this router, not to the saved configs
            zf = tmp_path / "zapret.json"
            zf.write_text(json.dumps({"format": "podkop-manager-zapret", "rules": {"github": {
                "args": ["--payload=tls_client_hello"], "url": "https://github.com/", "name": "from a file", "service": "other"}}}))
            page.goto(f"{base}/#/r/1/configs")
            page.wait_for_selector("text=also takes a zapret file")
            page.set_input_files("input[type=file]", str(zf))
            page.wait_for_selector("text=Turn on zapret first on home?")
            page.click("button:has-text('Turn on')")
            page.wait_for_selector("text=zapret first on github")
            assert sets[-1]["rules"]["github"] == {"args": ["--payload=tls_client_hello"], "url": "https://github.com/",
                                                   "name": "from a file", "service": "other"}
            assert page.locator("text=No saved configs").count() == 1
            # the strategy list lives in Settings
            page.goto(f"{base}/#/settings")
            page.wait_for_selector("textarea[aria-label='Zapret strategies']")
            assert page.locator("select[aria-label='Zapret check interval']").input_value() == "30"
            assert not errors, errors
            browser.close()
    finally:
        srv.should_exit = True
        t.join(timeout=10)
        zapret.POLL = 3.0
