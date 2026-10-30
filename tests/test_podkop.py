import pytest

from podkop_manager import podkop


def cfg(*sections):
    return {"sections": [{"name": n, "type": t, "anonymous": False, "options": o} for n, t, o in sections]}


BASE = cfg(("settings", "settings", {"dns_type": "udp", "shutdown_correctly": "1"}),
           ("main", "section", {"connection_type": "proxy", "proxy_config_type": "url",
                                "proxy_string": "vless://a@h:1#A", "community_lists": ["russia_inside"]}))


def test_diff_ignores_podkop_own_option():
    m = BASE["sections"][1]
    new = cfg(("settings", "settings", {"dns_type": "udp", "shutdown_correctly": "0"}), (m["name"], m["type"], m["options"]))
    assert podkop.diff(BASE, new) == []


def test_diff_changes_added_removed_order():
    new = cfg(("yt", "section", {"connection_type": "block"}),
              ("settings", "settings", {"dns_type": "doh", "shutdown_correctly": "1"}))
    d = podkop.diff(BASE, new)
    assert {"section": "yt", "change": "added", "type": "section", "options": {"connection_type": "block"}} in d
    assert {"section": "main", "change": "removed"} in d
    assert {"section": "settings", "option": "dns_type", "change": "changed", "old": "udp", "new": "doh"} in d


def test_diff_order():
    a = cfg(("settings", "settings", {}), ("main", "section", {}), ("yt", "section", {}))
    b = cfg(("settings", "settings", {}), ("yt", "section", {}), ("main", "section", {}))
    assert podkop.diff(a, b) == [{"change": "order", "old": ["settings", "main", "yt"], "new": ["settings", "yt", "main"]}]


def test_merge_probe_fills_rules_and_drops_old_fallbacks():
    pick = {"links": ["vless://1#a", "vless://2#b"], "fallback": ["vless://3#c"], "youtube": False}
    out = podkop.merge_probe(BASE, {"main": pick})
    main = out["sections"][1]["options"]
    assert main["proxy_config_type"] == "urltest"
    assert main["urltest_proxy_links"] == pick["links"] and main["urltest_fallback_links"] == ["vless://3#c"]
    assert main["community_lists"] == ["russia_inside"] and "urltest_testing_url" not in main   # untouched
    assert BASE["sections"][1]["options"]["proxy_config_type"] == "url"   # input not modified
    again = podkop.merge_probe(out, {"main": dict(pick, fallback=[], youtube=True)})
    o = again["sections"][1]["options"]
    assert "urltest_fallback_links" not in o and o["urltest_testing_url"] == podkop.YOUTUBE_PING_URL
    assert podkop.merge_probe(BASE, {"main": {"links": [], "fallback": []}}) == BASE


def test_schema_names_are_unique():
    for part in ("settings", "section"):
        names = [o["name"] for o in podkop.SCHEMA[part]]
        assert len(names) == len(set(names))


def test_tag_names():
    c = podkop.merge_probe(BASE, {"main": {"links": ["vless://1@h:1#%F0%9F%87%B1%F0%9F%87%B9%20A", "vless://2@h2:3?x=1"],
                                           "fallback": ["trojan://p@h3:4#C+D"]}})
    n = podkop.tag_names(c)
    assert n["main-1-out"] == "🇱🇹 A" and n["main-2-out"] == "h2:3" and n["main-fallback-1-out"] == "C D"
    assert n["main-urltest-out"] == "automatic"


def test_parse_log_sources_and_levels():
    p = podkop.parse_log("Sat Oct 17 23:18:40 2026 user.notice podkop: [info] Stopping the podkop")
    assert p == {"time": "23:18:40", "source": "podkop", "level": "info", "msg": "Stopping the podkop"}
    p = podkop.parse_log("Sat Oct 17 23:19:24 2026 user.notice podkop: [fatal] Sing-box configuration is invalid.")
    assert p["level"] == "error" and p["msg"] == "Sing-box configuration is invalid."
    p = podkop.parse_log("Sat Oct 17 23:18:48 2026 daemon.err sing-box[14682]: WARN[0000] outbound/vless[a-1-out]: slow")
    assert (p["source"], p["level"], p["msg"]) == ("sing-box", "warn", "outbound/vless[a-1-out]: slow")
    # sing-box writes to stderr, so daemon.err alone is not an error
    p = podkop.parse_log("Sat Oct 18 16:08:03 2026 daemon.err podkop[1]: Connecting to 198.18.0.4:443")
    assert (p["source"], p["level"]) == ("podkop", "info")              # wget's progress, not an error
    p = podkop.parse_log("Sat Oct 18 09:13:00 2026 cron.err crond[1]: USER root pid 17803 cmd /usr/bin/podkop list_update")
    assert p["level"] == "info"
    p = podkop.parse_log("Sat Oct 18 09:13:00 2026 cron.err crond[1]: can't open crontab")
    assert p["level"] == "error"
    p = podkop.parse_log("Sat Oct 17 23:18:48 2026 daemon.err sing-box[14682]: started")
    assert p["level"] == "info"
    p = podkop.parse_log("Sat Oct  4 03:00:01 2026 user.notice podkop-agent: apply 20261004-1")
    assert (p["source"], p["time"]) == ("agent", "03:00:01")
    assert podkop.parse_log("Sat Oct 17 23:18:48 2026 daemon.err sing-box[1]: ERROR[0001] failsafe: stop")["source"] == "engine"
    assert podkop.parse_log("garbage")["msg"] == "garbage"


def test_probe_rows_goes_to_and_reasons():
    st = {"rows": [{"node": "A", "link": "vless://a", "verdict": "GOOD", "median": "0.3", "mbit": "90"},
                   {"node": "B", "link": "vless://b", "verdict": "OK", "median": "0.4", "mbit": "80"},
                   {"node": "C", "link": "vless://c", "verdict": "OK", "median": "0.9", "mbit": "20"},
                   {"node": "D", "link": "vless://d", "verdict": "OK", "median": "0.2", "mbit": "-"},
                   {"node": "E", "verdict": "DEAD", "median": "-", "mbit": "-"},
                   {"node": "F", "verdict": "OTHER REGION", "country": "RU"},
                   {"node": "G", "verdict": "SKIPPED"}]}
    picks = {"main": {"links": ["vless://a"], "fallback": []}, "youtube": {"links": ["vless://a"], "fallback": ["vless://b"]}}
    rows = {r["node"]: r for r in podkop.probe_rows(st, ["--max-ping", "800", "--countries", "europe"], picks)}
    assert rows["A"]["goes_to"] == [{"rule": "main", "fallback": False}, {"rule": "youtube", "fallback": False}]
    assert rows["A"]["reason"] == "" and rows["B"]["goes_to"] == [{"rule": "youtube", "fallback": True}]
    assert rows["C"]["reason"] == "ping 900 ms, 800 or more; too slow, 40 Mbit/s or less"
    assert rows["D"]["reason"] == ""     # works, the lists were full
    assert "failed" in rows["E"]["reason"] and "RU" in rows["F"]["reason"] and "podkop" in rows["G"]["reason"]


def test_probe_keys():
    log = ["==> servers", "  key 1  sub.example.com            42 servers  · 10 GB left",
           "! key 2 (link.example.org): the key's device limit is reached (x): remove an old device"]
    assert podkop.probe_keys(log, ["https://a", "https://b"]) == {
        "https://a": {"servers": 42}, "https://b": {"error": "the key's device limit is reached (x): remove an old device"}}
    assert podkop.probe_keys(log, ["https://a"]) == {"https://a": {"servers": 42}}   # key 2 was not in that run


ROWS = [{"node": "PL Warsaw 5", "link": "vless://a@h:1#W5", "verdict": "GOOD", "median": "0.47", "mbit": "138",
         "country": "PL", "exit_ip": "203.0.113.41"},
        {"node": "PL Krakow 1", "link": "vless://b@h:1#K1", "verdict": "OK", "median": "0.58", "mbit": "96",
         "country": "PL", "exit_ip": "198.51.100.12"},
        {"node": "PL Gdansk", "link": "trojan://c@h:1#G", "verdict": "GOOD", "median": "0.3", "mbit": "99",
         "country": "PL", "exit_ip": "198.51.100.77"},
        {"node": "PL Slow", "link": "vless://d@h:1#S", "verdict": "OK", "median": "0.4", "mbit": "12",
         "country": "PL", "exit_ip": "198.51.100.78"},
        {"node": "PL Fast", "link": "vless://e@h:1#F", "verdict": "GOOD", "median": "0.2", "mbit": "300",
         "country": "PL", "exit_ip": "198.51.100.79"},
        {"node": "DE", "link": "vless://f@h:1#D", "verdict": "GOOD", "median": "0.1", "mbit": "300", "country": "DE",
         "youtube": "ok:DE"},
        {"node": "DE RU", "link": "vless://g@h:1#R", "verdict": "GOOD", "median": "0.45", "mbit": "60", "country": "DE",
         "youtube": "ok:RU"},
        {"node": "no link", "verdict": "GOOD", "median": "0.1", "mbit": "300", "country": "PL"}]


def test_auto_pick_filters_limits_and_order():
    rule = podkop.check_auto_rule({"countries": ["pl"], "protocols": ["vless"], "top": 2, "fallback": 1})
    p = podkop.auto_pick(ROWS, rule)
    assert p["links"] == ["vless://e@h:1#F", "vless://a@h:1#W5"] and p["fallback"] == ["vless://b@h:1#K1"]
    why = {x["name"]: x["reason"] for x in p["left_out"]}
    assert "trojan" in why["PL Gdansk"] and "Mbit/s" in why["PL Slow"]
    assert "DE" not in why and "no link" not in why      # other countries are not "left out"
    ip = podkop.check_auto_rule({"countries": ["PL"], "exit_ips": ["198.51.100.0/24"], "name_contains": "krak"})
    assert podkop.auto_pick(ROWS, ip)["links"] == ["vless://b@h:1#K1"]
    # no countries = any; limits and YouTube per rule
    yt = podkop.check_auto_rule({"youtube": "ru", "max_ping": 500, "min_speed": 50})
    p = podkop.auto_pick(ROWS, yt)
    assert p["links"] == ["vless://g@h:1#R"] and p["youtube"]
    why = {x["name"]: x["reason"] for x in p["left_out"]}
    assert "not RU" in why["DE"] and "not checked" in why["PL Fast"] and "500" in why["PL Krakow 1"]
    de = podkop.auto_pick(ROWS, dict(yt, youtube="DE"))           # any region YouTube detects
    assert [m["name"] for m in de["matched"]] == ["DE"] and "not DE" in {x["name"]: x["reason"] for x in de["left_out"]}["DE RU"]


def test_check_auto_rule_refuses_bad_input():
    for bad in ({"countries": ["Poland"]}, {"protocols": ["vmess"]}, {"exit_ips": ["10.0.0.0/33"]}, {"top": 0},
                {"max_ping": 50}, {"youtube": "maybe"}, {"min_speed": "fast"}):
        with pytest.raises(ValueError):
            podkop.check_auto_rule(bad)


def test_merge_probe_only_existing_sections():
    base = cfg(("settings", "settings", {}), ("netflix", "section", {"connection_type": "proxy"}))
    out = podkop.merge_probe(base, {"netflix": {"links": ["vless://a"], "fallback": []},
                                    "gone": {"links": ["vless://b"], "fallback": []}})
    assert out["sections"][1]["options"]["urltest_proxy_links"] == ["vless://a"] and len(out["sections"]) == 2


def test_fix_fingerprint():
    f = podkop.fix_fingerprint
    assert f("vless://u@h:443?security=reality&fp=qq&pbk=x#DE fp=qq") == "vless://u@h:443?security=reality&fp=chrome&pbk=x#DE fp=qq"
    assert f("vless://u@h:443?fp=360&security=reality") == "vless://u@h:443?fp=chrome&security=reality"
    for same in ("vless://u@h:443?security=reality&fp=chrome#A", "vless://u@h:443?security=tls&fp=qq#A",
                 "vless://u@h:443?security=reality&fp=qqx#A", "trojan://p@h:443#A"):
        assert f(same) == same


def test_probe_rows_match_picks_with_the_fingerprint_fixed():
    link = "vless://u@h:443?security=reality&fp=qq&pbk=x#DE"
    rows = [{"link": link, "verdict": "GOOD", "median": "0.1", "mbit": "90"}]
    picks = {"main": {"links": [podkop.fix_fingerprint(link)], "fallback": []}}
    [row] = podkop.probe_rows({"rows": rows}, [], picks)
    assert row["goes_to"] == [{"rule": "main", "fallback": False}] and row["reason"] == ""


def test_probe_csv_rows_like_the_agent():
    text = ("n,verdict,ok,total,min,median,p90,mbit_s,exit_ip,country,youtube,youtube_ms,node\n"
            '2,GOOD,20,20,0.1,0.2,0.3,50.0,1.2.3.4,NL,ok:RU,0.15,"A, ""x"""\n'
            "1,DEAD,0,4,-,-,-,-,-,-,,,B\n")
    rows = podkop.probe_csv_rows(text, "2\tvless://a@h:1#A\n")
    assert rows[0] == {"n": "2", "verdict": "GOOD", "ok": "20", "total": "20", "min": "0.1", "median": "0.2",
                       "p90": "0.3", "mbit": "50.0", "exit_ip": "1.2.3.4", "country": "NL", "youtube": "ok:RU",
                       "youtube_ms": "0.15", "node": 'A, "x"', "link": "vless://a@h:1#A"}
    assert rows[1]["node"] == "B" and rows[1]["link"] is None
    assert "link" not in podkop.probe_csv_rows(text)[0]


def test_install_command_and_pairing_line():
    from podkop_manager.agent import install_command, parse_pairing
    cmd = install_command("v1.23.0", "ssh-ed25519 AAAA mgr", "ssh-ed25519 BBBB fwd", ["10.0.0.0/24"], False)
    assert cmd.startswith("wget -qO /tmp/podkop-install.sh https://github.com/dkagramanyan/podkop-manager/releases/download/v1.23.0/install.sh && ")
    assert "--release v1.23.0 --key 'ssh-ed25519 AAAA mgr' --forward-key 'ssh-ed25519 BBBB fwd' --keep-clash-open --allow-clash 10.0.0.0/24" in cmd
    assert parse_pairing("  podkop-router 192.168.8.1 2222 ssh-ed25519 AAAAC3Nz+/x=\n") == {
        "host": "192.168.8.1", "port": 2222, "host_key": "ssh-ed25519 AAAAC3Nz+/x="}
    for bad in ("", "podkop-router 1.2.3.4 22 ssh-ed25519", "podkop-router 1.2.3.4 x ssh-ed25519 AAAA",
                "podkop-router 1.2.3.4 22 ssh-dss AAAA", "router 1.2.3.4 22 ssh-ed25519 AAAA"):
        with pytest.raises(ValueError):
            parse_pairing(bad)
