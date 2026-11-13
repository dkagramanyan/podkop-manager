"""OpenVPN client on the router (agent 0.21) and its manager API."""
import json
import subprocess
from pathlib import Path

import pytest

from test_web import H, FakeRouter, client

AGENT = Path(__file__).resolve().parents[1] / "router/podkop-agent"

OVPN = """client
dev tun0
proto udp
remote vpn.example.com 1194 udp
auth-user-pass
redirect-gateway def1
script-security 2
up /tmp/evil.sh
route 10.0.0.0 255.0.0.0
<ca>
-----BEGIN CERTIFICATE-----
up not-a-directive-inside-a-block
-----END CERTIFICATE-----
</ca>
<auth-user-pass>
olduser
oldpass
</auth-user-pass>
# a comment
verb 3
"""


def _vpn_code():
    return subprocess.run(["sed", "-n", "/^# --- OpenVPN client/,/^# --- commands/p", str(AGENT)],
                          capture_output=True, text=True, check=True).stdout


STUBS = '''
RUN="$1"; ETC="$1/etc"; mkdir -p "$ETC"
reply() { printf '%s\\n' "$1"; exit 0; }
fail() { echo "$1: $2" >&2; exit 1; }
event() { echo "$1" >> "$RUN/events"; }
singbox_pid() { cat "$RUN/sbpid" 2>/dev/null; }
hot_apply() { echo hot >> "$RUN/calls"; }
flock() { return 0; }
ubus() { :; }
logread() { :; }
ip() { :; }
'''


def _sh(tmp_path, body, stdin=""):
    code = STUBS + _vpn_code().replace("VPN_DIR=$ETC/vpn", 'VPN_DIR=$ETC/vpn; VPN_INIT="$RUN/podkop-vpn"') + body
    return subprocess.run(["sh", "-c", code, "sh", str(tmp_path)], input=stdin, capture_output=True, text=True, timeout=20)


def test_clean_drops_our_directives_scripts_and_routes_keeps_blocks(tmp_path):
    p = _sh(tmp_path, "\nvpn_clean", OVPN)
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    assert "remote vpn.example.com 1194 udp" in lines and "verb 3" in lines and "client" in lines
    for gone in ("dev tun0", "auth-user-pass", "redirect-gateway def1", "script-security 2", "up /tmp/evil.sh",
                 "route 10.0.0.0 255.0.0.0", "olduser", "# a comment"):
        assert gone not in lines
    # inside an inline block nothing is a directive
    assert "up not-a-directive-inside-a-block" in lines and "<ca>" in lines and "</ca>" in lines


@pytest.mark.parametrize("conf, msg", [("remote a 1\nca ca.crt\n", "file: ca"),
                                       ("remote a 1\n<ca>\nxx\n", "unclosed <ca>")])
def test_clean_refuses_file_references_and_open_blocks(tmp_path, conf, msg):
    p = _sh(tmp_path, "\nvpn_clean", conf)
    assert p.returncode == 2 and msg in p.stderr


def test_filter_limits_only_the_listed_rules_and_leaves_fully_routed_alone(tmp_path):
    cfg = {"route": {"rules": [
        {"action": "route", "inbound": "tproxy-in", "outbound": "vpn_1-out", "rule_set": ["vpn_1-user-domains"]},
        {"action": "route", "inbound": "tproxy-in", "outbound": "vpn_1-out", "source_ip_cidr": ["192.168.1.9"]},
        {"action": "route", "inbound": "tproxy-in", "outbound": "main-out", "rule_set": ["main"]},
        {"action": "reject"}]}}
    (tmp_path / "in.json").write_text(json.dumps(cfg))
    p = _sh(tmp_path, '\nmkdir -p "$VPN_DIR"; echo \'{"vpn_1":["192.168.1.20/32"]}\' > "$VPN_DIR/allow"'
                      '\nvpn_filter "$RUN/in.json" "$RUN/out.json"')
    assert p.returncode == 0, p.stderr
    rules = json.loads((tmp_path / "out.json").read_text())["route"]["rules"]
    assert rules[0]["source_ip_cidr"] == ["192.168.1.20/32"]
    assert rules[1]["source_ip_cidr"] == ["192.168.1.9"]
    assert "source_ip_cidr" not in rules[2] and rules[3] == {"action": "reject"}
    # no list: the config as it is
    (tmp_path / "etc/vpn/allow").unlink()
    p = _sh(tmp_path, '\nvpn_filter "$RUN/in.json" "$RUN/same.json"')
    assert json.loads((tmp_path / "same.json").read_text()) == cfg


def test_set_writes_config_login_and_devices(tmp_path):
    req = {"config": OVPN, "username": "me", "password": "p@ss word", "allow": {"vpn_1": ["192.168.1.20/32"]}}
    p = _sh(tmp_path, "\ncmd_vpn set", json.dumps(req))
    assert p.returncode == 0, p.stderr
    d = tmp_path / "etc/vpn"
    assert (d / "auth").read_text() == "me\np@ss word\n"
    conf = (d / "client.conf").read_text().splitlines()
    assert "dev pkvpn" in conf and "route-nopull" in conf and f"auth-user-pass {d}/auth" in conf
    assert "syslog podkop-vpn" in conf and "script-security 1" in conf and "up /tmp/evil.sh" not in conf
    assert json.loads((d / "allow").read_text()) == {"vpn_1": ["192.168.1.20/32"]}
    st = json.loads(p.stdout)
    assert st["configured"] and st["username"] == "me" and st["password_set"] and "p@ss" not in p.stdout
    assert not (tmp_path / "calls").exists()        # sing-box not running: nothing to reload
    # login again without a password keeps the saved one; devices emptied -> no file, sing-box reloads
    (tmp_path / "sbpid").write_text("123")
    p = _sh(tmp_path, "\ncmd_vpn set", json.dumps({"username": "you", "allow": {"vpn_1": []}}))
    assert p.returncode == 0, p.stderr
    assert (d / "auth").read_text() == "you\np@ss word\n" and not (d / "allow").exists()
    assert (tmp_path / "calls").read_text() == "hot\n"


def test_set_refuses_bad_input_and_on_without_openvpn(tmp_path):
    for bad in ({"allow": {"vpn_1": ["1.2.3.4; rm -rf /"]}}, {"allow": {"a-b": ["1.2.3.4"]}},
                {"username": "a\nb"}, {"enabled": "yes"}):
        p = _sh(tmp_path, "\ncmd_vpn set", json.dumps(bad))
        assert p.returncode == 1 and "bad_request" in p.stderr, bad
    p = _sh(tmp_path, "\ncmd_vpn set", json.dumps({"enabled": True}))
    assert p.returncode == 1 and "import a config first" in p.stderr
    p = _sh(tmp_path, "\ncmd_vpn set", json.dumps({"config": "remote a 1\n", "enabled": True}))
    assert p.returncode == 1 and "not_installed" in p.stderr
    p = _sh(tmp_path, "\ncmd_vpn set", json.dumps({"config": "client\n"}))
    assert p.returncode == 1 and "no remote line" in p.stderr


class RecordingAgent:
    def __init__(self):
        self.calls = []

    async def call(self, *args, payload=None, **kw):
        self.calls.append((args, payload))
        return {"ok": True}


def test_api_checks_and_forwards(tmp_path, monkeypatch):
    agent = RecordingAgent()
    monkeypatch.setattr(FakeRouter, "agent", agent)
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        put = lambda body: c.put("/api/routers/1/vpn", json=body, headers=H)
        assert put({}).status_code == 400
        assert put({"password": "x"}).status_code == 400
        assert put({"allow": {"vpn_1": ["not-an-ip"]}}).status_code == 400
        assert put({"username": "a\nb"}).status_code == 400
        r = put({"username": "me", "password": "pw", "enabled": True,
                 "allow": {"vpn_1": ["192.168.1.20", "192.168.1.20/32", " "], "x": []}})
        assert r.status_code == 200
        assert agent.calls[-1] == (("vpn", "set"), {"username": "me", "password": "pw", "enabled": True,
                                                     "allow": {"vpn_1": ["192.168.1.20/32"]}})
        assert put({"allow": {"*": ["192.168.1.7"]}}).status_code == 200
        assert agent.calls[-1] == (("vpn", "set"), {"allow": {"*": ["192.168.1.7/32"]}})
        assert put({"allow": {"a-b": ["192.168.1.7"]}}).status_code == 400
        test = lambda body: c.post("/api/routers/1/vpn/test", json=body, headers=H)
        assert test({"host": "a;b"}).status_code == 400 and test({"host": "git.example.com", "dns": "x"}).status_code == 400
        assert test({"host": "Git.Example.com", "dns": "10.9.0.1"}).status_code == 200
        assert agent.calls[-1] == (("vpn", "test"), {"host": "git.example.com", "dns": "10.9.0.1"})
        assert c.get("/api/routers/1/vpn/job/1;reboot").status_code == 400
        assert c.get("/api/routers/1/vpn/job/20261111-42").status_code == 200
        assert agent.calls[-1] == (("job", "20261111-42"), None)


def test_config_without_a_login_is_built(tmp_path):
    p = _sh(tmp_path, "\ncmd_vpn set", json.dumps({"config": "client\nremote a 1\n"}))
    assert p.returncode == 0, p.stderr
    conf = (tmp_path / "etc/vpn/client.conf").read_text().splitlines()
    assert "remote a 1" in conf and "dev pkvpn" in conf and 'pull-filter ignore "route"' in conf
    assert not any(l.startswith("auth-user-pass") for l in conf)
    assert not (tmp_path / "etc/vpn/client.conf.t").exists()


def test_openvpn_lines_have_their_own_log_source():
    from podkop_manager import podkop
    p = podkop.parse_log("Wed Nov 11 18:03:37 2026 daemon.notice podkop-vpn[22932]: Initialization Sequence Completed")
    assert p["source"] == "vpn" and p["level"] == "info" and p["msg"] == "Initialization Sequence Completed"


def test_star_limits_every_rule_of_the_tunnel_whatever_its_name(tmp_path):
    cfg = {"outbounds": [{"type": "direct", "tag": "renamed-out", "bind_interface": "pkvpn"},
                         {"type": "direct", "tag": "wg-out", "bind_interface": "wg0"}],
           "route": {"rules": [{"action": "route", "outbound": "renamed-out", "rule_set": ["a"]},
                               {"action": "route", "outbound": "wg-out", "rule_set": ["b"]}]}}
    (tmp_path / "in.json").write_text(json.dumps(cfg))
    p = _sh(tmp_path, '\nmkdir -p "$VPN_DIR"; echo \'{"*":["192.168.1.20/32"],"wg":["10.0.0.1/32"]}\' > "$VPN_DIR/allow"'
                      '\nvpn_filter "$RUN/in.json" "$RUN/out.json"')
    assert p.returncode == 0, p.stderr
    rules = json.loads((tmp_path / "out.json").read_text())["route"]["rules"]
    assert rules[0]["source_ip_cidr"] == ["192.168.1.20/32"] and rules[1]["source_ip_cidr"] == ["10.0.0.1/32"]
    p = _sh(tmp_path, "\ncmd_vpn set", json.dumps({"allow": {"*": ["192.168.1.9"]}}))
    assert p.returncode == 0, p.stderr
    assert json.loads((tmp_path / "etc/vpn/allow").read_text()) == {"*": ["192.168.1.9"]}


RESTARTS = '\nvpn_installed() { :; }\nvpn_init() { echo restart >> "$RUN/calls"; }\nVPN_INIT=true\n'


def test_a_new_password_alone_restarts_the_tunnel(tmp_path):
    first = {"config": "remote a 1\n", "username": "me", "password": "one"}
    assert _sh(tmp_path, RESTARTS + "cmd_vpn set", json.dumps(first)).returncode == 0
    calls = tmp_path / "calls"
    assert calls.read_text() == "restart\n"
    assert _sh(tmp_path, RESTARTS + "cmd_vpn set", json.dumps(first)).returncode == 0       # nothing new: no restart
    assert calls.read_text() == "restart\n"
    assert _sh(tmp_path, RESTARTS + "cmd_vpn set", json.dumps({"username": "me", "password": "two"})).returncode == 0
    assert calls.read_text() == "restart\nrestart\n"
    assert (tmp_path / "etc/vpn/auth").read_text() == "me\ntwo\n"


def test_service_gives_up_after_a_few_fatal_starts():
    assert "procd_set_param respawn 300 10 3" in _vpn_code()


def test_stopped_openvpn_is_started_again_later_but_not_after_a_refused_login(tmp_path):
    base = WATCH + 'vpn_pid() { :; }\nprintf "#!/bin/sh\\necho start >> %s/starts\\n" "$RUN" > "$RUN/init"; chmod +x "$RUN/init"; VPN_INIT="$RUN/init"\n'
    p = _sh(tmp_path, base + "rounds 45")
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "starts").read_text() == "start\nstart\n"        # rounds 20 and 40
    (tmp_path / "starts").unlink()
    p = _sh(tmp_path, base + 'logread() { echo "podkop-vpn[1]: AUTH: Received control message: AUTH_FAILED"; }\nrounds 45')
    assert not (tmp_path / "starts").exists()


WATCH = '''
mkdir -p "$VPN_DIR"; touch "$VPN_DIR/enabled"
vpn_pid() { echo 77; }
vpn_pushed() { printf '77\\n10.9.0.1\\n\\n' > "$RUN/vpn.push"; }
ip() { cat "$RUN/addr" 2>/dev/null; }
ping() { [ -f "$RUN/gw-ok" ]; }
kill() { echo "kill $*" >> "$RUN/events"; }
logger() { :; }
rounds() { _n=$1; while [ "$_n" -gt 0 ]; do vpn_watch; _n=$((_n - 1)); done; }
'''


def test_watch_reports_down_and_up_once_and_restarts_a_silent_tunnel(tmp_path):
    ev = tmp_path / "events"
    addr = "9: pkvpn    inet 10.9.0.2/24 scope global pkvpn"
    # a minute without an address: one vpn_down; the address back: one vpn_up
    p = _sh(tmp_path, WATCH + 'rounds 6; echo "' + addr + '" > "$RUN/addr"; touch "$RUN/gw-ok"; rounds 3')
    assert p.returncode == 0, p.stderr
    assert ev.read_text().split() == ["vpn_down", "vpn_up"]
    # the gateway never answered (no ICMP there): never restarted
    ev.unlink()
    p = _sh(tmp_path, WATCH + 'rm -f "$RUN/gw-ok"; rounds 8')
    assert not ev.exists()
    # it answered and then went silent for 3 rounds: reconnect, once
    p = _sh(tmp_path, WATCH + 'touch "$RUN/gw-ok"; rounds 2; rm "$RUN/gw-ok"; rounds 5')
    assert ev.read_text().splitlines() == ["kill -USR1 77", "vpn_restart"]
    # VPN off: nothing at all
    ev.unlink()
    p = _sh(tmp_path, WATCH + 'rm "$VPN_DIR/enabled" "$RUN/addr"; rounds 8')
    assert not ev.exists()


def test_alerts_have_texts():
    from podkop_manager import notify
    assert "wrong password" in notify.text("home", "vpn_down", {"running": False, "last": "wrong password"})
    assert notify.text("home", "vpn_up", {}) and notify.text("home", "vpn_restart", {}) is None


TESTER = '''
ip() { cat "$RUN/addr" 2>/dev/null; }
uci() { :; }
mkdir -p "$RUN/bin"; printf '#!/bin/sh\\ncat "$4" > "%s/cfg"; cat "%s/fetch.err" >&2 2>/dev/null; exit $(cat "%s/fetch.rc")\\n' "$RUN" "$RUN" "$RUN" > "$RUN/bin/sing-box"
chmod +x "$RUN/bin/sing-box"; PATH="$RUN/bin:$PATH"
vpn_test
'''


def test_site_check_goes_through_the_tunnel_with_the_given_dns(tmp_path):
    run = lambda req: _sh(tmp_path, TESTER, json.dumps(req))
    assert json.loads(run({"host": "git.corp.example"}).stdout) == {"ok": False, "reason": "down", "error": "the VPN is not connected"}
    (tmp_path / "addr").write_text("9: pkvpn    inet 10.9.0.2/24\n")
    (tmp_path / "fetch.rc").write_text("0")
    assert json.loads(run({"host": "git.corp.example", "dns": "10.9.0.1"}).stdout)["ok"] is True
    cfg = json.loads((tmp_path / "cfg").read_text())
    assert cfg["outbounds"] == [{"type": "direct", "tag": "vpn", "bind_interface": "pkvpn", "domain_resolver": "d"}]
    assert cfg["dns"]["servers"] == [{"type": "udp", "tag": "d", "server": "10.9.0.1", "detour": "vpn"}]
    run({"host": "git.corp.example"})        # no DNS server of its own: podkop's bootstrap server, not through the tunnel
    assert json.loads((tmp_path / "cfg").read_text())["dns"]["servers"] == [{"type": "udp", "tag": "d", "server": "77.88.8.8"}]
    (tmp_path / "fetch.rc").write_text("1")
    (tmp_path / "fetch.err").write_text('FATAL[0000] Get "https://x/": lookup git.corp.example: empty result\n')
    assert json.loads(run({"host": "git.corp.example"}).stdout)["reason"] == "dns"
    (tmp_path / "fetch.err").write_text('FATAL[0000] Get "https://x/": tls: failed to verify certificate: x509: certificate signed by unknown authority\n')
    assert json.loads(run({"host": "git.corp.example"}).stdout)["ok"] is True          # a company's own CA: reachable
    (tmp_path / "fetch.err").write_text('FATAL[0000] Get "https://x/": dial tcp 10.1.1.1:443: connect: connection refused\n')
    assert json.loads(run({"host": "git.corp.example"}).stdout)["reason"] == "connect"
    assert run({"host": "a;b"}).returncode == 1 and run({"host": "a.b", "dns": "1.1.1.1;x"}).returncode == 1


def _agent_code(start, end):
    return subprocess.run(["sed", "-n", f"/^{start}/,/^{end}/p", str(AGENT)],
                          capture_output=True, text=True, check=True).stdout


def test_settings_are_read_again_only_after_their_file_changed(tmp_path):
    """The daemon and "status" look every 15-30 s; reading podkop's settings
    through ubus each time was a tenth of a second of the router's CPU."""
    code = _agent_code("# seen_json, seen_fp", "# config_fp:")
    cfg = tmp_path / "podkop"
    cfg.write_text("x")
    script = f'''
RUN="{tmp_path}/run"; CFG="{cfg}"
config_json() {{ echo read >> "{tmp_path}/reads"; cat "{tmp_path}/now.json"; }}
fingerprint() {{ md5sum | cut -c 1-32; }}
{code}
echo '{{"a":1}}' > "{tmp_path}/now.json"
s() {{ seen_json && echo; }}
sleep 1.1; s; s; a=$(seen_fp)
echo '{{"a":2}}' > "{tmp_path}/now.json"; s          # the file did not change: the copy in RAM
sleep 1.1; touch "$CFG"; sleep 1.1; s; b=$(seen_fp)
[ "$a" != "$b" ] && echo differ
rm "{tmp_path}/now.json"; touch "$CFG"; s || echo unreadable
'''
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.stdout.split() == ['{"a":1}', '{"a":1}', '{"a":1}', '{"a":2}', "differ", "unreadable"], out.stderr
    assert (tmp_path / "reads").read_text().count("read") == 3


def test_podkop_enabled_looks_at_the_start_links_without_running_a_script(tmp_path):
    code = _agent_code("# .what ..etc.init.d.podkop enabled", "# the sing-box procd runs")
    assert "podkop_enabled()" in code
    rc = tmp_path / "rc.d"
    rc.mkdir()
    code = code.replace("/etc/rc.d", str(rc))
    run = lambda: subprocess.run(["sh", "-c", code + "\npodkop_enabled && echo yes || echo no"],  # noqa: E731
                                 capture_output=True, text=True).stdout.strip()
    (rc / "S99podkop-agent").write_text("")
    (rc / "S95podkop-engine").write_text("")
    assert run() == "no"
    (rc / "S99podkop").write_text("")
    assert run() == "yes"


def test_dns_guard_writes_three_firewall_sections_once_and_removes_them(tmp_path):
    code = _agent_code("# --- the router.s DNS for every device", "# --- OpenVPN client")
    script = f'''
F="{tmp_path}/fw"; mkdir -p "$F"
uci() {{
  [ "$1" = -q ] && shift
  case "$1" in
    get) [ -f "$F/${{2#firewall.}}" ] && echo x ;;
    set) k=${{2%%=*}}; k=${{k#firewall.}}; echo "${{2#*=}}" >> "$F/${{k%%.*}}" ;;
    add_list) k=${{2%%=*}}; k=${{k#firewall.}}; echo "${{2#*=}}" >> "$F/${{k%%.*}}" ;;
    delete) rm -f "$F/${{2#firewall.}}" ;;
    commit) echo commit >> "{tmp_path}/commits" ;;
  esac
}}
event() {{ echo "$1 $2" >> "{tmp_path}/events"; }}
reply() {{ printf '%s\\n' "$1"; }}
fail() {{ echo "$*" >&2; exit 1; }}
{code}
'''
    code_run = lambda c: subprocess.run(["sh", "-c", script.replace("/etc/init.d/firewall reload", "true") + c],  # noqa: E731
                                        capture_output=True, text=True, timeout=30)
    assert json.loads(code_run("dns_json").stdout) == {"guard": False}
    assert json.loads(code_run("cmd_dns_guard on").stdout) == {"guard": True}
    assert sorted(p.name for p in (tmp_path / "fw").iterdir()) == ["podkop_agent_dns_doh", "podkop_agent_dns_dot", "podkop_agent_dns_redirect"]
    redirect = (tmp_path / "fw/podkop_agent_dns_redirect").read_text().split("\n")
    assert redirect[0] == "redirect" and "53" in redirect and "DNAT" in redirect and "lan" in redirect
    doh = (tmp_path / "fw/podkop_agent_dns_doh").read_text().split("\n")
    assert "REJECT" in doh and "443" in doh and "1.1.1.1" in doh and "8.8.8.8" in doh
    assert "853" in (tmp_path / "fw/podkop_agent_dns_dot").read_text().split("\n")
    code_run("cmd_dns_guard on")                                  # already on: nothing is written (flash)
    assert (tmp_path / "commits").read_text().count("commit") == 1
    assert json.loads(code_run("cmd_dns_guard off").stdout) == {"guard": False}
    assert not list((tmp_path / "fw").iterdir()) and (tmp_path / "commits").read_text().count("commit") == 2
    code_run("cmd_dns_guard off")
    assert (tmp_path / "commits").read_text().count("commit") == 2
    assert (tmp_path / "events").read_text().splitlines() == ['dns_guard {"on":true}', 'dns_guard {"on":false}']
    assert code_run("cmd_dns_guard maybe").returncode == 1
