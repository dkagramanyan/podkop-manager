"""1.54.0: config notes, masked subscription addresses, one-rule switches, the
support report, the open-files alert, unlock answers and the route answer."""
import json
import time

from podkop_manager import podkop, report, usage
from podkop_manager.hub import Hub, RouterRuntime
from test_web import H, FakeRouter, client

LOGIN = {"user": "admin", "password": "secret-pass"}


def sec(name, **o):
    return {"name": name, "type": "section", "anonymous": False, "options": o}


def test_notes_name_what_podkop_accepts_but_is_probably_not_meant():
    sel = '{"type": "selector", "outbounds": ["main-out"]}'
    cfg = {"sections": [
        {"name": "settings", "type": "settings", "options": {}},
        sec("ai", connection_type="proxy", proxy_config_type="urltest", user_domain_list_type="text",
            user_domains_text="claude.ai\nclaude.ai\nintercom.io // support chat\n",
            user_subnet_list_type="text", user_subnets_text="",
            urltest_proxy_links=["vless://a@h1:1#A", "vless://b@h2:1#B"], urltest_fallback_links=["vless://a@h1:1#A again"],
            outbound_json=sel),
        sec("chat", connection_type="proxy", proxy_config_type="outbound", outbound_json=sel,
            user_domain_list_type="dynamic", user_domains=["js.intercom.io", "chatgpt.com"], community_lists=["meta"]),
        sec("apple", connection_type="proxy", proxy_config_type="outbound", outbound_json=sel,
            user_domain_list_type="text", user_domains_text="chatgpt.com", community_lists=["meta"]),
        sec("main", connection_type="proxy", proxy_config_type="urltest", user_domain_list_type="disabled")]}
    got = {(n["section"], n["option"]): n["message"] for n in podkop.notes(cfg)}
    assert got[("ai", "user_domains_text")] == "listed twice: claude.ai"
    assert "list is empty" in got[("ai", "user_subnets_text")]
    assert got[("ai", "urltest_fallback_links")].startswith("1 of 1 fallback servers is also among")
    assert "left over" in got[("ai", "outbound_json")]
    assert got[("chat", "user_domains")].startswith("js.intercom.io: rule ai comes first") and "ai's servers" in got[("chat", "user_domains")]
    assert "the same servers" in got[("apple", "user_domains_text")]          # both are selectors of main-out
    assert got[("apple", "community_lists")] == "community list meta is also in rule chat, which comes first"
    assert not any(n["section"] == "main" for n in podkop.notes(cfg))


def test_subscription_addresses_are_masked():
    assert usage.mask("https://sub.example.com/s/abcdef123456k9Q") == "sub.example.com/s/…k9Q"
    assert usage.mask("https://user:pw@sub.example.com/x") == "sub.example.com/…"
    assert usage.mask("https://sub.example.com") == "sub.example.com"
    assert usage.shown({"kind": "link", "url": "vless://uuid@h.example:443?pbk=k#n"}) == "vless://…@h.example"


def test_probe_settings_and_usage_never_carry_the_address(tmp_path):
    url = "https://sub.example.com/s/secretsecret42"
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data=LOGIN)
        assert c.put("/api/routers/1/probe", json={"keys": [url]}, headers=H).json()["keys"] == ["sub.example.com/s/…t42"]
        assert c.get("/api/routers/1/probe?settings=1", headers=H).json()["settings"]["keys"] == ["sub.example.com/s/…t42"]
        assert "secretsecret" not in c.get("/api/routers/1/usage", headers=H).text
        # a masked address sent back would delete the key: refused
        assert c.put("/api/routers/1/probe", json={"keys": ["sub.example.com/s/…t42"]}, headers=H).status_code == 400
        assert c.db.probe_settings(1)["keys"] == [url]


def test_a_switch_changes_one_rule_of_the_routers_own_list(tmp_path, monkeypatch):
    sent = []

    class Agent:
        async def call(self, cmd, *a, payload=None, **kw):
            sent.append((cmd, payload))
            return {"strict": payload["sections"]}
    monkeypatch.setattr(FakeRouter, "agent", Agent())
    monkeypatch.setattr(FakeRouter, "status", {"fingerprint": "fp1", "heal": {"strict": ["a", "b"]}, "protected": None})
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data=LOGIN)
        c.put("/api/routers/1/strict", json={"section": "c", "on": True}, headers=H)
        c.put("/api/routers/1/strict", json={"section": "a", "on": False}, headers=H)
        assert sent == [("strict", {"sections": ["a", "b", "c"]}), ("strict", {"sections": ["b"]})]
        assert c.put("/api/routers/1/protect", json={"section": "a", "on": True}, headers=H).status_code == 409     # list unknown
        c.put("/api/routers/1/strict", json={"sections": ["x"]}, headers=H)                                          # old pages
        assert sent[-1] == ("strict", {"sections": ["x"]})


def test_report_removes_secrets_and_names_servers_by_number():
    cfg = {"sections": [sec("main", connection_type="proxy", proxy_config_type="urltest", urltest_proxy_links=[
        "vless://0ec76309-f67e-4c1c-be13-2d9f143f3122@de1.vpn.example:8443?type=grpc&serviceName=s3cret&security=reality"
        "&sni=de1.vpn.example&pbk=PUBKEY&sid=abcd#Germany de1.vpn.example",
        "ss://Y2hhY2hhMjA6cGFzcw@5.6.7.8:8388#plain"]),
        sec("x", connection_type="proxy", proxy_config_type="outbound",
            outbound_json='{"type":"vless","server":"203.0.113.9","uuid":"0ec76309-f67e-4c1c-be13-2d9f143f3122"}')]}
    status = {"agent": {"version": "0.28.0"}, "proxies": {"groups": [{"name": "main-urltest-out", "type": "URLTest",
              "now": "main-1-out", "all": ["main-1-out", "main-2-out"]}],
              "servers": [{"name": "main-1-out", "delay": 90}, {"name": "main-2-out", "delay": None}]}}
    text = report.build({"name": "home"}, status, cfg, [{"t": 0, "type": "group_down", "data": {}}], [],
                        ["sing-box: dial tcp 5.6.7.8:8388: i/o timeout (de1.vpn.example)", "dial 203.0.113.9:443",
                         "probe: key 1 (https://sub.example/s/tok123): device limit",
                         "outbound/vless[main-1-out]: dial tcp 198.51.100.7:8443: i/o timeout",
                         'outbound main-2-out download test could not run: Get "https://x/": dial tcp 198.51.100.8:8388: i/o timeout', "entry 192.0.2.44 refused"],
                        now=0, hide=["https://sub.example/s/tok123"], entry_ips={"de1.vpn.example": "192.0.2.44"})
    for secret in ("0ec76309", "de1.vpn.example", "5.6.7.8", "203.0.113.9", "PUBKEY", "s3cret", "abcd", "Y2hhY2hh", "Germany", "tok123", "198.51.100.7", "198.51.100.8", "192.0.2.44"):
        assert secret not in text, secret
    assert "vless://…@host-1:8443?type=grpc&serviceName=…&security=reality&sni=…&pbk=…&sid=…" in text
    assert "dial tcp host-2:8388" in text and "dial host-3:443" in text
    assert "[main-1-out]: dial tcp host-1:8443" in text and "entry host-1 refused" in text     # the same server as its name
    assert "main-2-out download test could not run: Get \"https://x/\": dial tcp host-2:8388" in text
    assert "main-urltest-out: URLTest, now main-1-out, 1 of 2 servers answered" in text


def test_open_files_alert_once_and_clears(tmp_path):
    events = []
    rt = RouterRuntime({"id": 1, "host": "h", "port": 22, "host_key": "k", "name": "r"}, tmp_path / "key")
    hub = Hub.__new__(Hub)
    hub._event = lambda r, t, d: (events.append(t), setattr(r, "alert", {"type": t, "t": time.time()}) if t == "fds_high" else None)
    for n in (100, 2600, 2700, 1700, 1500):
        rt.status = {"singbox": {"fds": {"open": n, "limit": 4096}}}
        hub._check_fds(rt)
    assert events == ["fds_high", "fds_ok"] and rt.alert is None
    rt.status = {"singbox": {"fds": None}}
    hub._check_fds(rt)                     # sing-box not running: nothing to say
    assert events == ["fds_high", "fds_ok"]


def test_unlock_answers_leave_out_servers_a_rule_cannot_use():
    rule = podkop.check_auto_rule({"unlock": ["netflix", "claude"]})
    assert rule["unlock"] == ["netflix", "claude"]
    why = lambda u: podkop._unlock_why({"unlock": u}, rule["unlock"])     # noqa: E731
    assert why("netflix:ok:LT claude:ok:LT") == ""
    assert why("netflix:orig:- claude:ok:LT") == "Netflix shows only its own titles here"
    assert why("netflix:ok:LT claude:ok:RU") == "Claude sees RU, where it is not offered"
    assert why("chatgpt:ok:RU") == "" and why("") == ""                   # not asked: not held against it
    rows = podkop.probe_csv_rows("h\n" + "1,GOOD,20,20,1,2,3,50,1.2.3.4,LT,-,,\"n\"\n", "1\tvless://a@h:1#n", "1\tnetflix:no:-\n")
    assert rows[0]["unlock"] == "netflix:no:-"
    try:
        podkop.check_auto_rule({"unlock": ["hbo"]})
        raise AssertionError("hbo accepted")
    except ValueError:
        pass


def test_probe_asks_services_only_a_new_probe_knows(tmp_path):
    from podkop_manager.db import DB
    db = DB(tmp_path / "m.db")
    rid = db.add_router("r", "h", 22, "k")
    db.set_auto_rule(rid, "netflix", podkop.check_auto_rule({"countries": ["LT"], "unlock": ["netflix"]}))
    hub = Hub.__new__(Hub)
    hub.db, hub.routers = db, {rid: type("R", (), {"status": {"probe": {"version": "1.53.0"}}})()}
    assert "--unlock" not in hub.probe_options(rid)
    hub.routers[rid].status = {"probe": {"version": "1.54.0"}}
    opts = hub.probe_options(rid)
    assert opts[opts.index("--unlock") + 1] == "netflix"
    assert db.auto_rule(rid, "netflix")["unlock"] == ["netflix"]


def test_route_answer_names_the_rule_its_server_and_exit(tmp_path, monkeypatch):
    answer = {"target": "chatgpt.com", "kind": "domain", "match": {"rule": 9, "outbound": "chatgpt-out", "list": "chatgpt-user-domains-ruleset",
              "every": False}, "final": "direct-out", "unread": [], "dns": {"address": "198.18.0.3", "fakeip": True}, "nft_set": None, "seen": None}

    class Agent:
        async def call(self, cmd, *a, payload=None, **kw):
            assert cmd == "route" and payload == {"target": "chatgpt.com", "from": ""}
            return dict(answer)
    sel = '{"type": "selector", "outbounds": ["main-out"]}'
    monkeypatch.setattr(FakeRouter, "agent", Agent())
    monkeypatch.setattr(FakeRouter, "status", {"fingerprint": "fp1", "agent": {"capabilities": ["route"]}, "heal": {"strict": ["chatgpt"]}})
    monkeypatch.setattr(FakeRouter, "config", {"config": {"sections": [sec("chatgpt", connection_type="proxy", proxy_config_type="outbound",
                                                                                 outbound_json=sel)]}}, raising=False)
    monkeypatch.setattr(FakeRouter, "proxies", {"groups": [{"name": "chatgpt-out", "now": "main-out"}, {"name": "main-out", "now": "main-2-out"}]}, raising=False)
    monkeypatch.setattr(FakeRouter, "names", {"main-2-out": "Latvia gRPC"}, raising=False)
    monkeypatch.setattr(FakeRouter, "exits", {"chatgpt": {"ip": "1.2.3.4", "country": "LV"}}, raising=False)
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data=LOGIN)
        r = c.post("/api/routers/1/route", json={"target": "https://ChatGPT.com/c/123"}, headers=H).json()
        assert r["rule"] == {"section": "chatgpt", "known": True, "type": "proxy", "by": "domains", "list": "chatgpt-user-domains-ruleset",
                             "server": "Latvia gRPC", "exit": {"ip": "1.2.3.4", "country": "LV"}, "kill_switch": True}
        assert c.post("/api/routers/1/route", json={"target": "a b"}, headers=H).status_code == 400
        answer["match"] = {"rule": 2, "outbound": "reject", "list": "ads-user-domains-ruleset", "every": False}
        assert c.post("/api/routers/1/route", json={"target": "chatgpt.com"}, headers=H).json()["rule"]["type"] == "block"
        monkeypatch.setattr(FakeRouter, "status", {"fingerprint": "fp1", "agent": {"capabilities": []}})
        assert c.post("/api/routers/1/route", json={"target": "chatgpt.com"}, headers=H).status_code == 409
