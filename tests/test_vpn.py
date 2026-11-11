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
        assert c.get("/api/routers/1/vpn/job/1;reboot").status_code == 400
        assert c.get("/api/routers/1/vpn/job/20261111-42").status_code == 200
        assert agent.calls[-1] == (("job", "20261111-42"), None)
