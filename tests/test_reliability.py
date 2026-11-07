import asyncio
import copy
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from podkop_manager import podkop, reliability as r
from podkop_manager.db import DB
from podkop_manager.hub import Hub, RouterRuntime
from podkop_manager.agent import AgentError


def path(ok=True, code=None):
    return [{"target": t, "exit": 0 if ok else 28, "code": code if code is not None else 204 if ok else 0,
             "seconds": .1 if ok else 8} for t in ("https://a.example", "https://b.example")]


LINKS = {"main-1-out": "vless://one@a:443#one", "main-2-out": "vless://two@b:443#two"}
CONFIG = {"sections": [{"name": "main", "options": {"connection_type": "proxy", "proxy_config_type": "urltest",
                       "urltest_proxy_links": list(LINKS.values())}}]}
OPTIONS = dict(r.DEFAULTS, enabled=True, auto_recover=True)


def report(a=False, b=True, direct=True, dns=True):
    return {"direct": path(direct), "router_dns": dns, "nodes": {"main-1-out": path(a), "main-2-out": path(b)}}


def test_quarantine_requires_two_paired_checks_and_verified_replacement():
    state = r.advance({}, report(), LINKS, OPTIONS, 1000)
    assert r.eligibility(CONFIG, LINKS, state, report(), 1000) == []
    state = r.advance(state, report(), LINKS, OPTIONS, 1060)
    assert r.eligibility(CONFIG, LINKS, state, report(), 1060) == ["main-1-out"]
    assert r.eligibility(CONFIG, LINKS, state, report(), 1300) == []  # replacement is stale
    state = r.advance(state, report(b=False), LINKS, OPTIONS, 1120)
    assert r.eligibility(CONFIG, LINKS, state, report(b=False), 1120) == []  # never empty a group


@pytest.mark.parametrize("bad", [report(direct=False), report(dns=False), report(dns=None)])
def test_common_failure_breaks_sequence(bad):
    state = r.advance({}, report(), LINKS, OPTIONS, 1000)
    state = r.advance(state, bad, LINKS, OPTIONS, 1060)
    state = r.advance(state, report(), LINKS, OPTIONS, 1120)
    assert not state[r.identity(LINKS["main-1-out"])].get("quarantined")


def test_access_denied_and_one_failed_target_do_not_quarantine():
    data = report()
    data["nodes"]["main-1-out"] = path(True, 403)
    state = {}
    for t in (1000, 1060, 1120):
        state = r.advance(state, data, LINKS, OPTIONS, t)
    assert not state[r.identity(LINKS["main-1-out"])].get("quarantined")
    data["nodes"]["main-1-out"] = [path()[0], path(False)[1]]
    assert r.checked_path(data["nodes"]["main-1-out"]) == "degraded"


def test_socks_handshake_failure_and_broken_dns_controls():
    rows = [dict(v, exit=97) for v in path(False)]
    assert r.checked_path(rows) == "down"
    data = report()
    data.update(primary_dns={"ok": False}, provider_dns=False)
    state = r.advance({}, data, LINKS, OPTIONS, 1000)
    state = r.advance(state, data, LINKS, OPTIONS, 1060)
    assert not state[r.identity(LINKS["main-1-out"])].get("quarantined")


def test_cooldown_and_two_successes_are_both_required():
    state = r.advance({}, report(), LINKS, OPTIONS, 1000)
    state = r.advance(state, report(), LINKS, OPTIONS, 1060)
    key = r.identity(LINKS["main-1-out"])
    state = r.advance(state, report(a=True), LINKS, OPTIONS, 1120)
    state = r.advance(state, report(a=True), LINKS, OPTIONS, 1180)
    assert state[key]["quarantined"]
    state = r.advance(state, report(a=True), LINKS, OPTIONS, 1661)
    assert state[key]["quarantined"]  # stale successes cannot release it
    state = r.advance(state, report(a=True), LINKS, OPTIONS, 1721)
    assert not state[key]["quarantined"]


def test_identity_survives_reorder_and_duplicate_group_is_not_two_failures():
    links = {**LINKS, "youtube-1-out": LINKS["main-1-out"] + " renamed"}
    data = report()
    data["nodes"]["youtube-1-out"] = path(False)
    state = r.advance({}, data, links, OPTIONS, 1000)
    assert state[r.identity(LINKS["main-1-out"])]["failures"] == 1
    assert r.identity("vless://one@a:443#renamed") == r.identity(LINKS["main-1-out"])


def test_log_classification_and_incident_burst(tmp_path):
    db = DB(tmp_path / "manager.db")
    line = "connection: connection download closed: http2: client connection lost"
    for _ in range(43):
        db.observe(1, "router", r.log_observation(line), {"selected": {"main": "main-1-out"}}, 1000)
    db.observe(1, "kuma", {"kind": r.classify("Request failed with status code 403"), "scope": "LinkedIn"}, {}, 1001)
    switch = r.log_observation("outbound/urltest[main-urltest-out]: switched from outbound main-1-out to main-2-out")
    db.observe(1, "router", switch, {}, 1002)
    db.observe(2, "router", r.log_observation(line), {}, 1000)
    history = db.connection_history(1)
    assert len(history["incidents"]) == 2
    assert history["incidents"][1]["count"] == 43
    assert len(history["observations"]) == 45
    assert history["incidents"][0]["category"] == "access:LinkedIn"


def test_selections_follow_aliases_and_stop_cycles():
    cfg = copy.deepcopy(CONFIG)
    cfg["sections"].append({"name": "meta", "options": {"connection_type": "proxy"}})
    groups = {"groups": [{"name": "meta-out", "now": "main-out"},
                         {"name": "main-out", "now": "main-urltest-out"},
                         {"name": "main-urltest-out", "now": "main-2-out"}]}
    assert r.selections(cfg, groups) == {"main": "main-2-out", "meta": "main-2-out"}
    groups["groups"][2]["now"] = "main-out"
    assert r.selections(cfg, groups) == {}


def test_streaming_and_interactive_profiles_and_storage(tmp_path):
    rows = [{"link": "vless://a", "verdict": "GOOD", "median": .05, "p90": .4, "mbit": 50},
            {"link": "vless://b", "verdict": "GOOD", "median": .1, "p90": .12, "mbit": 200}]
    rule = podkop.check_auto_rule({"top": 1})
    assert podkop.auto_pick(rows, rule)["links"] == ["vless://a"]
    for profile in ("streaming", "interactive"):
        rule = podkop.check_auto_rule({"top": 1, "profile": profile})
        assert podkop.auto_pick(rows, rule)["links"] == ["vless://b"]
        db = DB(tmp_path / "manager.db")
        db.set_auto_rule(1, "main", rule)
        assert db.auto_rule(1, "main")["profile"] == profile


@pytest.mark.parametrize("value", [{"interval": 0}, {"auto_recover": True}, {"failures": True}, {"oops": 1}])
def test_settings_reject_invalid_values(value):
    with pytest.raises(ValueError):
        r.settings(value)


def test_runtime_filter_keeps_fallback_and_never_empties_group(tmp_path):
    config = {"outbounds": [{"type": "urltest", "tag": "main-urltest-out", "outbounds": ["a"], "fallbacks": ["b"]}]}
    (tmp_path / "config").write_text(json.dumps(config))
    helper = Path(__file__).resolve().parents[1] / "router/reliability.sh"
    for banned, expected in [(["a"], ["b"]), (["a", "b"], ["a"]), ([], ["a"])]:
        (tmp_path / "eligibility.json").write_text(json.dumps({"fingerprint": "fp", "expires": 9999999999, "banned": banned}))
        proc = subprocess.run(["sh", "-c", '. "$1"; RUN="$2"; config_fp() { echo fp; }; reliability_filter "$2/config" "$2/out"',
                               "sh", str(helper), str(tmp_path)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads((tmp_path / "out").read_text())["outbounds"][0]["outbounds"] == expected


def test_stale_runtime_policy_does_not_affect_new_config(tmp_path):
    (tmp_path / "config").write_text(json.dumps({"outbounds": [{"type": "urltest", "outbounds": ["a", "b"]}]}))
    (tmp_path / "eligibility.json").write_text(json.dumps({"fingerprint": "old", "expires": 9999999999, "banned": ["a"]}))
    helper = Path(__file__).resolve().parents[1] / "router/reliability.sh"
    subprocess.run(["sh", "-c", '. "$1"; RUN="$2"; config_fp() { echo new; }; reliability_filter "$2/config" "$2/out"',
                    "sh", str(helper), str(tmp_path)], check=True)
    assert json.loads((tmp_path / "out").read_text())["outbounds"][0]["outbounds"] == ["a", "b"]


def test_hub_checks_and_runtime_only_recovery(tmp_path):
    db = DB(tmp_path / "manager.db")
    rid = db.add_router("test", "router", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", Path(__file__).resolve().parents[1])
    rt = RouterRuntime(db.router(rid), tmp_path / "key")
    hub.routers[rid] = rt
    db.set_setting(f"reliability:{rid}", OPTIONS)
    calls = []

    class Agent:
        async def call(self, name, **kwargs):
            calls.append((name, kwargs.get("payload")))
            if name == "version":
                return {"version": "0.8.0", "protocol": 1}
            if name == "config-get":
                return {"config": CONFIG, "fingerprint": "fp"}
            if name == "proxies":
                return {"groups": [{"name": "main-out", "now": "main-1-out"}]}
            if name == "diagnostics":
                return dict(report(), fingerprint="fp")
            if name == "eligibility":
                return {"changed": True}
            raise AssertionError(name)

    rt.agent = Agent()
    asyncio.run(hub.check_connections(rid))
    asyncio.run(hub.check_connections(rid))
    assert calls[-1] == ("eligibility", {"fingerprint": "fp", "banned": ["main-1-out"]})
    assert len(db.connection_history(rid)["checks"]) == 2
    assert db.versions(rid) == []  # no UCI edits, applies or restarts
    edited = copy.deepcopy(CONFIG)
    edited["sections"][0]["options"]["urltest_tolerance"] = "150"
    db.set_draft(rid, edited, "fp")
    calls.clear()
    asyncio.run(hub.check_connections(rid))
    assert all(c[0] != "eligibility" for c in calls)


def test_authenticated_kuma_webhook_and_settings(tmp_path, monkeypatch):
    from test_web import client, H, FakeHub, FakeRouter, FakeAgent
    async def version(self, name, **kwargs):
        if name == "version":
            return {"version": "0.8.0", "protocol": 1}
        raise AgentError("unreachable", "no router in tests")
    monkeypatch.setattr(FakeAgent, "call", version)
    monkeypatch.setattr(FakeHub, "observe", Hub.observe, raising=False)
    for name, value in (("config", None), ("proxies", None), ("connection_check", {})):
        monkeypatch.setattr(FakeRouter, name, value, raising=False)
    with client(tmp_path, with_router=True) as c:
        assert c.get("/api/routers/1/reliability").status_code == 401
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        assert c.put("/api/routers/1/reliability", headers=H, json={"auto_recover": True}).status_code == 400
        assert c.put("/api/routers/1/reliability", headers=H, json={"enabled": True}).status_code == 200
        route = c.post("/api/routers/1/kuma-token", headers=H).json()["path"]
        assert c.post(route + "wrong", json={}).status_code == 403
        body = {"monitor": {"name": "LinkedIn"}, "heartbeat": {"status": 0, "msg": "Request failed with status code 403"}}
        assert c.post(route, json=body).status_code == 200  # token, not browser session/CSRF
        assert c.db.connection_history(1)["incidents"][0]["category"] == "access:LinkedIn"
        assert c.db.get_setting("server_health:1", {}) == {}
        assert c.post(route, content=b"x" * 65537).status_code == 413
        assert c.post(route, json=[]).status_code == 400
        c.post("/api/routers/1/kuma-token", headers=H)
        assert c.post(route, json=body).status_code == 403  # rotation revokes old token


def test_stalled_payload_requires_independent_controls_and_fresh_recovery():
    data = report(a=True)
    good = [dict(row, bytes=65536) for row in path()]
    bad = [dict(row, code=200, bytes=32768) for row in path(False)]
    data["payload"] = {"diagnostic-direct": good, "main-1-out": bad, "main-2-out": good}
    state = r.advance({}, data, LINKS, OPTIONS, 1000)
    state = r.advance(state, data, LINKS, OPTIONS, 1060)
    key = r.identity(LINKS["main-1-out"])
    assert state[key]["quarantined"]
    state = r.advance(state, report(a=True), LINKS, OPTIONS, 1700)
    assert state[key]["successes"] == 0  # small checks cannot clear a payload stall
    data["payload"]["diagnostic-direct"] = bad
    state = r.advance(state, data, LINKS, OPTIONS, 1760)
    assert state[key]["successes"] == 0  # broken control cannot confirm recovery


@pytest.fixture
def diagnostic_environment(tmp_path):
    """Only the collector's standard tools; no timeout executable or applet."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("sh", "awk", "basename", "cat", "cut", "flock", "head", "jq", "mktemp",
                 "rm", "sleep", "wc", "od", "tr", "true"):
        target = shutil.which(name)
        assert target, f"test prerequisite: {name}"
        (bindir / name).symlink_to(target)
    assert shutil.which("timeout", path=str(bindir)) is None
    return bindir, {**os.environ, "PATH": str(bindir), "PODKOP_DIAG_TICK": "0.1"}


@pytest.mark.parametrize("dns_exit", [0, 1])
@pytest.mark.parametrize("tags", [[], ["main-1-out"],
                                  ["youtube-1-out", "youtube-fallback-2-out", "anthropic_am-1-out", "main-1-out"]])
def test_agent_diagnostics_collects_bounded_separate_paths(tmp_path, diagnostic_environment, dns_exit, tags):
    """Execute the real collector without timeout, with local network fakes."""
    helper = Path(__file__).resolve().parents[1] / "router/reliability.sh"
    config = tmp_path / "live.json"
    config.write_text(json.dumps({"dns": {"servers": [{"tag": "dns-server", "type": "udp", "server": "192.0.2.53"}]},
                                 "outbounds": [{"tag": t, "type": "vless"} for t in tags] +
                                              [{"tag": "direct-out", "type": "direct"}]}))
    commands = {
        "sing-box": '#!/bin/sh\nif [ "$1" = run ]; then exec sleep 90; fi\n',
        "curl": '#!/bin/sh\ncase "$*" in *size_download*connect*) exit 1;; *time_connect*) echo "204 0.1 0.01 0";; *) echo "206 0.1 65536";; esac\n',
        "nslookup": '#!/bin/sh\nprintf "%s\\n" "$2" >> "$LOOKUP_LOG"\nexit "$DNS_EXIT"\n',
        "ping": '#!/bin/sh\nexit 0\n',
        "ubus": '#!/bin/sh\necho \'{"up":true,"l3_device":"eth0","dns-server":["192.0.2.53"]}\'\n',
        "ip": '#!/bin/sh\necho "default via 192.0.2.1 dev eth0"\n',
    }
    bindir, env = diagnostic_environment
    env.update(LOOKUP_LOG=str(tmp_path / "lookups"), DNS_EXIT=str(dns_exit))
    for name, content in commands.items():
        f = bindir / name
        f.write_text(content)
        f.chmod(0o755)
    script = '''
    . "$1"
    RUN="$2"; TAB=$(printf '\\t')
    uci() { echo "$RUN/live.json"; }
    config_fp() { echo fingerprint; }
    reply() { printf '%s\\n' "$1"; exit 0; }
    fail() { echo "$*" >&2; exit 1; }
    cmd_diagnostics
    '''
    out = subprocess.run(["sh", "-c", script, "sh", str(helper), str(tmp_path)],
                         input=json.dumps({"tags": tags, "payload": True}),
                         text=True, capture_output=True, timeout=15,
                         env=env)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["router_dns"] is (dns_exit == 0)
    assert result["provider_dns"] is (dns_exit == 0)
    assert result["primary_dns"]["ok"] is (dns_exit == 0)
    assert (tmp_path / "lookups").read_text().splitlines() == ["127.0.0.42", "192.0.2.53", "192.0.2.53"]
    assert r.checked_path(result["direct"]) == "up"
    assert set(result["nodes"]) == set(tags)
    assert all(r.checked_path(result["nodes"][tag]) == "up" for tag in tags)
    assert all(r.payload_state(result["payload"][tag]) == "up" for tag in tags)
    assert r.controls_work(result) is (dns_exit == 0)
    assert not list(tmp_path.glob("check.*"))


@pytest.mark.parametrize("command,field,limit,message", [
    ("cmd_diagnostics", "tags", 4, "up to four server tags expected"),
    ("cmd_eligibility", "banned", 100, "invalid eligibility policy"),
])
@pytest.mark.parametrize("invalid", [None, 17, "", "../outside", "main/out", "main.1.out",
                                     "main out", "main-1-out\n", "main\t1", "сервер-out", "too_many"])
def test_diagnostics_and_recovery_reject_invalid_tags(tmp_path, diagnostic_environment, command, field, limit, message, invalid):
    helper = Path(__file__).resolve().parents[1] / "router/reliability.sh"
    _, env = diagnostic_environment
    tags = [f"main-{i}-out" for i in range(limit + 1)] if invalid == "too_many" else [invalid]
    script = '''
    . "$1"
    RUN="$2"; ETC="$2"
    fail() { printf '%s: %s\\n' "$1" "$2"; exit 1; }
    "$3"
    '''
    out = subprocess.run(["sh", "-c", script, "sh", str(helper), str(tmp_path), command],
                         input=json.dumps({field: tags, "fingerprint": "fp", "payload": False}),
                         env=env, text=True, capture_output=True, timeout=5)
    assert out.returncode == 1
    assert out.stdout.strip() == f"bad_request: {message}"
    assert not list(tmp_path.glob("check.*"))
    assert not list(tmp_path.glob("policy.*"))


@pytest.mark.parametrize("mode,expected", [("success", 0), ("failure", 2), ("hang", 124), ("cancel", 1)])
def test_bounded_dns_lookup_exit_status_and_cleanup(tmp_path, diagnostic_environment, mode, expected):
    helper = Path(__file__).resolve().parents[1] / "router/reliability.sh"
    bindir, env = diagnostic_environment
    pidfile = tmp_path / "lookup.json"
    fake = bindir / "nslookup"
    fake.write_text(f"#!{sys.executable}\n" + '''import json, os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(os.environ["LOOKUP_PID"], "w") as f:
    json.dump({"pid": os.getpid(), "parent": os.getppid(), "args": sys.argv[1:]}, f)
if os.environ["MODE"] in ("hang", "cancel"):
    time.sleep(60)
print("lookup result")
sys.exit(2 if os.environ["MODE"] == "failure" else 0)
''')
    fake.chmod(0o755)
    env.update(LOOKUP_PID=str(pidfile), MODE=mode)
    script = '. "$1"; diagnostic_lookup "$2" example.com 192.0.2.53'
    started = time.monotonic()
    proc = subprocess.Popen([str(bindir / "sh"), "-c", script, "sh", str(helper), "30" if mode == "cancel" else "1"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                child = json.loads(pidfile.read_text())
                break
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(.01)
        assert child, "lookup did not start"
        if mode == "cancel":
            os.kill(child["parent"], signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode == expected, stderr
        assert time.monotonic() - started < 5
        assert child["args"] == ["example.com", "192.0.2.53"]
        if mode in ("success", "failure"):
            assert stdout.strip() == "lookup result"
        with pytest.raises(ProcessLookupError):
            os.kill(child["pid"], 0)  # no lookup or unreaped zombie left behind
    finally:
        if proc.poll() is None:
            proc.kill()
        if child:
            try:
                os.kill(child["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.communicate(timeout=5)


def test_eligibility_rollback_and_manual_pin(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "router/reliability.sh"
    config = tmp_path / "live.json"
    config.write_text('original config')
    old = {"fingerprint": "fp", "expires": 9999999999, "banned": []}
    policy = tmp_path / "eligibility.json"
    policy.write_text(json.dumps(old))
    script = '''
    . "$1"
    RUN="$2"; ETC="$2"; TAB=$(printf '\\t')
    uci() { echo "$RUN/live.json"; }
    config_fp() { echo fp; }
    singbox_pid() { :; }
    reply() { printf '%s\\n' "$1"; exit 0; }
    fail() { echo "$*" >&2; exit 1; }
    hot_apply() { echo broken > "$RUN/live.json"; return 1; }
    event() { :; }
    cmd_eligibility
    '''
    request = json.dumps({"fingerprint": "fp", "banned": ["main-1-out"]})
    out = subprocess.run(["sh", "-c", script, "sh", str(helper), str(tmp_path)], input=request,
                         capture_output=True, text=True, timeout=10)
    assert out.returncode == 1
    assert config.read_text() == 'original config'
    assert json.loads(policy.read_text()) == old
    (tmp_path / "pins").write_text("main\tlink-hash\n")
    out = subprocess.run(["sh", "-c", script, "sh", str(helper), str(tmp_path)], input=request,
                         capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    assert json.loads(policy.read_text())["banned"] == []
    assert config.read_text() == 'original config'
