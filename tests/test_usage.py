import time
from datetime import datetime
from pathlib import Path

import pytest

from podkop_manager import podkop, usage
from podkop_manager.db import DB
from podkop_manager.hub import Hub

GB = usage.GB


def ROW(link, median):
    return {"node": link.split("#")[1], "link": link, "verdict": "GOOD", "median": median, "mbit": "90"}


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def ts(s: str) -> float:
    return datetime.fromisoformat(s + "+00:00").timestamp()


def key(**kw):
    k = {"id": 1, "kind": "sub", "url": "https://sub.example.com/abc", "label": "", "title": "", "why": "",
         "links": ["vless://a@h:1#A", "vless://b@h:2#B"], "limit_source": "provider", "limit_gb": 0,
         "period": "", "reset_time": "00:00", "action": "warn", "threshold": 90, "acted_until": None}
    k.update(kw)
    return k


def test_parse_headers():
    assert usage.parse_userinfo("upload=1073741824; download=2147483648; total=32212254720; expire=1795000000") == {
        "used": 3 * GB, "total": 30 * GB, "expire": 1795000000}
    assert usage.parse_userinfo("") is None
    assert usage.parse_title("base64:0J/RgNC+0LLQsNC50LTQtdGAINCQ") == "Провайдер А"
    assert usage.parse_title("plain") == "plain"


def test_last_reset():
    assert usage.last_reset(ts("2026-10-18T13:30"), "00:00") == ts("2026-10-18T00:00")
    assert usage.last_reset(ts("2026-10-18T02:00"), "03:00") == ts("2026-10-17T03:00")


def test_provider_daily_limit_and_forecast():
    now = ts("2026-10-18T12:00")
    # the provider's counter dropped at 00:10 yesterday and today: it resets daily
    samples = [{"t": ts("2026-10-17T00:00"), "used": 28 * GB, "total": 30 * GB, "expire": None},
               {"t": ts("2026-10-17T00:10"), "used": 0, "total": 30 * GB, "expire": None},
               {"t": ts("2026-10-17T23:50"), "used": 30 * GB, "total": 30 * GB, "expire": None},
               {"t": ts("2026-10-18T00:10"), "used": 0, "total": 30 * GB, "expire": None},
               {"t": ts("2026-10-18T09:00"), "used": 9 * GB, "total": 30 * GB, "expire": None},
               {"t": ts("2026-10-18T12:00"), "used": 21 * GB, "total": 30 * GB, "expire": None}]
    u = usage.key_usage(key(), samples, [], now)
    assert u["daily"] and u["limit_source"] == "provider" and u["reset"] == "00:00"
    assert u["used"] == 21 * GB and u["limit"] == 30 * GB and u["pct"] == 70.0
    # 12 GB in the last 3 hours: 9 GB left lasts until 14:15, before the reset
    assert u["forecast"]["hits_today"] and round(u["forecast"]["eta"]) == round(ts("2026-10-18T14:15"))
    assert [d["hit"] for d in u["days"]][-2:] == [True, False]
    assert not usage.over_threshold(u)


def test_single_link_counted_on_the_router():
    now = ts("2026-10-18T12:00")
    hour = int(ts("2026-10-18T10:00"))
    traffic = [{"hour": hour, "key_id": 2, "rule": "main", "server": "vless://x@h:1#X", "bytes": 5 * GB},
               {"hour": hour, "key_id": 2, "rule": "netflix", "server": "vless://x@h:1#X", "bytes": 4 * GB},
               {"hour": int(ts("2026-10-17T10:00")), "key_id": 2, "rule": "main", "server": "vless://x@h:1#X",
                "bytes": 7 * GB}]
    k = key(id=2, kind="link", url="vless://x@h:1#X", links=[], limit_source="manual", limit_gb=10)
    u = usage.key_usage(k, [], traffic, now)
    assert u["label"] == "X" and u["daily"] and u["used"] == 9 * GB and u["pct"] == 90.0
    assert usage.over_threshold(u)
    assert [r["rule"] for r in u["by_rule"]] == ["main", "netflix"]
    assert [d["bytes"] for d in u["days"]][-2:] == [7 * GB, 9 * GB]
    s = usage.servers_today(traffic, {2: "fra"}, now)
    assert [(x["name"], x["key"], x["rule"], x["bytes"]) for x in s] == [("X", "fra", "main", 5 * GB),
                                                                          ("X", "fra", "netflix", 4 * GB)]


def test_link_index():
    keys = [key(), key(id=2, kind="link", url="vless://x@h:1#X", links=[])]
    assert usage.link_index(keys) == {"vless://a@h:1": 1, "vless://b@h:2": 1, "vless://x@h:1": 2}


def test_probe_config_moves_away_from_avoided_keys(tmp_path: Path):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    db.set_probe_settings(rid, keys=["https://sub.example.com/abc"])
    db.sync_sub_keys(rid, ["https://sub.example.com/abc"])
    kid = db.keys(rid)[0]["id"]
    db.update_key(kid, links=["vless://a@h:1#A"], action="avoid", acted_until=time.time() + 3600)
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 1, "fallback": 1}))
    hub = Hub(db, tmp_path / "key", tmp_path)
    cur = {"config": {"sections": [{"name": "main", "type": "section", "options": {"connection_type": "proxy"}}]}}
    status = {"rows": [ROW("vless://a@h:1#A", "0.2"), ROW("vless://c@h:3#C", "0.3")]}
    main = hub.probe_config(rid, cur, status)["sections"][0]["options"]
    assert main["urltest_proxy_links"] == ["vless://c@h:3#C"] and "urltest_fallback_links" not in main
    db.update_key(kid, acted_until=time.time() - 1)
    main = hub.probe_config(rid, cur, status)["sections"][0]["options"]
    assert main["urltest_proxy_links"] == ["vless://a@h:1#A"] and main["urltest_fallback_links"] == ["vless://c@h:3#C"]


def test_move_rules_touches_only_rules_with_the_keys_servers(tmp_path: Path):
    import asyncio
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    db.set_probe_settings(rid, keys=["https://sub.example.com/abc"])
    db.sync_sub_keys(rid, ["https://sub.example.com/abc"])
    kid = db.keys(rid)[0]["id"]
    db.update_key(kid, links=["vless://a@h:1#A"], action="avoid", acted_until=time.time() + 3600)
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 2, "fallback": 0}))
    db.set_auto_rule(rid, "youtube", podkop.check_auto_rule({"top": 1, "fallback": 0, "name_contains": "Y"}))
    run = db.add_probe_run(rid, "manual", [])
    db.finish_probe_run(run, "done", {"rows": [ROW("vless://a@h:1#A", "0.2"), ROW("vless://c@h:3#C", "0.3"),
                                               ROW("vless://y@h:4#Y", "0.4")]})
    sec = lambda n, links: {"name": n, "type": "section", "options": {
        "connection_type": "proxy", "proxy_config_type": "urltest", "urltest_proxy_links": links}}
    cur = {"fingerprint": "fp", "config": {"sections": [sec("youtube", ["vless://old@h:9#O"]),
                                                        sec("main", ["vless://a@h:1#A"])]}}
    hub = Hub(db, tmp_path / "key", tmp_path)
    applied = []

    async def config(_rid):
        return cur

    async def apply(_rid, base, config, note, trigger):
        applied.append((config, note, trigger))
        return {"version": 1}

    states = iter(["applied", "applied", "rejected"])

    async def wait_version(vid):
        return {"state": next(states), "message": "podkop's settings changed since you loaded them"}

    hub.config, hub.apply, hub.wait_version = config, apply, wait_version
    rt = type("RT", (), {"id": rid})()
    assert asyncio.run(hub._move_rules(rt, ["provider-a"]))
    new, note, trigger = applied[0]
    by = {s["name"]: s["options"]["urltest_proxy_links"] for s in new["sections"]}
    assert by == {"youtube": ["vless://old@h:9#O"], "main": ["vless://c@h:3#C", "vless://y@h:4#Y"]}   # youtube had none
    assert trigger == "usage" and "provider-a" in note
    # after the reset: main gets the probe's list back, youtube still untouched
    db.update_key(kid, acted_until=time.time() - 1)
    cur["config"] = new
    asyncio.run(hub._move_rules(rt, []))
    by = {s["name"]: s["options"]["urltest_proxy_links"] for s in applied[1][0]["sections"]}
    assert by == {"youtube": ["vless://old@h:9#O"], "main": ["vless://a@h:1#A", "vless://c@h:3#C"]}
    # a move the router did not take is not done: it is tried again
    db.update_key(kid, acted_until=time.time() + 3600)
    cur["config"] = applied[1][0]
    assert not asyncio.run(hub._move_rules(rt, ["provider-a"]))


def test_old_probe_lists_become_rules_once(tmp_path: Path):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    db.set_probe_settings(rid, keys=["https://s"], options=["--countries", "europe", "--max-ping", "800", "--top", "7"])
    hub = Hub(db, tmp_path / "key", tmp_path)
    hub._lists_to_rules(rid)
    rules = {r["section"]: r for r in db.auto_rules(rid)}
    assert (rules["main"]["top"], rules["main"]["max_ping"], rules["main"]["youtube"]) == (7, 800, "")
    assert rules["youtube"]["youtube"] == "RU"
    assert hub.probe_options(rid) == ["--countries", "all", "--no-entry-check", "--max-ping", "800", "--min-speed", "40"]
    hub._countries_to_rules(rid)                  # the probe page's "europe" moves into the rules
    assert db.auto_rule(rid, "main")["countries"] == sorted(podkop.EUROPE)
    assert db.probe_settings(rid)["options"] == []
    assert hub.probe_options(rid)[:2] == ["--countries", ",".join(sorted(podkop.EUROPE))]
    db.delete_auto_rule(rid, "youtube")
    db.set_auto_rule(rid, "netflix", podkop.check_auto_rule({"countries": []}))
    hub._lists_to_rules(rid)                      # only once
    hub._countries_to_rules(rid)
    assert [r["section"] for r in db.auto_rules(rid)] == ["main", "netflix"]
    assert hub.probe_options(rid)[:2] == ["--countries", "all"]      # netflix takes any country
    assert hub.probe_options(rid)[-1] == "--no-youtube"
    fresh = db.add_router("new", "192.168.1.2", 22, "ssh-ed25519 AAAA")
    hub._lists_to_rules(fresh)                    # never used the probe: no rules
    assert db.auto_rules(fresh) == []


def test_reading_from_before_the_reset_is_not_today():
    # the provider resets at 00:10; at 00:12 the newest reading is still yesterday's 23:55 one
    samples = [{"t": ts("2026-10-17T00:05"), "used": 29 * GB, "total": 30 * GB, "expire": None},
               {"t": ts("2026-10-17T00:15"), "used": 0, "total": 30 * GB, "expire": None},
               {"t": ts("2026-10-17T23:55"), "used": 29 * GB, "total": 30 * GB, "expire": None}]
    u = usage.key_usage(key(), samples, [], ts("2026-10-18T00:12"))
    assert u["daily"] and u["used"] == 0 and not usage.over_threshold(u)


def test_router_counts_the_current_hour():
    hour = int(ts("2026-10-18T12:00"))
    traffic = [{"hour": hour, "key_id": 2, "rule": "main", "server": "vless://x@h:1#X", "bytes": 3 * GB}]
    k = key(id=2, kind="link", url="vless://x@h:1#X", links=[], limit_source="manual", limit_gb=10)
    assert usage.key_usage(k, [], traffic, ts("2026-10-18T12:20"))["used"] == 3 * GB



def test_restart_follows_unfinished_changes_and_probe_runs(tmp_path: Path):
    import asyncio
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    sent = db.add_version(rid, {"sections": []}, [], "x", "fp")
    db.update_version(sent, job="j1", state="running")
    lost = db.add_version(rid, {"sections": []}, [], "y", "fp")         # never got to the router
    run = db.add_probe_run(rid, "schedule")
    hub = Hub(db, tmp_path / "key", tmp_path)
    followed = []

    async def track_job(rt, vid, job):
        followed.append(("job", vid, job))

    async def track_probe(rt, run_id, trigger):
        followed.append(("probe", run_id, trigger))

    async def main():
        hub._track_job, hub._track_probe = track_job, track_probe
        hub._poll_loop = hub._watch_loop = hub._usage_loop = lambda rt: asyncio.sleep(3600)
        await hub.start()
        await asyncio.sleep(0)
        await hub.stop()

    asyncio.run(main())
    assert sorted(followed) == [("job", sent, "j1"), ("probe", run, "schedule")]
    assert db.version(lost)["state"] == "unknown"


def test_traffic_is_credited_to_links_counted_by_the_agent(tmp_path: Path):
    import asyncio
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", tmp_path)
    readings = iter([{"since": "s", "servers": {"main-1-out": 100}, "links": {"main\tvless://a@h:1#A": 100}},
                     {"since": "s", "servers": {"main-1-out": 100 + 50},
                      "links": {"main\tvless://a@h:1#A": 130, "main\tvless://b@h:2#B": 20}}])

    class Agent:
        async def call(self, *a, **kw):
            return next(readings)

    # the config now has B first: the tag main-1-out is B, but the agent says where the bytes went
    rt = type("RT", (), {"id": rid, "agent": Agent(), "config": {"config": {"sections": [
        {"name": "main", "type": "section", "options": {"proxy_config_type": "urltest",
                                                         "urltest_proxy_links": ["vless://b@h:2#B", "vless://a@h:1#A"]}}]}}})()
    asyncio.run(hub.read_traffic(rt))
    asyncio.run(hub.read_traffic(rt))
    got = {r["server"]: r["bytes"] for r in db.traffic(rid, 0)}
    assert got == {"vless://a@h:1#A": 30, "vless://b@h:2#B": 20}


def test_fixed_fingerprint_still_matches_its_key_and_reaches_the_probe(tmp_path: Path):
    import asyncio
    assert usage.norm("vless://a@h:1?security=reality&fp=chrome&pbk=x#A") == usage.norm("vless://a@h:1?security=reality&fp=qq&pbk=x#B")
    assert usage.norm("vless://a@h:1?fp=qq") == "vless://a@h:1"
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    db.set_probe_settings(rid, keys=["https://s"], fix_fingerprint=1)
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 1, "fallback": 0}))
    hub = Hub(db, tmp_path / "key", tmp_path)
    link = "vless://a@h:1?security=reality&fp=qq#A"
    picks = hub.auto_picks(rid, {"rows": [ROW(link, "0.2")]})
    assert picks["main"]["links"] == ["vless://a@h:1?security=reality&fp=chrome#A"]
    assert picks["main"]["matched"][0]["link"].endswith("fp=chrome#A")
    sent = []

    class Agent:
        async def call(self, cmd, payload=None, timeout=0):
            sent.append(payload["options"])
            return {}

    for version in ("0.3.0", "0.3.1"):
        rt = type("RT", (), {"id": rid, "agent": Agent(), "status": {"agent": {"version": version}}})()
        hub.routers = {rid: rt}
        hub.probes = {}
        hub._track_probe = lambda *a: asyncio.sleep(0)
        asyncio.run(hub.start_probe(rid))
    assert ["--fix-fingerprint" in o for o in sent] == [False, True]      # only agents that allow it


def test_adding_a_running_router_imports_and_changes_nothing(tmp_path: Path):
    import asyncio
    db = DB(tmp_path / "m.db")
    rid = db.add_router("prod", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", tmp_path)
    cfg = {"sections": [{"name": "settings", "type": "settings", "options": {"shutdown_correctly": "0"}},
                        {"name": "youtube", "type": "section", "options": {"connection_type": "proxy"}},
                        {"name": "main", "type": "section", "options": {"connection_type": "proxy"}}]}
    calls = []

    class Agent:
        async def call(self, cmd, *args, payload=None, timeout=0):
            calls.append((cmd,) + args)
            return {"status": {"podkop": {"version": "0.7.21", "running": True}, "fingerprint": "fp", "probe": {
                        "cron": "30 4 * * * podkop-probe --cron"}},
                    "config-get": {"fingerprint": "fp", "config": cfg},
                    "probe-config": {"saved": True, "keys": ["https://a.example/s/1", "https://b.example/2", "junk"],
                                     "countries": "europe", "top": 10, "section": "main", "youtube": True,
                                     "youtube_noads": "RU", "youtube_section": "youtube", "apply_to": "both",
                                     "max_ping": 800, "min_speed": 40, "cron": "30 4 * * * podkop-probe --cron"},
                    "probe-cron": {"cron": ""}}.get(cmd)

    from podkop_manager.hub import RouterRuntime
    r = RouterRuntime(db.router(rid), tmp_path / "key")
    r.agent = Agent()
    hub.routers = {rid: r}
    asyncio.run(hub.import_found(rid))
    assert {c[0] for c in calls} == {"status", "config-get", "proxies", "probe-config"}      # only reads
    [saved] = db.configs()
    assert saved["name"].startswith("prod, as found") and saved["podkop"] == "0.7.21"
    assert "shutdown_correctly" not in saved["config"]["sections"][0]["options"]
    assert db.probe_settings(rid)["keys"] == ["https://a.example/s/1", "https://b.example/2"]
    rules = {x["section"]: x for x in db.auto_rules(rid)}
    assert (rules["main"]["max_ping"], rules["main"]["youtube"], len(rules["main"]["countries"])) == (800, "", len(podkop.EUROPE))
    assert rules["youtube"]["youtube"] == "RU"
    assert db.probe_settings(rid)["schedule"] == ""            # the router's own cron still runs it
    asyncio.run(hub.import_found(rid))                          # once
    assert len(db.configs()) == 1
    # "Let the manager run it": the cron line goes, the manager runs it at that time
    asyncio.run(hub.take_over_probe(r))
    ps = db.probe_settings(rid)
    assert calls[-1] == ("probe-cron", "off") and (ps["schedule"], ps["auto_apply"]) == ("04:30", 1)


def test_exit_ip_is_looked_up_when_the_server_changes(tmp_path: Path):
    import asyncio
    from podkop_manager.agent import AgentError
    from podkop_manager.hub import RouterRuntime
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", tmp_path)
    calls = []

    class Agent:
        async def call(self, cmd, section, timeout=0):
            calls.append(section)
            if section == "work":
                raise AgentError("exit_unknown", "no answer from ipinfo.io through wg0")
            return {"section": section, "server": now["main"], "ip": "203.0.113.5", "country": "DE", "city": "", "org": ""}

    rt = RouterRuntime(db.router(rid), tmp_path / "key")
    rt.agent = Agent()
    rt.config = {"config": {"sections": [
        {"name": "settings", "type": "settings", "options": {}},
        {"name": "main", "type": "section", "options": {"connection_type": "proxy", "proxy_config_type": "urltest"}},
        {"name": "work", "type": "section", "options": {"connection_type": "vpn", "interface": "wg0"}},
        {"name": "ru", "type": "section", "options": {"connection_type": "exclusion"}}]}}
    now = {"main": "main-2-out"}

    def poll():
        rt.proxies = {"groups": [{"name": "main-out", "now": "main-urltest-out"},
                                 {"name": "main-urltest-out", "now": now["main"]}]}
        hub._check_exits(rt)

    async def run(steps):
        for step in steps:
            step()
            if rt.exit_task:
                await rt.exit_task

    asyncio.run(run([poll]))
    assert calls == ["main", "work"]
    assert rt.exits["main"]["ip"] == "203.0.113.5" and rt.exits["work"]["error"].startswith("no answer")
    asyncio.run(run([poll]))                       # nothing changed: no lookup
    assert calls == ["main", "work"]
    now["main"] = "main-1-out"                     # urltest moved: only that section again
    asyncio.run(run([poll]))
    assert calls == ["main", "work", "main"]


def test_router_time_on_the_manager_clock(monkeypatch):
    from podkop_manager import hub as hub_mod

    class LT:
        tm_gmtoff = 3 * 3600                      # the manager runs on Moscow time
    monkeypatch.setattr(hub_mod.time, "localtime", lambda *a: LT())
    assert hub_mod.manager_minutes(4 * 60 + 30, "+0000") == 7 * 60 + 30     # a router on UTC
    assert hub_mod.manager_minutes(4 * 60 + 30, "+0300") == 4 * 60 + 30
    assert hub_mod.manager_minutes(23 * 60, "-0500") == 7 * 60             # the next day
    assert hub_mod.manager_minutes(4 * 60 + 30, "") == 4 * 60 + 30          # unknown: as is


def test_take_over_refuses_a_cron_line_without_one_time(tmp_path: Path):
    import asyncio
    from podkop_manager.agent import AgentError
    from podkop_manager.hub import RouterRuntime
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", tmp_path)
    calls = []

    class Agent:
        async def call(self, cmd, *args, payload=None, timeout=0):
            calls.append(cmd)
            return {"keys": ["https://a.example/s/1"]}

    r = RouterRuntime(db.router(rid), tmp_path / "key")
    r.agent = Agent()
    r.status = {"probe": {"cron": "*/30 4 * * * podkop-probe --cron"}}
    with pytest.raises(AgentError, match="no single daily time"):
        asyncio.run(hub.take_over_probe(r))
    assert calls == []                                   # the router's run is left on
    r.status = {"probe": {"cron": "30 4 * * * podkop-probe --cron"}}   # no keys in the manager yet
    asyncio.run(hub.take_over_probe(r))
    assert calls == ["probe-config", "probe-cron"] and db.probe_settings(rid)["keys"] == ["https://a.example/s/1"]


@pytest.mark.parametrize("noads, youtube", [("RU", "RU"), ("ru by", "RU"), ("", "plays")])
def test_youtube_region_from_the_routers_probe(tmp_path: Path, noads, youtube):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", tmp_path)
    cfg = {"sections": [{"name": "youtube", "type": "section", "options": {}}]}
    made = hub._probe_rules_from(rid, cfg, {"youtube_section": "youtube", "youtube": True, "apply_to": "both",
                                            "youtube_noads": noads})
    assert made == ["youtube"] and db.auto_rule(rid, "youtube")["youtube"] == youtube


def test_rules_never_get_servers_of_keys_moved_away_from(tmp_path: Path):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    db.set_probe_settings(rid, keys=["https://sub.example.com/abc"])
    db.sync_sub_keys(rid, ["https://sub.example.com/abc"])
    kid = db.keys(rid)[0]["id"]
    db.update_key(kid, links=["vless://a@h:1#A"], action="avoid", acted_until=time.time() + 3600)
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 2, "fallback": 0}))
    hub = Hub(db, tmp_path / "key", tmp_path)
    st = {"rows": [ROW("vless://a@h:1#A", "0.2"), ROW("vless://c@h:3#C", "0.3")]}
    assert hub.auto_picks(rid, st)["main"]["links"] == ["vless://c@h:3#C"]     # what the rule's preview shows too


def test_probe_rule_takes_servers_only_of_its_keys(tmp_path: Path):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    subs = ["https://gas.example.com/abc", "https://free.example.com/def"]
    db.set_probe_settings(rid, keys=subs)
    db.sync_sub_keys(rid, subs)
    gas, free = (k["id"] for k in db.keys(rid))
    db.update_key(gas, links=["vless://a@h:1#A"])
    db.update_key(free, links=["vless://b@h:2#B"])
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 2, "fallback": 0}))
    db.set_rule_keys(rid, "main", [gas])
    hub = Hub(db, tmp_path / "key", tmp_path)
    st = {"rows": [ROW("vless://b@h:2#B", "0.1"), ROW("vless://a@h:1&fp=qq#A", "0.2"), ROW("vless://c@h:3#C", "0.3")]}
    p = hub.auto_picks(rid, st)["main"]
    assert p["links"] == ["vless://a@h:1&fp=qq#A"] and p["left_out"] == []    # other keys' servers are not "left out"
    assert hub.key_rule_links(rid) == {}                                        # the probe's rule: not synced by keys
    db.delete_key(gas)                                                          # its key gone: every key's servers again
    assert hub.auto_picks(rid, st)["main"]["links"] == ["vless://b@h:2#B", "vless://a@h:1&fp=qq#A"]


def test_keys_of_1_28_move_out_of_the_probe_rule(tmp_path: Path):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"keys": [3]}))      # a 1.28.0 config: accepted, dropped
    db.x("UPDATE auto_rules SET keys = '[3, 4]' WHERE section = 'main'")
    db = DB(tmp_path / "m.db")
    assert db.rule_keys(rid) == {"main": [3, 4]} and "keys" not in db.auto_rule(rid, "main")


def test_rule_takes_every_server_of_its_keys_and_follows_them(tmp_path: Path):
    import asyncio
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    subs = ["https://gas.example.com/abc", "https://free.example.com/def"]
    db.set_probe_settings(rid, keys=subs)
    db.sync_sub_keys(rid, subs)
    gas, free = (k["id"] for k in db.keys(rid))
    db.update_key(gas, label="Gas VPN", links=["vless://a@h:1#A", "vmess://x", "trojan://b@h:2#B"])
    db.update_key(free, links=["vless://a@h:1#A2", "vless://f@h:5#F"])
    db.set_rule_keys(rid, "main", [gas, free])
    db.set_rule_keys(rid, "vpn", [gas])
    hub = Hub(db, tmp_path / "key", tmp_path)
    # each server once, only what podkop can build
    assert hub.key_rule_links(rid)["main"] == ["vless://a@h:1#A", "trojan://b@h:2#B", "vless://f@h:5#F"]

    sec = lambda n, o: {"name": n, "type": "section", "options": o}
    cur = {"fingerprint": "fp", "config": {"sections": [
        sec("main", {"connection_type": "proxy", "proxy_config_type": "urltest", "urltest_proxy_links": ["vless://f@h:5#F"]}),
        sec("vpn", {"connection_type": "vpn", "interface": "wg0"})]}}
    db.set_draft(rid, cur["config"], "fp")
    applied = []

    async def config(_rid):
        return cur

    async def apply(_rid, base, config, note, trigger):
        applied.append((config, note, trigger))
        return {"version": 1}

    hub.config, hub.apply, hub.update_draft = config, apply, lambda rt: None
    rt = type("RT", (), {"id": rid, "snapshot": lambda self: {}})()
    asyncio.run(hub.sync_key_rules(rt))
    (new, note, trigger), = applied
    main = podkop.sections_by_name(new)["main"]["options"]
    assert main["urltest_proxy_links"] == ["vless://a@h:1#A", "trojan://b@h:2#B", "vless://f@h:5#F"]
    assert trigger == "keys" and "Gas VPN" in note and "vpn" not in note        # vpn is not a URLTest rule
    assert podkop.sections_by_name(db.draft(rid)["config"])["main"]["options"]["urltest_proxy_links"] == main["urltest_proxy_links"]
    # the same list again (also when the router did not take it): nothing more
    asyncio.run(hub.sync_key_rules(rt))
    assert len(applied) == 1
    # the provider dropped a server: applied once more
    db.update_key(gas, links=["vless://a@h:1#A"])
    asyncio.run(hub.sync_key_rules(rt))
    assert podkop.sections_by_name(applied[1][0])["main"]["options"]["urltest_proxy_links"] == ["vless://a@h:1#A", "vless://f@h:5#F"]
    # keys changed in a draft not applied yet: not synced; discarding the draft puts them back
    hub.keep_rules(rid)
    db.set_rule_keys(rid, "main", [gas])
    db.update_key(gas, links=["vless://n@h:6#N"])
    asyncio.run(hub.sync_key_rules(rt))
    assert len(applied) == 2
    hub.restore_rules(rid)
    assert db.rule_keys(rid)["main"] == [gas, free]


FAKE_PROBE = r"""
while [ $# -gt 0 ]; do
  case "$1" in -o) out=$2; shift ;; --nodes) nodes=$2; shift ;; --via) via=$2; shift ;; --via-host-key) hk=$2; shift ;; esac
  shift
done
echo "via $via ($hk)"
{ echo "n,verdict,ok,total,min,median,p90,mbit_s,exit_ip,country,youtube,youtube_ms,node"
  echo '1,GOOD,20,20,0.1,0.2,0.3,50.0,1.2.3.4,NL,,,"A"'
  echo '2,SKIPPED,0,0,-,-,-,-,-,-,,,"B"'; } > "$out"
awk -F '\t' '{ print NR "\t" $1 }' "$nodes" > "$out.links"
grep -c '!' "$nodes"
"""


def test_local_probe_tests_the_routers_nodes_here(tmp_path: Path):
    import asyncio
    from podkop_manager.hub import RouterRuntime
    (tmp_path / "router").mkdir()
    (tmp_path / "router" / "probe.sh").write_text(FAKE_PROBE)
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    db.set_probe_settings(rid, keys=["https://sub.example.com/abc"], test_from="manager")
    hub = Hub(db, tmp_path / "key", tmp_path)
    rt = RouterRuntime(db.router(rid), hub.key)
    hub.routers[rid] = rt
    asked = []

    class Agent:
        async def call(self, *args, payload=None, timeout=0):
            asked.append((args, payload))
            return [{"link": "vless://a@h:1#A", "outbound": {"type": "vless", "server": "h"}},
                    {"link": "vless://b@h:2#B", "why": "podkop: no"}]
    rt.agent = Agent()
    run = db.add_probe_run(rid, "manual", [])
    asyncio.run(hub._run_local_probe(rt, run, "manual", ["--countries", "NL"]))
    assert asked == [(("probe-nodes",), {"keys": ["https://sub.example.com/abc"], "fix_fingerprint": False})]
    st = db.probe_result(run)
    assert db.probe_runs(rid)[0]["state"] == "done" and st["local"]
    assert [(r["node"], r["link"]) for r in st["rows"]] == [("A", "vless://a@h:1#A"), ("B", "vless://b@h:2#B")]
    assert st["log"] == ["via root@192.168.1.1:22 (ssh-ed25519 AAAA)", "1"]
    assert not (tmp_path / "probe" / str(rid) / "nodes.txt").exists()
    assert asyncio.run(hub.probe_status(rt))["rows"] == st["rows"]
    rt.local_probe = None          # after a restart: the stored result
    assert asyncio.run(hub.probe_status(rt))["rows"] == st["rows"]


def test_engine_download_is_checked_and_unpacked(tmp_path: Path, monkeypatch):
    import hashlib
    import io
    import tarfile
    from podkop_manager import hub as hubmod

    def tgz(name, data):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as t:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        return out.getvalue()
    ipk = tgz("./data.tar.gz", tgz("./usr/bin/sing-box", b"#!/bin/sh\necho engine\n"))

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    monkeypatch.setattr(hubmod.urllib.request, "urlopen", lambda url, timeout=0: Answer(ipk))
    monkeypatch.setitem(hubmod.ENGINE_FILES, "x86_64", ("x86_64", hashlib.sha256(ipk).hexdigest()))
    dest = tmp_path / "bin" / "sing-box"
    hubmod.fetch_engine(dest, "x86_64")
    assert dest.read_bytes() == b"#!/bin/sh\necho engine\n" and dest.stat().st_mode & 0o111
    monkeypatch.setitem(hubmod.ENGINE_FILES, "x86_64", ("x86_64", "0" * 64))
    with pytest.raises(ValueError, match="not the expected file"):
        hubmod.fetch_engine(tmp_path / "other", "x86_64")
    with pytest.raises(ValueError, match="no build"):
        hubmod.fetch_engine(dest, "mips")
