"""Devices and "where does it go" (agent 0.28): the router's device list and
the rule a name or an address would take, read from sing-box's configuration."""
import json
import os
import subprocess
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "router/podkop-agent"


def _code():
    return subprocess.run(["sed", "-n", "/^# devices: what is on the LAN/,/^# --- dispatch/p", str(AGENT)],
                          capture_output=True, text=True, check=True).stdout.rsplit("# --- dispatch", 1)[0]


STUBS = r'''
RUN="$1/run"; D="$1"; mkdir -p "$RUN"; TAB=$(printf '\t')
reply() { printf '%s\n' "$1"; exit 0; }
fail() { echo "$1: $2" >&2; exit 1; }
uci() {
  case "$*" in
    "-q get podkop.settings.config_path") echo "$D/config.json" ;;
    "-q get podkop.settings.source_network_interfaces") echo br-lan ;;
    "-q show dhcp") cat "$D/dhcp.uci" 2>/dev/null ;;
  esac
}
ip() { cat "$D/neigh" 2>/dev/null; }
nslookup() {
  case "$1" in
    *.fake.example|chatgpt.com) printf 'Server: 127.0.0.1\n\nName: %s\nAddress: 198.18.0.9\n' "$1" ;;
    *) printf 'Server: 127.0.0.1\n\nName: %s\nAddress: 93.184.215.14\n' "$1" ;;
  esac
}
nft() { case "$1 $2" in "list sets") printf 'table inet PodkopTable {\n\tset podkop_subnets {\n' ;; "get element") grep -q "$5" "$D/nft-set" 2>/dev/null ;; esac; }
guard_fetch() { echo "$1" >> "$D/fetched"; [ -f "$D/remote.lst" ] && cp "$D/remote.lst" "$2"; }
'''


# sing-box rule-set match <-f fmt> <file> <target>: the "list" files here are plain
# text, one domain suffix or a /24 per line
SING_BOX = r'''#!/bin/sh
t=$6
while IFS= read -r l; do
  case "$t" in "$l"|*".$l") echo "match rules.[0]"; exit 0 ;; esac
  case "$l" in */24) [ "${t%.*}" = "${l%.*}" ] && { echo "match rules.[0]"; exit 0; } ;; esac
done < "$5"
'''


def _sh(tmp_path, body, stdin=""):
    b = tmp_path / "bin"
    if not b.exists():
        b.mkdir()
        (b / "sing-box").write_text(SING_BOX)
        (b / "sing-box").chmod(0o755)
    return subprocess.run(["sh", "-c", STUBS + _code() + body, "sh", str(tmp_path)], input=stdin,
                          capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PATH": f"{b}:{os.environ['PATH']}"})


def lst(tmp_path, name, *lines):
    (tmp_path / name).write_text("\n".join(lines) + "\n")
    return str(tmp_path / name)


def config(tmp_path):
    rs = lambda tag, path: {"type": "local", "tag": tag, "format": "source", "path": path}     # noqa: E731
    cfg = {"route": {"final": "direct-out", "rule_set": [
        rs("blocked-user-domains-ruleset", lst(tmp_path, "b.lst", "ads.example")),
        rs("chatgpt-user-domains-ruleset", lst(tmp_path, "c.lst", "chatgpt.com", "openai.com")),
        rs("meta-user-subnets-ruleset", lst(tmp_path, "m.lst", "31.13.24.0/24")),
        {"type": "remote", "tag": "main-telegram-community-ruleset", "format": "binary", "url": "https://lists.example/telegram.srs"},
        rs("banks-user-domains-ruleset", lst(tmp_path, "x.lst", "bank.example"))],
        "rules": [
            {"action": "sniff", "inbound": ["tproxy-in", "dns-in"]},
            {"action": "reject", "inbound": "tproxy-in", "rule_set": "blocked-user-domains-ruleset"},
            {"action": "route", "inbound": "tproxy-in", "outbound": "direct-out", "rule_set": "banks-user-domains-ruleset"},
            {"action": "route", "inbound": "tproxy-in", "outbound": "direct-out", "source_ip_cidr": ["192.168.1.9/32"]},
            {"action": "route", "inbound": "tproxy-in", "outbound": "vpn_1-out", "source_ip_cidr": ["192.168.1.20/32"]},
            {"action": "route", "inbound": "tproxy-in", "outbound": "chatgpt-out", "domain_suffix": ["own.example"],
             "rule_set": "chatgpt-user-domains-ruleset"},
            {"action": "route", "inbound": "tproxy-in", "outbound": "meta-out", "rule_set": "meta-user-subnets-ruleset"},
            {"action": "route", "inbound": "tproxy-in", "outbound": "main-out", "rule_set": ["main-telegram-community-ruleset"]}]}}
    (tmp_path / "config.json").write_text(json.dumps(cfg))


def route(tmp_path, target, source=""):
    p = _sh(tmp_path, "\ncmd_route", json.dumps({"target": target, "from": source}))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_the_first_rule_in_order_takes_it(tmp_path):
    config(tmp_path)
    r = route(tmp_path, "api.chatgpt.com")
    assert r["match"] == {"rule": 5, "outbound": "chatgpt-out", "list": "chatgpt-user-domains-ruleset", "every": False}
    assert route(tmp_path, "own.example")["match"]["list"] is None          # written in the rule itself
    assert route(tmp_path, "ads.example")["match"]["outbound"] == "reject"  # a block section
    assert route(tmp_path, "bank.example")["match"]["outbound"] == "direct-out"
    m = route(tmp_path, "31.13.24.7")
    assert m["match"]["outbound"] == "meta-out" and m["kind"] == "ip" and m["dns"] is None


def test_a_device_route_needs_the_device(tmp_path):
    config(tmp_path)
    assert route(tmp_path, "example.org")["match"] is None                   # no device given: device rules skipped
    r = route(tmp_path, "example.org", "192.168.1.20")
    assert r["match"] == {"rule": 4, "outbound": "vpn_1-out", "list": None, "every": True}
    assert route(tmp_path, "chatgpt.com", "192.168.1.9")["match"]["outbound"] == "direct-out"   # never routed


def test_community_lists_are_downloaded_once_and_unread_ones_named(tmp_path):
    config(tmp_path)
    r = route(tmp_path, "t.me")                       # the download fails: named, not guessed
    assert r["match"] is None and r["unread"] == ["main-telegram-community-ruleset"]
    r = route(tmp_path, "t.me")                       # not asked again within ten minutes
    assert (tmp_path / "fetched").read_text().count("\n") == 1
    for f in (tmp_path / "run/route").glob("*.bad"):
        f.unlink()
    lst(tmp_path, "remote.lst", "t.me")
    assert route(tmp_path, "t.me")["match"]["outbound"] == "main-out"
    route(tmp_path, "web.t.me")                       # kept: no new download
    assert (tmp_path / "fetched").read_text().count("\n") == 2


def test_bad_targets_are_refused(tmp_path):
    config(tmp_path)
    for t in ("", "a b", "x;rm", "../etc"):
        assert _sh(tmp_path, "\ncmd_route", json.dumps({"target": t})).returncode != 0
    assert _sh(tmp_path, "\ncmd_route", json.dumps({"target": "a.example", "from": "1.2.3.x"})).returncode != 0


def test_devices_from_leases_fixed_leases_and_neighbours(tmp_path):
    (tmp_path / "dhcp.uci").write_text(
        "dhcp.lan=dhcp\ndhcp.lan.interface='lan'\n"
        "dhcp.cfg01=host\ndhcp.cfg01.name='nas'\ndhcp.cfg01.mac='AA:BB:CC:00:00:01'\ndhcp.cfg01.ip='192.168.1.5'\n"
        "dhcp.cfg02=host\ndhcp.cfg02.mac='aa:bb:cc:00:00:02'\n")             # no ip: not a device of its own
    (tmp_path / "neigh").write_text("192.168.1.5 lladdr aa:bb:cc:00:00:01 REACHABLE\n"
                                    "192.168.1.30 lladdr aa:bb:cc:00:00:30 STALE\n192.168.1.31 FAILED\n")
    p = _sh(tmp_path, "\ncmd_devices")
    assert p.returncode == 0, p.stderr
    d = {x["ip"]: x for x in json.loads(p.stdout)["devices"]}
    assert set(d) == {"192.168.1.5", "192.168.1.30"}
    assert d["192.168.1.5"] == {"ip": "192.168.1.5", "mac": "aa:bb:cc:00:00:01", "name": "nas", "fixed": True, "lease": False}
    assert d["192.168.1.30"]["fixed"] is False
