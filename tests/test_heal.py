"""Self-heal of dead URLTest groups on the router (router/reliability.sh)."""
import json
import subprocess
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "router/reliability.sh"

CONFIG = {"outbounds": [
    {"type": "urltest", "tag": "main-urltest-out", "outbounds": ["main-1-out", "main-2-out"],
     "fallbacks": ["main-fallback-1-out"], "interval": "3m"},
    {"type": "urltest", "tag": "youtube-urltest-out", "outbounds": ["youtube-1-out", "youtube-2-out"], "interval": "3m"},
    {"type": "urltest", "tag": "anthropic_am-urltest-out", "outbounds": ["anthropic_am-1-out"], "interval": "3m"},
    *[{"type": "vless", "tag": t} for t in ("main-1-out", "main-2-out", "main-fallback-1-out", "youtube-1-out",
                                             "youtube-2-out", "anthropic_am-1-out")],
    {"type": "direct", "tag": "direct-out"}]}

STUBS = '''
. "$1"
D="$2"; RUN="$D/run"; ETC="$D/etc"; mkdir -p "$RUN" "$ETC"
uci() { echo "$RUN/live.json"; }
config_fp() { cat "$D/fp"; }
proxies() { cat "$D/proxies.json"; }
date() { if [ "${1:-}" = +%s ]; then cat "$D/now"; else command date "$@"; fi; }
event() { printf '%s %s\\n' "$1" "${2:-{\\}}" >> "$D/events"; }
hot_apply() { echo "$1" >> "$D/applies"; heal_filter "$RUN/base.json" "$RUN/live.json"; [ ! -f "$D/fail" ]; }
heal_wan() { [ -f "$D/wan" ]; }
logread() { cat "$D/log" 2>/dev/null; }
logger() { :; }
clash_get() { echo "$*" >> "$D/tests"; }
setsid() { echo "$*" >> "$D/spawned"; }
reply() { printf '%s\\n' "$1"; exit 0; }
fail() { echo "$*" >&2; exit 1; }
'''


class Router:
    def __init__(self, tmp: Path):
        self.d = tmp
        (tmp / "run").mkdir()
        (tmp / "run/base.json").write_text(json.dumps(CONFIG))
        (tmp / "run/live.json").write_text(json.dumps(CONFIG))
        (tmp / "fp").write_text("fp1")
        (tmp / "wan").write_text("")
        self.now(1000)
        self.alive([])

    def now(self, t):
        (self.d / "now").write_text(str(t))

    def alive(self, tags, delay=100):
        (self.d / "proxies.json").write_text(json.dumps({"proxies": {
            t: {"history": [{"delay": delay if t in tags else 0}] if t in tags else []}
            for t in [o["tag"] for o in CONFIG["outbounds"]] + ["main-heal-1-out"]}}))

    def sh(self, command, stdin=""):
        p = subprocess.run(["sh", "-c", STUBS + command, "sh", str(HELPER), str(self.d)], input=stdin,
                           capture_output=True, text=True, timeout=30)
        assert p.returncode == 0, p.stderr
        return p.stdout

    def check(self):
        self.sh("heal_check")

    def events(self):
        f = self.d / "events"
        rows = [line.split(" ", 1) for line in f.read_text().splitlines()] if f.exists() else []
        return [(t, json.loads(d)) for t, d in rows]

    def members(self, group="main"):
        live = json.loads((self.d / "run/live.json").read_text())
        return next(o for o in live["outbounds"] if o["tag"] == f"{group}-urltest-out")["outbounds"]

    def state(self):
        return json.loads((self.d / "run/heal/state.json").read_text())


def test_dead_group_borrows_working_servers_then_gives_them_back(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "run/../etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("anthropic_am\n")
    r.alive(["youtube-2-out", "youtube-1-out", "anthropic_am-1-out"])
    r.check()                                   # first check without an answer: one strike
    assert r.events() == [] and r.state()["groups"]["main"]["strikes"] == 1
    tests = (tmp_path / "tests").read_text()
    assert "/proxies/main-fallback-1-out/delay" in tests and "/proxies/anthropic_am-1-out/delay" not in tests
    r.now(1030); r.check()                      # the second, 30 s later: down
    (typ, data), = r.events()
    assert typ == "group_down" and data["section"] == "main"
    assert set(data["borrowed"]) == {"youtube-1-out", "youtube-2-out"}     # never the section marked off
    assert data["servers"] == ["main-1-out", "main-2-out", "main-fallback-1-out"]
    assert r.members()[:2] == ["main-1-out", "main-2-out"] and set(r.members()[2:]) == {"youtube-1-out", "youtube-2-out"}
    r.now(1060); r.check()                      # still down: no second report, no reload
    assert len(r.events()) == 1 and len((tmp_path / "applies").read_text().splitlines()) == 1
    r.alive(["youtube-1-out", "main-2-out"])    # one of its own servers works again
    r.now(1090); r.check()
    assert r.events()[-1][0] == "group_recovered"
    assert r.members() == ["main-1-out", "main-2-out"] and r.state()["groups"] == {}


def test_wan_outage_is_not_reported(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "wan").unlink()
    r.check(); r.now(1030); r.check(); r.now(1060); r.check()
    assert r.events() == [] and not (tmp_path / "applies").exists()
    (tmp_path / "wan").write_text("")
    r.now(2060); r.check()
    assert [t for t, _ in r.events()] == ["group_down"] * 3   # every group, nothing to borrow
    assert r.events()[0][1]["borrowed"] == []


def test_all_off_and_manager_links_expire(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("*\n")
    r.check(); r.now(5000); r.check()
    assert r.events() == []
    (tmp_path / "etc/heal-off").unlink()
    # the manager's servers: built by podkop's parser (stubbed), 2-hour lease
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/sing_box_config_facade.sh").write_text(
        'sing_box_cf_add_proxy_outbound() { printf \'{"outbounds":[{"type":"vless","tag":"%s-out","server":"x"}]}\' "$2"; }\n')
    out = r.sh(f'PODKOP_LIB="{tmp_path}/lib"; cmd_heal_links',
               json.dumps({"section": "main", "fingerprint": "fp1", "links": ["vless://a@b:1#x"]}))
    assert json.loads(out)["groups"]["main"]["extra"] == ["main-heal-1-out"]
    live = json.loads((tmp_path / "run/live.json").read_text())
    assert "main-heal-1-out" in r.members() and any(o["tag"] == "main-heal-1-out" for o in live["outbounds"])
    r.now(5000 + 7300); r.check()               # lease over: they go, and the group is reported again
    assert "main-heal-1-out" not in r.members()
    assert [t for t, _ in r.events()].count("group_down") >= 1


def test_settings_change_and_failed_reload(tmp_path):
    r = Router(tmp_path)
    r.alive(["youtube-1-out"])
    (tmp_path / "fail").write_text("")
    r.check(); r.now(1500); r.check()
    assert [t for t, _ in r.events()] == ["heal_failed", "group_down", "group_down"]   # main and anthropic_am
    assert r.state()["groups"]["main"]["added"] == [] and r.state()["groups"]["main"]["hold"] > 1500
    (tmp_path / "fail").unlink()
    r.now(1560); r.check()                      # held: no new try for 10 minutes
    assert len((tmp_path / "applies").read_text().splitlines()) == 2    # the try and its undo
    r.now(2200); r.check()
    assert "youtube-1-out" in r.members()
    (tmp_path / "fp").write_text("fp2")         # an apply: the old state means nothing now
    r.sh('heal_filter "$RUN/base.json" "$RUN/live.json"')
    assert r.members() == ["main-1-out", "main-2-out"]


def test_failed_lists_are_updated_once_a_group_works(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "log").write_text("podkop: [info] Starting lists update...\npodkop: [error] Download cloudflare list failed\n")
    r.alive(["youtube-1-out"])
    r.check(); r.now(1500); r.check()
    assert "podkop list_update" in (tmp_path / "spawned").read_text()


def test_heal_off_command_validates_and_writes_only_on_change(tmp_path):
    r = Router(tmp_path)
    out = json.loads(r.sh("cmd_heal_off", json.dumps({"sections": ["anthropic_am"]})))
    assert out["off"] == ["anthropic_am"] and (tmp_path / "etc/heal-off").read_text() == "anthropic_am\n"
    p = subprocess.run(["sh", "-c", STUBS + "cmd_heal_off", "sh", str(HELPER), str(tmp_path)],
                       input=json.dumps({"sections": ["bad name"]}), capture_output=True, text=True)
    assert p.returncode == 1
    json.loads(r.sh("cmd_heal_off", json.dumps({"sections": []})))
    assert not (tmp_path / "etc/heal-off").exists()


# --- manager ----------------------------------------------------------------------------------

import asyncio  # noqa: E402
import time  # noqa: E402

from podkop_manager import podkop  # noqa: E402
from podkop_manager.db import DB  # noqa: E402
from podkop_manager.hub import Hub, RouterRuntime  # noqa: E402


def test_link_host_and_network():
    assert podkop.link_host("vless://uuid@Host.example:443?type=tcp#name") == "host.example"
    assert podkop.link_host("ss://YWVzOnBhc3M=@1.2.3.4:2030#x") == "1.2.3.4"
    assert podkop.link_host("vless://u@[2001:db8::1]:443") == "2001:db8::1"
    assert podkop.link_host("vmess://eyJhZGQiOiJ4In0=") == ""
    assert podkop.entry_net("62.144.49.101") == "62.144.49.0/24"
    assert podkop.entry_net("2001:db8::1") is None and podkop.entry_net(None) is None


def _row(n, ip, verdict="GOOD", key=1):
    return {"link": f"vless://u@h{n}:443#s{n}", "verdict": verdict, "median": 0.1 + n / 1000, "p90": 0.2, "mbit": 50,
            "country": "DE", "entry_ip": ip, "key": key}


def test_picks_spread_across_networks_but_never_take_flaky_first():
    rows = [_row(i, f"62.144.49.{i}") for i in range(1, 8)] + [_row(10 + i, f"10.0.{i}.1", "OK") for i in range(3)]
    rows.append(_row(20, "10.9.9.9", "FLAKY"))
    pick = podkop.auto_pick(rows, podkop.check_auto_rule({"top": 5, "fallback": 2}))
    nets = [podkop.entry_net(x["entry_ip"]) for x in pick["matched"]]
    assert nets.count("62.144.49.0/24") == 2 and len(pick["links"]) == 5
    assert all(x["verdict"] != "FLAKY" for x in pick["matched"] + pick["fallback_rows"])
    # unknown networks: the order is the old one (reliability, then key diversity)
    plain = podkop.auto_pick([dict(r, entry_ip=None) for r in rows], podkop.check_auto_rule({"top": 5, "fallback": 2}))
    assert [x["verdict"] for x in plain["matched"]] == ["GOOD"] * 5


class FakeAgent:
    def __init__(self, cfg):
        self.cfg, self.calls = cfg, []

    async def call(self, name, *args, **kwargs):
        self.calls.append((name, kwargs.get("payload")))
        if name == "config-get":
            return {"config": self.cfg, "fingerprint": "fp"}
        if name == "resolve":
            return {h: "62.144.49.1" for h in kwargs["payload"]["hosts"]}
        return {}


def _hub(tmp_path, cfg):
    db = DB(tmp_path / "manager.db")
    rid = db.add_router("reserv", "router", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", Path(__file__).resolve().parents[1])
    rt = RouterRuntime(db.router(rid), tmp_path / "key")
    rt.agent = FakeAgent(cfg)
    hub.routers[rid] = rt
    return db, hub, rt, rid


def _cfg(links):
    return {"sections": [{"name": "settings", "type": "settings", "anonymous": False, "options": {}},
                         {"name": "main", "type": "section", "anonymous": False, "options": {
                             "connection_type": "proxy", "proxy_config_type": "urltest", "urltest_proxy_links": links}}]}


def test_group_down_lends_earlier_servers_and_runs_one_probe(tmp_path):
    now = _cfg(["vless://u@dead1:443#a", "vless://u@dead2:443#b"])
    db, hub, rt, rid = _hub(tmp_path, now)
    for links in (["vless://u@old1:443#x", "vless://u@dead1:443#a"], ["vless://u@old2:443#y"]):
        vid = db.add_version(rid, _cfg(links), [], "", "fp")
        db.update_version(vid, state="applied")
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 5}))
    probes = []

    async def start_probe(r, trigger="manual"):
        probes.append(trigger)
        return 1
    hub.start_probe = start_probe
    asyncio.run(hub.heal(rt, {"section": "main", "borrowed": []}))
    name, payload = rt.agent.calls[-1]
    assert name == "heal-links" and payload["fingerprint"] == "fp"
    assert payload["links"] == ["vless://u@old2:443#y", "vless://u@old1:443#x"]    # newest first, none it has now
    assert probes == ["heal"]
    rt.agent.calls.clear()
    asyncio.run(hub.heal(rt, {"section": "main", "borrowed": ["youtube-1-out"]}))
    assert rt.agent.calls == [] and probes == ["heal"]        # borrowed on the router; probe only every 30 min


def test_heal_probe_result_is_applied_with_entry_networks(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@dead1:443#a"]))
    applied = []

    async def apply_probe(r, st, note):
        applied.append((note, st))
        return 7
    hub.apply_probe = apply_probe
    run = db.add_probe_run(rid, "heal")
    st = {"state": "done", "rows": [{"link": "vless://u@new1:443#n", "verdict": "GOOD"}], "log": []}
    asyncio.run(hub._finish_probe(rt, run, "heal", st))
    assert applied[0][0] == "self-heal probe" and applied[0][1]["rows"][0]["entry_ip"] == "62.144.49.1"
    run = db.add_probe_run(rid, "heal")
    asyncio.run(hub._finish_probe(rt, run, "heal", {"state": "failed", "log": ["error: no connection"]}))
    assert db.events(rid)[0]["type"] == "heal_probe_failed"


def test_loaded_config_keeps_its_servers_when_the_probe_is_old(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    run = db.add_probe_run(rid, "manual")
    db.finish_probe_run(run, "done", {"state": "done", "rows": [
        {"link": "vless://u@stale:443#s", "verdict": "GOOD", "country": "DE", "median": "0.1", "mbit": "50"}]})
    saved = {"config": _cfg(["vless://u@file:443#f"]), "rules": {"main": podkop.check_auto_rule({"top": 1})}}
    db.x("UPDATE probe_runs SET finished = ? WHERE id = ?", time.time() - 2 * 86400, run)
    out = hub.load_config(rid, saved)
    assert podkop.sections_by_name(out)["main"]["options"]["urltest_proxy_links"] == ["vless://u@file:443#f"]
    db.x("UPDATE probe_runs SET finished = ? WHERE id = ?", time.time(), run)
    out = hub.load_config(rid, saved)
    assert podkop.sections_by_name(out)["main"]["options"]["urltest_proxy_links"] == ["vless://u@stale:443#s"]


def test_a_reported_group_reports_its_recovery_after_an_apply(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("youtube\nanthropic_am\n")
    r.check(); r.now(1030); r.check()
    assert [t for t, _ in r.events()] == ["group_down"]
    (tmp_path / "fp").write_text("fp2")         # the probe's servers applied
    r.now(1060); r.check()                      # still down: a new count, no second report yet
    assert [t for t, _ in r.events()] == ["group_down"] and r.state()["groups"]["main"]["alerted"]
    r.alive(["main-1-out"])
    r.now(1090); r.check()
    assert [t for t, _ in r.events()] == ["group_down", "group_recovered"] and r.state()["groups"] == {}


def test_alert_clears_when_the_router_has_no_group_down(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    rt.alert = {"type": "group_down", "t": time.time() - 120}
    rt.status = {"heal": {"off": [], "groups": {"main": {"dead_since": 1, "reported": True}}}}
    hub._clear_heal_alert(rt)
    assert rt.alert
    rt.status = {"heal": {"off": [], "groups": {}}}
    hub._clear_heal_alert(rt)
    assert rt.alert is None


def test_resolved_addresses_are_kept_for_later_picks(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 2}))
    rows = [{"link": f"vless://u@h{i}:443#s{i}", "verdict": "GOOD", "median": "0.1", "mbit": "50", "country": "DE"}
            for i in range(3)]
    asyncio.run(hub._entry_ips(rt, {"rows": [dict(r) for r in rows]}))
    pick = hub.auto_picks(rid, {"rows": rows})["main"]      # rows read again, without addresses
    assert all(x["entry_ip"] == "62.144.49.1" for x in pick["matched"])


def test_lending_waits_while_the_router_is_busy(tmp_path, monkeypatch):
    import podkop_manager.hub as hubmod
    from podkop_manager.agent import AgentError
    monkeypatch.setattr(hubmod, "HEAL_RETRY_SECONDS", 0)
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@dead:443#a"]))
    vid = db.add_version(rid, _cfg(["vless://u@old:443#x"]), [], "", "fp")
    db.update_version(vid, state="applied")
    tries = []
    call = rt.agent.call

    async def busy_once(name, *args, **kwargs):
        if name == "heal-links":
            tries.append(1)
            if len(tries) == 1:
                raise AgentError("busy", "an apply is in progress")
        return await call(name, *args, **kwargs)
    rt.agent.call = busy_once
    asyncio.run(hub.heal(rt, {"section": "main", "borrowed": []}))
    assert len(tries) == 2 and not [e for e in db.events(rid) if e["type"] == "heal_links_failed"]
