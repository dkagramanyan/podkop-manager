"""The manager's watch over itself (selfwatch.py), the devices' DNS option and
the router's clock."""
import asyncio
import os
import time
from pathlib import Path

from podkop_manager import notify
from podkop_manager.db import DB
from podkop_manager.hub import Hub, RouterRuntime
from podkop_manager.selfwatch import SelfWatch

from test_vpn import RecordingAgent
from test_web import H, FakeRouter, client


def test_time_not_running_is_known_after_a_start(tmp_path):
    w = SelfWatch(tmp_path / "alive")
    assert w.down_for() is None                       # a first start
    (tmp_path / "alive").touch()
    assert w.down_for() is None                       # a normal restart
    os.utime(tmp_path / "alive", (time.time() - 3600,) * 2)
    assert 3590 < w.down_for() < 3700


def test_a_notification_says_how_long_the_manager_was_down(tmp_path):
    db = DB(tmp_path / "m.db")
    hub = Hub(db, tmp_path / "key", Path(__file__).resolve().parents[1])
    sent = []

    async def send(settings, message, payload=None, tries=4):
        sent.append((message, payload))
        return []
    hub.send_notification = send

    async def go():
        hub._was_down(None)
        hub._was_down(7500)                           # nobody to tell: no channel is set
        db.set_setting("notify", {**notify.DEFAULTS, "webhook_url": "http://127.0.0.1:9/x"})
        hub._was_down(None)
        hub._was_down(7500)
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert len(sent) == 1 and "not running for 2 h 5 min" in sent[0][0]
    assert sent[0][1] == {"event": "manager_started", "data": {"down_seconds": 7500}}


def test_the_loop_touches_its_file_and_a_loop_that_stands_still_is_seen(tmp_path, monkeypatch):
    pings = []
    monkeypatch.setattr("podkop_manager.selfwatch.ping", pings.append)
    w = SelfWatch(tmp_path / "alive", "http://monitor.example/ping", stall=0)     # 0: no thread that ends the process

    async def go():
        t = asyncio.create_task(w.run())
        await asyncio.sleep(0.2)
        t.cancel()
    asyncio.run(go())
    assert (tmp_path / "alive").exists() and pings == ["http://monitor.example/ping"]
    w.stall = 180
    assert not w.stalled()
    w.seen -= 200
    assert w.stalled()


def test_dns_guard_api_and_alert_text(tmp_path, monkeypatch):
    agent = RecordingAgent()
    monkeypatch.setattr(FakeRouter, "agent", agent)
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        assert c.put("/api/routers/1/dns-guard", json={"on": "yes"}, headers=H).status_code == 400
        assert c.put("/api/routers/1/dns-guard", json={"on": True}, headers=H).status_code == 200
        assert agent.calls[-1][0] == ("dns-guard", "on")
        assert c.put("/api/routers/1/dns-guard", json={"on": False}, headers=H).status_code == 200
        assert agent.calls[-1][0] == ("dns-guard", "off")
    t = notify.text("home", "group_stuck", {"section": "main", "server": "main-1-out"})
    assert "main" in t and "main-1-out" in t and "restarted" in t


def test_the_routers_clock_is_compared_with_the_managers(tmp_path):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "router", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", Path(__file__).resolve().parents[1])
    rt = RouterRuntime(db.router(rid), tmp_path / "key")

    class A:
        async def call(self, *a, **k):
            return {"agent": {"version": "0.26.0"}, "time": time.time() - 4000, "uptime": 50}
    rt.agent = A()
    hub.routers[rid] = rt
    asyncio.run(hub.refresh(rt))
    assert 3990 < rt.status["clock_off"] < 4010
