"""Changes scheduled for later: kept with the settings they were made on,
applied at their time on top of what the router has then."""
import asyncio
import copy
from pathlib import Path

import pytest

from podkop_manager import podkop
from podkop_manager.agent import AgentError
from podkop_manager.db import DB
from podkop_manager.hub import Hub, RouterRuntime
from test_web import H, client, FakeHub


def sec(name, **options):
    return {"name": name, "type": "section", "anonymous": False, "options": dict(options)}


BASE = {"sections": [{"name": "settings", "type": "settings", "anonymous": False, "options": {"dns_server": "8.8.8.8"}},
                     sec("main", connection_type="proxy", urltest_proxy_links=["vless://a@h:1#A"], user_domains=["a.com"]),
                     sec("youtube", connection_type="proxy", urltest_proxy_links=["vless://y@h:1#Y"])]}


def changed(config, name, **options):
    out = copy.deepcopy(config)
    for s in out["sections"]:
        if s["name"] == name:
            for k, v in options.items():
                if v is None:
                    s["options"].pop(k, None)
                else:
                    s["options"][k] = v
    return out


def test_rebase_keeps_the_routers_newer_settings():
    # scheduled: a domain added to main; meanwhile the nightly probe gave main and youtube new servers
    target = changed(BASE, "main", user_domains=["a.com", "b.com"])
    cur = changed(changed(BASE, "main", urltest_proxy_links=["vless://n@h:1#N"]), "youtube", urltest_proxy_links=["vless://z@h:1#Z"])
    out, conflicts = podkop.rebase(BASE, target, cur)
    assert conflicts == []
    by = podkop.sections_by_name(out)
    assert by["main"]["options"]["user_domains"] == ["a.com", "b.com"]
    assert by["main"]["options"]["urltest_proxy_links"] == ["vless://n@h:1#N"]       # the probe's, kept
    assert by["youtube"]["options"]["urltest_proxy_links"] == ["vless://z@h:1#Z"]
    assert podkop.diff(cur, out) == [{"section": "main", "option": "user_domains", "change": "changed",
                                      "old": ["a.com"], "new": ["a.com", "b.com"]}]


def test_rebase_conflicts_when_the_same_option_changed_on_the_router():
    target = changed(BASE, "main", urltest_proxy_links=["vless://mine@h:1#M"])
    cur = changed(BASE, "main", urltest_proxy_links=["vless://n@h:1#N"])
    _, conflicts = podkop.rebase(BASE, target, cur)
    assert conflicts == ["main / urltest_proxy_links: changed on the router too"]
    # the same new value on both sides is no conflict
    assert podkop.rebase(BASE, target, target)[1] == []
    # removed on the router, changed here
    gone = {"sections": [s for s in BASE["sections"] if s["name"] != "main"]}
    assert podkop.rebase(BASE, target, gone)[1] == ["main: changed here, but removed on the router"]


def test_rebase_adds_removes_and_orders_sections():
    added = copy.deepcopy(BASE)
    added["sections"].insert(2, sec("work", connection_type="vpn", interface="wg0"))
    cur = changed(BASE, "youtube", urltest_proxy_links=["vless://z@h:1#Z"])
    out, conflicts = podkop.rebase(BASE, added, cur)
    assert conflicts == [] and [s["name"] for s in out["sections"]] == ["settings", "main", "work", "youtube"]
    removed = {"sections": [s for s in BASE["sections"] if s["name"] != "youtube"]}
    assert podkop.rebase(BASE, removed, cur)[1] == ["youtube: removed here, but changed on the router"]
    out, conflicts = podkop.rebase(BASE, removed, changed(BASE, "main", user_domains=["c.com"]))
    assert conflicts == [] and [s["name"] for s in out["sections"]] == ["settings", "main"]
    moved = copy.deepcopy(BASE)
    moved["sections"] = [moved["sections"][0], moved["sections"][2], moved["sections"][1]]
    out, conflicts = podkop.rebase(BASE, moved, cur)
    assert conflicts == [] and [s["name"] for s in out["sections"]] == ["settings", "youtube", "main"]


class Agent:
    def __init__(self, config, fp="fp1"):
        self.config, self.fp, self.applied, self.away = copy.deepcopy(config), fp, [], False

    async def call(self, name, *args, payload=None, **kwargs):
        if self.away:
            raise AgentError("unreachable", "cannot connect to router: test")
        if name == "config-get":
            return {"config": copy.deepcopy(self.config), "fingerprint": self.fp}
        if name == "apply":
            self.applied.append(payload)
            return {"job": f"j{len(self.applied)}"}
        if name == "job":
            return {"state": "applied"}
        raise AssertionError(name)


def _hub(tmp_path):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "router", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", Path(__file__).resolve().parents[1])
    rt = RouterRuntime(db.router(rid), tmp_path / "key")
    rt.agent = Agent(BASE)
    hub.routers[rid] = rt
    return db, hub, rt, rid


def test_scheduled_change_is_applied_at_its_time_on_the_routers_settings_then(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["a.com", "b.com"])
    db.set_draft(rid, target, "fp1")

    async def go():
        with pytest.raises(AgentError, match="changed on the router"):
            await hub.schedule(rid, "old-fp", target, "", 2e9)
        vid = await hub.schedule(rid, "fp1", target, "more domains", 2e9)
        assert db.draft(rid) is None                          # the change is in History now
        assert [(s["id"], s["changes"]) for s in rt.scheduled] == [(vid, 1)]
        await hub._run_due()
        assert rt.agent.applied == [] and db.version(vid)["state"] == "scheduled"     # not yet
        # meanwhile the nightly probe changed main's servers on the router
        rt.agent.config = changed(BASE, "main", urltest_proxy_links=["vless://n@h:1#N"])
        rt.agent.fp = "fp2"
        db.x("UPDATE versions SET due = 1 WHERE id = ?", vid)
        await hub._run_due()
        sent = rt.agent.applied[-1]
        assert sent["base"] == "fp2"
        main = podkop.sections_by_name(sent["config"])["main"]["options"]
        assert main["user_domains"] == ["a.com", "b.com"] and main["urltest_proxy_links"] == ["vless://n@h:1#N"]
        v = db.version(vid)
        assert v["state"] in ("running", "applied") and v["fingerprint_before"] == "fp2"
        assert rt.scheduled == []
        for t in list(hub.jobs):
            await t
        assert db.version(vid)["state"] == "applied"
    asyncio.run(go())


def test_scheduled_change_waits_for_an_away_router_then_fails(tmp_path, monkeypatch):
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["x.com"])

    async def go():
        vid = await hub.schedule(rid, "fp1", target, "", 2e9)
        db.x("UPDATE versions SET due = ? WHERE id = ?", __import__("time").time() - 10, vid)
        rt.agent.away = True
        await hub._run_due()
        assert db.version(vid)["state"] == "scheduled"           # tried again in 30 s
        db.x("UPDATE versions SET due = 1 WHERE id = ?", vid)    # an hour and more ago
        await hub._run_due()
        v = db.version(vid)
        assert v["state"] == "failed" and "cannot connect" in v["message"]
        assert db.events(rid)[0]["type"] == "schedule_failed"
    asyncio.run(go())


def test_conflicting_scheduled_change_is_not_applied(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", urltest_proxy_links=["vless://mine@h:1#M"])

    async def go():
        vid = await hub.schedule(rid, "fp1", target, "", 2e9)
        rt.agent.config, rt.agent.fp = changed(BASE, "main", urltest_proxy_links=["vless://n@h:1#N"]), "fp2"
        assert (await hub.run_scheduled(vid))["state"] == "rejected"
        assert rt.agent.applied == []
        assert "urltest_proxy_links: changed on the router too" in db.version(vid)["message"]
        assert db.events(rid)[0]["type"] == "schedule_failed"
        assert rt.alert["type"] == "schedule_failed"              # the corner alert at once
    asyncio.run(go())


def test_cancel_and_back_to_draft(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["x.com"])

    async def go():
        a = await hub.schedule(rid, "fp1", target, "", 2e9)
        b = await hub.schedule(rid, "fp1", changed(BASE, "youtube", user_domains=["y.com"]), "", 2e9 + 60)
        await hub.cancel_scheduled(a)
        assert db.version(a)["state"] == "cancelled" and [s["id"] for s in rt.scheduled] == [b]
        rt.agent.config, rt.agent.fp = changed(BASE, "main", urltest_proxy_links=["vless://n@h:1#N"]), "fp2"
        await hub.cancel_scheduled(b, to_draft=True)
        d = db.draft(rid)
        assert d["base"] == "fp2"                                 # made on the router's settings now
        by = podkop.sections_by_name(d["config"])
        assert by["youtube"]["options"]["user_domains"] == ["y.com"]
        assert by["main"]["options"]["urltest_proxy_links"] == ["vless://n@h:1#N"]
        c = await hub.schedule(rid, "fp2", changed(rt.agent.config, "main", user_domains=["z.com"]), "", 2e9)
        with pytest.raises(AgentError, match="draft already"):
            await hub.cancel_scheduled(c, to_draft=True)
        with pytest.raises(AgentError, match="not scheduled"):
            await hub.cancel_scheduled(a)
    asyncio.run(go())


def test_schedule_api(tmp_path, monkeypatch):
    got = []

    async def schedule(self, rid, base, config, note, due, trigger="web"):
        got.append((rid, base, note, due, trigger))
        return 7

    monkeypatch.setattr(FakeHub, "schedule", schedule, raising=False)
    import time
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        cfg = {"sections": []}
        for due in (None, "x", time.time(), time.time() + 400 * 86400):
            r = c.post("/api/routers/1/schedule", json={"base": "fp1", "config": cfg, "due": due}, headers=H)
            assert r.status_code == 400, due
        due = time.time() + 3600
        r = c.post("/api/routers/1/schedule", json={"base": "fp1", "config": cfg, "due": due, "note": "n"}, headers=H)
        assert r.json() == {"version": 7, "due": due} and got == [(1, "fp1", "n", due, "web")]
        assert c.post("/api/versions/99/cancel", json={}, headers=H).status_code == 404
        assert c.post("/api/versions/99/run", headers=H).status_code == 404


def test_prune_keeps_scheduled_changes(tmp_path):
    db = DB(tmp_path / "m.db")
    vid = db.add_version(1, BASE, [], "", "fp", due=2e9, base_config=BASE)
    for _ in range(5):
        db.add_version(1, BASE, [], "", "fp")
    db.prune(versions=2)
    assert db.version(vid)["state"] == "scheduled" and len(db.versions(1)) == 3
    assert db.version(vid)["base_config"] == BASE


def test_a_watchdog_that_stands_still_on_the_router_is_reported_once(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    types = lambda: [e["type"] for e in db.events(rid, 10)]  # noqa: E731
    rt.status = {"uptime": 9000, "daemon": {"beat": 20}}
    hub._check_daemon(rt)
    assert rt.alert is None and "agent_stuck" not in types()
    rt.status = {"uptime": 9000, "daemon": {"beat": 86000}}
    hub._check_daemon(rt)
    hub._check_daemon(rt)
    assert rt.alert["type"] == "agent_stuck" and types().count("agent_stuck") == 1
    rt.status = {"uptime": 9000, "daemon": {"beat": 3}}
    hub._check_daemon(rt)
    assert rt.alert is None and "agent_ok" in types()
    rt.status = {"uptime": 60, "daemon": {"beat": None}}          # just booted: the daemon has not started yet
    hub._check_daemon(rt)
    rt.status = {"uptime": 9000}                                  # an older agent says nothing
    hub._check_daemon(rt)
    assert types().count("agent_stuck") == 1


def test_status_is_asked_less_often_while_nobody_looks(tmp_path):
    from podkop_manager import hub as hubmod
    db, hub, rt, rid = _hub(tmp_path)
    assert hub.poll_seconds() == hubmod.POLL_IDLE_SECONDS
    rt.checked = 0

    async def go():
        q = hub.subscribe()
        assert rt.wake.is_set() and hub.poll_seconds() == hubmod.POLL_SECONDS      # the first viewer: fresh at once
        hub.unsubscribe(q)
        assert hub.poll_seconds() == hubmod.POLL_IDLE_SECONDS
    asyncio.run(go())


def test_connection_checks_slow_down_only_while_nothing_is_wrong():
    from podkop_manager import reliability
    ok = [{"target": "a", "exit": 0, "code": 204}, {"target": "b", "exit": 0, "code": 204}]
    bad = [{"target": "a", "exit": 28, "code": 0}, {"target": "b", "exit": 28, "code": 0}]
    report = {"nodes": {"main-1-out": ok}, "direct": ok, "router_dns": True, "primary_dns": {"ok": True}, "payload": {}}
    assert reliability.calm(report)
    assert not reliability.calm(dict(report, nodes={"main-1-out": ok, "main-2-out": bad}))
    assert not reliability.calm(dict(report, router_dns=False))
    assert not reliability.calm(dict(report, direct=bad))
    assert not reliability.calm(dict(report, banned=["main-2-out"]))
    assert not reliability.calm(dict(report, nodes={}))


def test_update_dialog_says_whether_another_router_ran_this_agent(tmp_path):
    import re
    import time
    db, hub, rt, rid = _hub(tmp_path)
    version = re.search(r'^AGENT_VERSION="([^"]+)"', (hub.src / "router/podkop-agent").read_text(), re.M).group(1)
    assert hub.agent_tried(rid) == {"version": version, "routers": []}
    spare = db.add_router("spare", "spare", 22, "ssh-ed25519 BBBB")
    other = RouterRuntime(db.router(spare), tmp_path / "key")
    other.status = {"agent": {"version": version}}
    hub.routers[spare] = other
    db.add_event(spare, "agent_started", {"version": version})
    db.x("UPDATE events SET t = ? WHERE router_id = ?", time.time() - 30 * 3600, spare)
    assert hub.agent_tried(rid)["routers"] == [{"name": "spare", "hours": 30.0, "trouble": False}]
    db.add_event(spare, "agent_stuck", {})
    assert hub.agent_tried(rid)["routers"][0]["trouble"] is True
    other.status = {"agent": {"version": "0.1.0"}}             # runs something else: says nothing
    assert hub.agent_tried(rid)["routers"] == []


def test_safe_mode_is_an_alert_until_it_is_left(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    hub._on_message(rt, {"type": "safe_mode", "reason": "main did not answer", "trial": True})
    assert rt.alert["type"] == "safe_mode"
    hub._on_message(rt, {"type": "safe_mode_off", "reason": "switched off"})
    assert rt.alert is None
