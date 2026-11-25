"""zapret2 (agent 0.29, manager 0.42): the agent's sing-box and nft parts,
the strategy list, services, the release payload and the search."""
import base64
import io
import json
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from podkop_manager import zapret
from test_web import H, FakeHub, FakeRouter, client

AGENT = Path(__file__).resolve().parents[1] / "router/podkop-agent"


def _code():
    return subprocess.run(["sed", "-n", "/^# --- zapret: DPI bypass/,/^# --- commands/p", str(AGENT)],
                          capture_output=True, text=True, check=True).stdout


STUBS = '''
RUN="$1"; ETC="$1/etc"; mkdir -p "$ETC"
reply() { printf '%s\\n' "$1"; exit 0; }
fail() { echo "$1: $2" >&2; exit 1; }
nft() { [ -f "$RUN/table" ]; }
'''


def _sh(tmp_path, body, stdin=""):
    code = STUBS + (_code().replace("ZAPRET_DIR=/etc/podkop-zapret", 'ZAPRET_DIR="$RUN/zapret"')
                    .replace("ZAPRET_INIT=/etc/init.d/podkop-zapret", 'ZAPRET_INIT="$RUN/init"')) + body
    return subprocess.run(["sh", "-c", code, "sh", str(tmp_path)], input=stdin, capture_output=True, text=True, timeout=20)


SB = {"outbounds": [
    {"type": "vless", "tag": "main-1-out"},
    {"type": "urltest", "tag": "main-urltest-out", "outbounds": ["main-1-out"]},
    {"type": "selector", "tag": "main-out", "outbounds": ["main-1-out", "main-urltest-out"], "default": "main-urltest-out"},
    {"type": "selector", "tag": "meta-out", "outbounds": ["main-out"]},
    {"type": "selector", "tag": "youtube-out", "outbounds": ["main-out"]},
    {"type": "direct", "tag": "direct-out"}],
    "route": {"rules": [
        {"action": "route", "inbound": "tproxy-in", "outbound": "youtube-out", "rule_set": "youtube-community"},
        {"action": "route", "inbound": "tproxy-in", "outbound": "meta-out", "rule_set": ["meta-user-domains", "meta-user-subnets"]},
        {"action": "route", "inbound": "tproxy-in", "outbound": "main-out", "rule_set": "main-community"}],
        "final": "direct-out"}}
RULES = {"meta": {"args": ["--payload=tls_client_hello", "--lua-desync=fake:blob=fake_default_tls"], "url": "https://www.instagram.com/", "name": "a", "n": 1},
         "youtube": {"args": ["--lua-desync=multisplit:pos=1"], "url": "https://www.youtube.com/generate_204", "name": "b", "n": 2}}


def _setup(tmp_path, rules=RULES, table=True, strict=""):
    (tmp_path / "zapret").mkdir(exist_ok=True)
    nf = tmp_path / "zapret/nfqws2"
    nf.write_text("#!/bin/sh\n")
    nf.chmod(0o755)
    (tmp_path / "zapret/rules.json").write_text(json.dumps(rules))
    if table:
        (tmp_path / "table").write_text("")
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/strict").write_text(strict)
    (tmp_path / "in.json").write_text(json.dumps(SB))


def _filter(tmp_path, src="in.json"):
    p = _sh(tmp_path, f'\nzapret_filter "$RUN/{src}" "$RUN/out.json" && cat "$RUN/out.json"')
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_filter_puts_zapret_first_and_the_rules_servers_behind(tmp_path):
    _setup(tmp_path)
    out = _filter(tmp_path)
    tags = {o["tag"]: o for o in out["outbounds"]}
    assert tags["meta-zapret-direct"] == {"type": "direct", "tag": "meta-zapret-direct", "routing_mark": 0x08000001}
    g = tags["meta-zapret-out"]
    assert g["type"] == "urltest" and g["outbounds"] == ["meta-zapret-direct"] and g["fallbacks"] == ["meta-out"]
    assert g["url"] == "https://www.instagram.com/"
    assert tags["youtube-zapret-direct"]["routing_mark"] == 0x08000002
    rules = out["route"]["rules"]
    assert [r["outbound"] for r in rules] == ["youtube-zapret-out", "youtube-zapret-out", "youtube-out",
                                              "meta-zapret-out", "meta-zapret-out", "meta-out", "main-out"]
    # only the web goes to zapret; the rest of what the rule takes stays on its servers
    assert rules[3] == dict(SB["route"]["rules"][1], network=["tcp"], port=[80, 443], protocol=["tls", "http"], outbound="meta-zapret-out")
    assert rules[4] == dict(SB["route"]["rules"][1], network=["udp"], port=[443], outbound="meta-zapret-out")
    assert rules[5] == SB["route"]["rules"][1]
    # run again on its own output: the same config (health_on reads the live one)
    (tmp_path / "again.json").write_text(json.dumps(out))
    assert _filter(tmp_path, "again.json") == out


def test_filter_takes_everything_back_without_rules_or_table_and_skips_kill_switch(tmp_path):
    _setup(tmp_path)
    out = _filter(tmp_path)
    (tmp_path / "again.json").write_text(json.dumps(out))
    (tmp_path / "table").unlink()          # no firewall table: zapret traffic would loop, so none
    assert _filter(tmp_path, "again.json") == SB
    _setup(tmp_path, strict="meta\n")
    out = _filter(tmp_path)
    assert "meta-zapret-out" not in {o["tag"] for o in out["outbounds"]}
    assert "youtube-zapret-out" in {o["tag"] for o in out["outbounds"]}


def test_nft_table_queues_each_rule_and_the_search_and_gives_podkops_mark(tmp_path):
    p = _sh(tmp_path, "\nzapret_nft_text \"$(cat)\"", json.dumps(RULES))
    assert p.returncode == 0, p.stderr
    t = p.stdout
    for n in (1, 2, 99):
        assert f"meta mark {0x08000000 + n} tcp dport {{ 80, 443 }} ct original packets 1-20 counter queue num {300 + n} bypass" in t
        assert f"ct mark {0x08000000 + n} tcp sport {{ 80, 443 }} ct reply packets 1-10 queue num {300 + n} bypass" in t
    assert "type filter hook output priority -160" in t and "type route hook output priority -155" in t
    assert "ct mark & 0xff000000 == 0x08000000 meta mark set 0x00200000" in t
    assert "udp dport 443 counter reject" in t


def test_exec_passes_each_option_as_one_argument(tmp_path):
    rule = {"args": ["--lua-desync=luaexec:code=desync.pat=tls_mod(fake_default_tls,'rnd,dupsid',desync.reasm_data)",
                     "--payload=tls_client_hello"], "n": 3}
    p = _sh(tmp_path, '\nzapret_run() { for a in "$@"; do echo "[$a]"; done; }\nzapret_exec "$(cat)"', json.dumps(rule))
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == ["[3]", f"[{rule['args'][0]}]", "[--payload=tls_client_hello]"]


@pytest.mark.parametrize("args", [["--qnum=5"], ["--fwmark=1"], ["--lua-init=@/etc/x"], ["plain"], ["--a=b c"], []])
def test_options_of_ours_or_odd_ones_are_refused(tmp_path, args):
    p = _sh(tmp_path, '\nprintf "%s" "$(cat)" | jq -e --argjson deny "$ZAPRET_DENY" "$ZAPRET_ARGS_OK"', json.dumps(args))
    assert p.returncode != 0


# --- manager side ------------------------------------------------------------------

def test_strategy_lines_parse_and_bad_ones_name_their_line():
    got = zapret.parse_strategies("# mine\nfast | --payload=tls_client_hello --lua-desync=fake:blob=0x00\n\n")
    assert got == [{"name": "fast", "args": ["--payload=tls_client_hello", "--lua-desync=fake:blob=0x00"]}]
    with pytest.raises(ValueError, match="line 2"):
        zapret.parse_strategies("a | --x=1\nb | rm -rf /")
    with pytest.raises(ValueError, match="line 1"):
        zapret.parse_strategies("just options --x=1")
    d = zapret.default_strategies()
    assert len(d) >= 8 and all(a.startswith("--") for s in d for a in s["args"])
    assert zapret.parse_strategies(zapret.strategies_text(d)) == d


def sec(name, **o):
    return {"name": name, "type": "section", "options": {"connection_type": "proxy", **o}}


def test_services_by_name_or_by_their_own_lists_only():
    assert zapret.service_of(sec("meta")) == "meta"
    assert zapret.service_of(sec("yt", community_lists=["youtube"])) == "youtube"
    assert zapret.service_of(sec("twitter")) == "twitter"
    assert zapret.service_of(sec("main", community_lists=["youtube", "russia_inside"])) is None
    view = zapret.rules_view({"sections": [{"name": "settings", "type": "settings", "options": {}}, sec("telegram"), sec("discord"),
                                           {"name": "vpn_1", "type": "section", "options": {"connection_type": "vpn"}}]})
    assert [(r["name"], r["default"]) for r in view] == [("telegram", True), ("discord", False)]
    assert view[0]["urls"] and view[0]["note"]


def test_a_release_downloaded_before_does_when_github_refuses(tmp_path, monkeypatch):
    import urllib.error

    def refused(url, timeout=0):
        raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, None)
    monkeypatch.setattr(zapret, "_get", refused)
    with pytest.raises(OSError, match="rate limit"):
        zapret.release(tmp_path)                     # nothing kept: the error says why
    (tmp_path / "zapret2-v1.0.6-openwrt-embedded.tar.gz").write_bytes(b"old")
    newest = tmp_path / "zapret2-v1.0.7-openwrt-embedded.tar.gz"
    newest.write_bytes(b"new")
    import os
    os.utime(tmp_path / "zapret2-v1.0.6-openwrt-embedded.tar.gz", (1, 1))
    assert zapret.release(tmp_path) == ("v1.0.7", newest)


def test_payload_takes_the_routers_binary_lua_and_fake_files(tmp_path):
    tgz = tmp_path / "z.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        for name, data in [("zapret2-v9/binaries/linux-arm64/nfqws2", b"ARM"), ("zapret2-v9/binaries/linux-x86_64/nfqws2", b"X86"),
                           ("zapret2-v9/lua/zapret-lib.lua.gz", b"L"), ("zapret2-v9/lua/zapret-tests.lua.gz", b"T"),
                           ("zapret2-v9/files/fake/tls_clienthello_www_google_com.bin", b"F"), ("zapret2-v9/files/fake/bitcoin.bin", b"B")]:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    body = zapret.payload("v9", tgz, "linux-arm64")
    assert sorted(body["files"]) == ["fake/tls_clienthello_www_google_com.bin", "lua/zapret-lib.lua.gz", "nfqws2"]
    assert base64.b64decode(body["files"]["nfqws2"]) == b"ARM" and body["version"] == {"nfqws2": "v9"}
    assert zapret.arch_dirs("aarch64") == ["linux-arm64"] and zapret.arch_dirs("mips")[0] == "linux-mipsel"


class ZapretAgent:
    def __init__(self):
        self.calls, self.rules = [], {}

    async def call(self, *args, payload=None, timeout=60):
        self.calls.append((args, payload))
        if args[:2] == ("zapret", "status"):
            return {"installed": "v1.0.7", "rules": self.rules, "using": {}, "refused": [], "arch": "aarch64"}
        if args[:2] == ("zapret", "check"):
            return {"job": "1-1"}
        if args[0] == "job":
            return {"state": "done", "baseline": [{"url": "u", "ok": False}], "results": [
                {"name": "slow", "args": ["--a=1"], "ok": True, "urls": [{"ok": True, "ms": 900}]},
                {"name": "fast", "args": ["--b=1"], "ok": True, "urls": [{"ok": True, "ms": 200}]},
                {"name": "bad", "args": ["--c=1"], "ok": False, "urls": [{"ok": False, "ms": 8000}]}]}
        if args[:2] == ("zapret", "set"):
            self.rules = payload["rules"]
            return {"rules": self.rules}
        raise AssertionError(args)


def test_search_turns_zapret_on_with_the_fastest_working_strategy(tmp_path, monkeypatch):
    monkeypatch.setattr(zapret, "POLL", 0)
    monkeypatch.setattr(FakeHub, "cached_config", lambda self, r: None, raising=False)
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        rt = FakeRouter()
        rt.agent, rt.status = ZapretAgent(), {"agent": {"capabilities": ["zapret"]}}
        monkeypatch.setattr(FakeHub, "get", lambda self, rid: rt)
        r = c.get("/api/routers/1/zapret", headers=H)
        assert r.status_code == 200, r.text
        assert [x["name"] for x in r.json()["rules"]] == ["main"] and r.json()["count"] >= 8
        r = c.post("/api/routers/1/zapret/search", headers=H, json={"apply": True, "rules": [
            {"name": "meta", "urls": ["https://www.instagram.com/"], "test": "https://www.instagram.com/"}]})
        assert r.status_code == 200, r.text
        for _ in range(100):
            run = c.get("/api/routers/1/zapret", headers=H).json()["run"]
            if run["state"] != "running":
                break
            time.sleep(0.02)
        assert run["state"] == "done", run["error"]
        assert rt.agent.rules == {"meta": {"args": ["--b=1"], "url": "https://www.instagram.com/", "name": "fast", "service": ""}}
        assert run["outcome"]["meta"] == {"on": True, "strategy": "fast"}
        # a strategy list sent by the page
        bad = c.put("/api/routers/1/zapret", headers=H, json={"rules": {"meta": {"args": ["rm"], "url": "https://x/"}}})
        assert bad.status_code == 400
        assert c.put("/api/zapret/strategies", headers=H, json={"text": "x | nope"}).status_code == 400
        assert c.put("/api/zapret/strategies", headers=H, json={"text": "mine | --lua-desync=fake"}).json() == {"count": 1, "custom": True, "interval": 30}


def test_a_zapret_file_turns_its_rules_on_installs_zapret_first_and_keeps_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeHub, "cached_config", lambda self, r: None, raising=False)
    installs = []

    class Bare(ZapretAgent):
        installed = None

        async def call(self, *args, payload=None, timeout=60):
            if args[:2] == ("zapret", "status"):
                return {"installed": self.installed, "rules": self.rules, "using": {}, "refused": [], "arch": "aarch64"}
            if args[:2] == ("zapret", "set"):
                self.rules = payload["rules"]
                return {"running": list(self.rules), "refused": []}
            return await super().call(*args, payload=payload, timeout=timeout)

    async def install(agent, cache, machine):
        installs.append(machine)
        agent.installed = "v1.0.7"
        return {}

    monkeypatch.setattr(zapret, "install", install)
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        agent = Bare()
        agent.rules = {"other": {"args": ["--a=1"], "url": "https://o.example/", "name": "kept", "service": "", "n": 1}}
        monkeypatch.setattr(FakeRouter, "agent", agent)
        monkeypatch.setattr(FakeRouter, "status", {"agent": {"capabilities": ["zapret"]}})
        rule = {"args": ["--payload=tls_client_hello"], "url": "https://www.youtube.com/generate_204", "name": "fake md5", "service": "youtube"}
        # a rule the router does not run yet: nothing happens
        r = c.post("/api/routers/1/zapret/import", headers=H, json={"rules": {"main": rule, "meta": rule}})
        assert r.status_code == 400 and "meta" in r.json()["detail"] and "apply the config first" in r.json()["detail"]
        assert not installs and "main" not in agent.rules
        assert c.post("/api/routers/1/zapret/import", headers=H, json={"rules": {"main": dict(rule, args=["rm"])}}).status_code == 400
        assert c.post("/api/routers/1/zapret/import", headers=H, json={"rules": {}}).status_code == 400
        r = c.post("/api/routers/1/zapret/import", headers=H, json={"rules": {"main": rule}})
        assert r.status_code == 200, r.text
        assert r.json() == {"rules": ["main"], "refused": [], "installed": True} and installs == ["aarch64"]
        assert agent.rules == {"other": {"args": ["--a=1"], "url": "https://o.example/", "name": "kept", "service": ""}, "main": rule}
        # installed now: not again
        assert c.post("/api/routers/1/zapret/import", headers=H, json={"rules": {"main": rule}}).json()["installed"] is False
        assert installs == ["aarch64"]


def test_a_saved_config_takes_the_routers_zapret_first_and_its_file_brings_it_back(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeHub, "cached_config", lambda self, r: None, raising=False)
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        agent = ZapretAgent()
        agent.rules = {"main": {"args": ["--b=1"], "url": "https://x.com/", "name": "fast", "service": "twitter", "n": 1},
                       "gone": {"args": ["--b=1"], "url": "https://x.com/", "name": "old", "service": "", "n": 2}}
        monkeypatch.setattr(FakeRouter, "agent", agent)
        monkeypatch.setattr(FakeRouter, "status", {"fingerprint": "fp1", "podkop": {"version": "0.7.24"}, "agent": {"capabilities": ["zapret"]}})
        row = c.post("/api/configs", headers=H, json={"router_id": 1}).json()
        assert row["zapret"] == ["main"]                        # a rule the config does not have is left out
        text = c.get(f"/api/configs/{row['id']}/file", headers=H).text
        assert json.loads(text)["zapret"] == {"main": {"args": ["--b=1"], "url": "https://x.com/", "name": "fast", "service": "twitter"}}
        again = c.post("/api/configs/import", headers=H, json={"text": text, "file": "x.json"}).json()
        assert again["zapret"] == ["main"]
        # the router's switches and probe settings go along; its keys do not
        router = json.loads(text)["router"]
        assert set(router) == {"probe"} and "keys" not in router["probe"]
        monkeypatch.setattr(FakeRouter, "status", {"fingerprint": "fp1", "agent": {"capabilities": ["zapret"]},
                                                   "heal": {"strict": ["main", "gone"], "off": ["*"]}, "protected": []})
        row = c.post("/api/configs", headers=H, json={"router_id": 1}).json()
        assert row["zapret"] == [] and {k: v for k, v in row["router"].items() if k != "probe"} == {      # (refused there)
            "kill_switch": ["main"], "protected": [], "heal_off": ["*"]}


SET_STUBS = '''
safe_mode() { return 1; }
zapret_installed() { return 1; }
'''


@pytest.mark.parametrize("rules, ok", [
    ({"youtube": {"args": ["--payload=tls_client_hello"], "url": "https://www.youtube.com/generate_204", "name": "a b"}}, True),
    ({"bad name": {"args": ["--x=1"], "url": "https://a/"}}, False),
    ({"yt": {"args": ["--qnum=1"], "url": "https://a/"}}, False),
    ({"yt": {"args": ["--x=1"], "url": "ftp://a/"}}, False),
    ({"yt": {"args": ["--x=1"], "url": "https://a/", "name": "two\nlines"}}, False)])
def test_set_checks_its_input_before_anything_else(tmp_path, rules, ok):
    p = _sh(tmp_path, SET_STUBS + "\nzapret_set", json.dumps({"rules": rules}))
    # a good request gets as far as "not installed" (the stub); a bad one stops at the check
    assert ("not_installed" in p.stderr) == ok and ("bad_request" in p.stderr) == (not ok), p.stderr


@pytest.mark.parametrize("rule, refused", [("meta", False), ("chatgpt", True), ("vpn_1", True), ("nope", True)])
def test_set_refuses_only_the_rules_with_a_kill_switch_or_protection(tmp_path, rule, refused):
    # 0.29.1 refused every rule as soon as any rule had a kill switch or Protected
    cfg = {"sections": [{"name": n, "type": "section", "options": {"connection_type": ct}}
                        for n, ct in (("meta", "proxy"), ("chatgpt", "proxy"), ("vpn_1", "vpn"))]}
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/strict").write_text("chatgpt\n")
    (tmp_path / "etc/protected").write_text("google_ai\n")
    stubs = "\nsafe_mode() { return 1; }\nzapret_installed() { return 0; }\nseen_json() { echo '" + json.dumps(cfg) + "'; }\n"
    body = {"rules": {rule: {"args": ["--payload=tls_client_hello"], "url": "https://x.com/", "name": "a"}}}
    p = _sh(tmp_path, stubs + "zapret_set", json.dumps(body))
    assert ("zapret works only on proxy rules" in p.stderr) == refused, p.stderr


@pytest.mark.parametrize("body, ok", [
    ({"urls": ["https://x.com/"], "strategies": [{"name": "a", "args": ["--x=1"]}]}, True),
    ({"urls": [], "strategies": [{"name": "a", "args": ["--x=1"]}]}, False),
    ({"urls": ["https://x.com/"], "strategies": [{"name": "a", "args": ["--fwmark=1"]}]}, False)])
def test_check_checks_its_input(tmp_path, body, ok):
    (tmp_path / "zapret").mkdir()
    nf = tmp_path / "zapret/nfqws2"
    nf.write_text("#!/bin/sh\n")
    nf.chmod(0o755)
    p = _sh(tmp_path, '\njob_new() { echo 1-1; }\nspawn() { :; }\ncmd_zapret check', json.dumps(body))
    assert (p.returncode == 0 and '"job"' in p.stdout) == ok, p.stderr


def test_install_checks_file_names(tmp_path):
    for files, ok in [({"nfqws2": "AA==", "lua/zapret-lib.lua.gz": "AA=="}, True), ({"lua/../x": "AA=="}, False), ({"nfqws": "AA=="}, False)]:
        p = _sh(tmp_path, '\nzapret_install', json.dumps({"files": files}))
        assert ("expected {" not in p.stderr) == ok, (files, p.stderr)


def test_where_does_it_go_names_the_rule_and_says_zapret(tmp_path, monkeypatch):
    class RouteAgent:
        async def call(self, *args, payload=None, timeout=60):
            return {"target": "instagram.com", "kind": "domain", "match": {"rule": 3, "outbound": "meta-zapret-out", "list": "meta-user-domains-ruleset"}}

    rt = FakeRouter()
    rt.agent, rt.status = RouteAgent(), {"agent": {"capabilities": ["route"]}}
    rt.config, rt.names, rt.exits = {"config": {"sections": [sec("meta")]}}, {}, {}
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        monkeypatch.setattr(FakeHub, "get", lambda self, rid: rt)
        for now, want in (("meta-zapret-direct", True), ("meta-out", False)):
            rt.proxies = {"groups": [{"name": "meta-zapret-out", "now": now}], "servers": []}
            rule = c.post("/api/routers/1/route", headers=H, json={"target": "instagram.com"}).json()["rule"]
            assert rule["section"] == "meta" and rule["by"] == "domains" and rule["zapret"] is want


def test_a_route_rule_with_its_own_ports_stays_on_the_servers(tmp_path):
    _setup(tmp_path)
    sb = json.loads(json.dumps(SB))
    sb["route"]["rules"][1]["port"] = [5222]
    (tmp_path / "in.json").write_text(json.dumps(sb))
    out = _filter(tmp_path)
    assert [r["outbound"] for r in out["route"]["rules"] if r["outbound"].startswith("meta")] == ["meta-out"]


def test_watch_tells_a_move_to_the_servers_and_back_once_it_holds(tmp_path):
    _setup(tmp_path, rules={"meta": RULES["meta"]})
    now = tmp_path / "now"
    body = '''
TAB=$(printf '\\t')
zapret_running() { jq -c keys "$ZAPRET_RULES"; }
event() { echo "$1 $2" >> "$RUN/events"; }
proxies() { printf '{"proxies": {"meta-zapret-out": {"now": "%s"}}}' "$(cat "$RUN/now")"; }
zapret_watch'''
    seq = ["meta-out", "meta-zapret-direct", "meta-zapret-direct", "meta-out", "meta-out", "meta-out", "meta-zapret-direct", "meta-zapret-direct"]
    told = []
    for n in seq:
        now.write_text(n)
        p = _sh(tmp_path, body)
        assert p.returncode == 0, p.stderr
        ev = tmp_path / "events"
        told.append(ev.read_text().splitlines() if ev.exists() else [])
    # the first moment on the servers (sing-box's first test) is not told; a held move is, once
    assert told[2] == []
    assert told[4] == ['zapret_servers {"section":"meta"}'] and told[5] == told[4]
    assert told[7] == ['zapret_servers {"section":"meta"}', 'zapret_direct {"section":"meta"}']


def test_watch_tests_each_direct_path_itself(tmp_path):
    # sing-box tests a URLTest only while it carries connections; the agent does it every 2 rounds (30 s)
    _setup(tmp_path, rules={"meta": RULES["meta"]})
    p = _sh(tmp_path, '''
TAB=$(printf '\\t')
zapret_running() { jq -c keys "$ZAPRET_RULES"; }
event() { :; }
proxies() { echo '{"proxies": {}}'; }
clash_get() { echo "$*" >> "$RUN/probes"; }
zapret_watch; zapret_watch; zapret_watch; zapret_watch; zapret_watch''')
    assert p.returncode == 0, p.stderr
    probes = (tmp_path / "probes").read_text().splitlines()
    assert probes == ["/proxies/meta-zapret-direct/delay --data-urlencode url=https://www.instagram.com/ --data timeout=6000"] * 3


def test_one_interval_for_sing_box_and_the_agents_own_test(tmp_path):
    _setup(tmp_path, rules={"meta": RULES["meta"]})
    out = _filter(tmp_path)
    assert next(o for o in out["outbounds"] if o["tag"] == "meta-zapret-out")["interval"] == "30s"      # the default
    (tmp_path / "zapret/interval").write_text("60\n")
    out = _filter(tmp_path)
    assert next(o for o in out["outbounds"] if o["tag"] == "meta-zapret-out")["interval"] == "60s"
    p = _sh(tmp_path, '''
TAB=$(printf '\\t')
zapret_running() { jq -c keys "$ZAPRET_RULES"; }
event() { :; }
proxies() { echo '{"proxies": {}}'; }
clash_get() { echo "$*" >> "$RUN/probes"; }
for i in 1 2 3 4 5 6 7 8; do zapret_watch; done''')
    assert p.returncode == 0, p.stderr
    assert len((tmp_path / "probes").read_text().splitlines()) == 2          # every 4 rounds of 15 s
    (tmp_path / "zapret/interval").write_text("7\n")                          # nonsense: the default
    p = _sh(tmp_path, "\nzapret_interval")
    assert p.stdout.strip() == "30"


@pytest.mark.parametrize("interval, ok", [(45, True), (15, True), (20, False), (900, False), ("30", False)])
def test_set_takes_an_interval_by_15_seconds(tmp_path, interval, ok):
    body = {"rules": {"youtube": {"args": ["--payload=tls_client_hello"], "url": "https://www.youtube.com/"}}, "interval": interval}
    p = _sh(tmp_path, SET_STUBS + "\nzapret_set", json.dumps(body))
    assert ("not_installed" in p.stderr) == ok, p.stderr


def test_settings_interval_goes_to_every_router_with_zapret_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeHub, "cached_config", lambda self, r: None, raising=False)
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        agent = ZapretAgent()
        agent.rules = {"meta": {"args": ["--b=1"], "url": "https://www.instagram.com/", "name": "fast", "service": "meta", "n": 1}}
        # the hub's own router (class attributes: the client made it already)
        monkeypatch.setattr(FakeRouter, "agent", agent)
        monkeypatch.setattr(FakeRouter, "status", {"agent": {"capabilities": ["zapret"]}})
        rt = FakeRouter()
        assert c.put("/api/zapret/strategies", headers=H, json={"interval": 20}).status_code == 400
        r = c.put("/api/zapret/strategies", headers=H, json={"interval": 60}).json()
        assert r["interval"] == 60 and r["routers"] == ["home"]
        assert rt.agent.calls[-1][1] == {"interval": 60, "rules": {"meta": {"args": ["--b=1"], "url": "https://www.instagram.com/", "name": "fast", "service": "meta"}}}
        assert c.get("/api/zapret/strategies", headers=H).json()["interval"] == 60
        # and with every change of rules
        c.put("/api/routers/1/zapret", headers=H, json={"rules": {}})
        assert rt.agent.calls[-1][1] == {"rules": {}, "interval": 60}


# --- 1.60.0: zapret mends itself (agent 0.30.0) -----------------------------------------

def test_filter_adds_a_missing_table_instead_of_leaving_the_rules_out(tmp_path):
    _setup(tmp_path, table=False)
    p = _sh(tmp_path, '''
nft() { if [ "$1" = -f ]; then cat > /dev/null; : > "$RUN/table"; else [ -f "$RUN/table" ]; fi; }
zapret_filter "$RUN/in.json" "$RUN/out.json" && cat "$RUN/out.json"''')
    assert p.returncode == 0, p.stderr
    assert "meta-zapret-out" in {o["tag"] for o in json.loads(p.stdout)["outbounds"]}


REPAIR = '''
TAB=$(printf '\\t')
event() { echo "$1 $2" >> "$RUN/events"; }
podkop_start_state() { echo 0; }
reapply_pins() { :; }
hot_apply() { echo "$1" >> "$RUN/hot"; [ ! -f "$RUN/hot-fails" ] || { echo "sing-box did not take the new servers"; return 1; }; }
'''
MISSING = json.dumps({"proxies": {"meta-out": {"now": "main-out"}, "youtube-out": {"now": "main-out"},
                                  "youtube-zapret-out": {"now": "youtube-zapret-direct"}}})


def _events(tmp_path):
    ev = tmp_path / "events"
    return ev.read_text().splitlines() if ev.exists() else []


def test_missing_zapret_outbounds_come_back_after_two_rounds(tmp_path):
    _setup(tmp_path)
    body = REPAIR + f"mkdir -p \"$RUN/zapret\"; zapret_repair '{MISSING}'"
    p = _sh(tmp_path, body)
    assert p.returncode == 0, p.stderr
    assert not (tmp_path / "hot").exists()          # one round: the daemon's own start may still add them
    p = _sh(tmp_path, body)
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "hot").read_text().startswith("zapret: outbounds of meta were missing")
    assert _events(tmp_path) == ['zapret_repaired {"rules":["meta"]}']


def test_a_repair_that_fails_is_told_once_and_not_tried_for_5_minutes(tmp_path):
    _setup(tmp_path)
    (tmp_path / "hot-fails").write_text("")
    body = REPAIR + f"mkdir -p \"$RUN/zapret\"; zapret_repair '{MISSING}'"
    for _ in range(4):
        assert _sh(tmp_path, body).returncode == 0
    assert len((tmp_path / "hot").read_text().splitlines()) == 1
    assert _events(tmp_path) == ['zapret_missing {"rules":["meta"],"reason":"sing-box did not take the new servers"}']


def test_a_kill_switch_rule_left_out_on_purpose_is_not_mended(tmp_path):
    _setup(tmp_path, strict="meta\n")
    body = REPAIR + f"mkdir -p \"$RUN/zapret\"; zapret_repair '{MISSING}'; zapret_repair '{MISSING}'"
    assert _sh(tmp_path, body).returncode == 0
    assert not (tmp_path / "hot").exists() and _events(tmp_path) == []


def test_a_stopped_nfqws2_is_started_again(tmp_path):
    _setup(tmp_path)
    init = tmp_path / "init"
    init.write_text('#!/bin/sh\necho "$1" >> "$(dirname "$0")/init.log"\n')
    init.chmod(0o755)
    p = _sh(tmp_path, REPAIR + '''
zapret_running() { echo '["youtube"]'; }
zapret_init() { :; }
proxies() { :; }
zapret_watch; zapret_watch''')
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "init.log").read_text().split() == ["start"]          # not again within 2 minutes
    assert _events(tmp_path) == ['zapret_restarted {"rules":["meta"]}']


CFG = {"sections": [{"name": "settings", "type": "settings", "options": {}},
                    {"name": "meta", "type": "section", "options": {"connection_type": "proxy"}},
                    {"name": "youtube", "type": "section", "options": {"connection_type": "vpn"}}]}


def test_a_rule_that_is_gone_or_no_proxy_rule_leaves_zapret(tmp_path):
    _setup(tmp_path)
    init = tmp_path / "init"
    init.write_text("#!/bin/sh\n")
    init.chmod(0o755)
    p = _sh(tmp_path, REPAIR + "singbox_pid() { echo 1; }\nseen_json() { echo '" + json.dumps(CFG) + "'; }\nzapret_prune")
    assert p.returncode == 0, p.stderr
    assert list(json.loads((tmp_path / "zapret/rules.json").read_text())) == ["meta"]
    assert _events(tmp_path) == ['zapret_dropped {"rules":["youtube"]}']
    assert "youtube left" in (tmp_path / "hot").read_text()


def test_set_drops_a_stale_rule_and_keeps_an_unchanged_kill_switch_rule(tmp_path):
    # 1.59: one rule that had got a kill switch (or was removed) refused every later change
    _setup(tmp_path, rules={"meta": dict(RULES["meta"], service=""), "gone": dict(RULES["youtube"], n=2, service="")},
           strict="meta\n")
    cfg = {"sections": CFG["sections"] + [{"name": "chatgpt", "type": "section", "options": {"connection_type": "proxy"}}]}
    stubs = REPAIR + "safe_mode() { return 1; }\nzapret_init() { :; }\nsingbox_pid() { :; }\nseen_json() { echo '" + json.dumps(cfg) + "'; }\n"
    new = {"args": ["--payload=tls_client_hello"], "url": "https://chatgpt.com/", "name": "c", "service": ""}
    old = {k: {f: v[f] for f in ("args", "url", "name", "service")} for k, v in
           json.loads((tmp_path / "zapret/rules.json").read_text()).items()}
    p = _sh(tmp_path, stubs + "zapret_set", json.dumps({"rules": {**old, "chatgpt": new}}))
    assert p.returncode == 0, p.stderr
    assert sorted(json.loads((tmp_path / "zapret/rules.json").read_text())) == ["chatgpt", "meta"]
    # turning zapret on for a kill-switch rule is still refused
    p = _sh(tmp_path, stubs + "zapret_set", json.dumps({"rules": {"meta": dict(old["meta"], url="https://x.com/")}}))
    assert "zapret works only on proxy rules" in p.stderr


@pytest.mark.parametrize("now, strict, running, want", [
    ({"meta-out": "", "meta-zapret-out": "meta-zapret-direct"}, "", ["meta"], "zapret"),
    ({"meta-out": "", "meta-zapret-out": "meta-out"}, "", ["meta"], "servers"),
    ({"meta-out": ""}, "", ["meta"], "missing"),
    ({"meta-out": "", "meta-zapret-out": "meta-zapret-direct"}, "", [], "stopped"),
    ({"meta-out": ""}, "meta\n", ["meta"], "refused"),
    ({}, "", ["meta"], "unknown")])
def test_each_rule_has_a_state(tmp_path, now, strict, running, want):
    _setup(tmp_path, rules={"meta": RULES["meta"]}, strict=strict)
    (tmp_path / "now.json").write_text(json.dumps(now))
    p = _sh(tmp_path, f"\nzapret_running() {{ echo '{json.dumps(running)}'; }}\nzapret_state \"$RUN/now.json\"")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"meta": want}
