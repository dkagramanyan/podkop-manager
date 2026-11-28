"""1.63.0 (agent 0.33.0): a reload in place that changes nothing is not made
(each one cuts every connection), and pinned servers are selected again after one."""
import json
import subprocess
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "router/podkop-agent"
CFG = {"outbounds": [{"tag": "main-1-out"}, {"tag": "main-out"}, {"tag": "direct-out"}]}


def _code():
    return subprocess.run(["sed", "-n", "/^hot_apply() {/,/^}/p", str(AGENT)], capture_output=True, text=True, check=True).stdout


STUBS = r'''
D="$1"; RUN="$D/run"; ETC="$D/etc"; VPN_DIR="$D/vpn"; mkdir -p "$RUN" "$ETC"; HEALTH_JQ=.
singbox_pid() { echo 42; }
podkop_start_state() { echo 0; }
uci() { echo "$D/config.json"; }
podkop_build() { cp "$D/build.json" "$1"; }
protect_ok() { :; }
kill() { echo hup >> "$D/calls"; }
sleep() { :; }
proxies() { echo '{"proxies": {"main-1-out": {}, "main-out": {}}}'; }
reapply_pins() { echo "pins $1" >> "$D/calls"; }
dns_ok() { :; }
logger() { :; }
'''


def _run(tmp_path, args):
    p = subprocess.run(["sh", "-c", STUBS + _code() + f"\nhot_apply {args}", "sh", str(tmp_path)],
                       capture_output=True, text=True, timeout=30)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    (tmp_path / "calls").unlink(missing_ok=True)
    return p, calls


def test_same_settings_no_reload_unless_an_apply_forces_it(tmp_path):
    (tmp_path / "build.json").write_text(json.dumps(CFG))
    # what sing-box runs: an earlier reload's output (jq's layout)
    (tmp_path / "config.json").write_text(subprocess.check_output(["jq", ".", str(tmp_path / "build.json")], text=True))
    p, calls = _run(tmp_path, "'eligibility lease expired'")
    assert p.returncode == 0, p.stdout + p.stderr
    assert calls == []
    # an apply: its rule-set files changed under the same config
    p, calls = _run(tmp_path, "'apply 1' force")
    assert p.returncode == 0 and calls == ["hup", "pins quiet"]


def test_a_reload_selects_the_pinned_servers_again(tmp_path):
    (tmp_path / "build.json").write_text(json.dumps(CFG))
    (tmp_path / "config.json").write_text(json.dumps({"outbounds": [{"tag": "main-out"}]}))
    p, calls = _run(tmp_path, "'protected rules changed'")
    assert p.returncode == 0, p.stdout + p.stderr
    assert calls == ["hup", "pins quiet"]
    assert json.loads((tmp_path / "config.json").read_text()) == CFG


def test_a_reload_waits_until_sing_box_shows_every_new_server(tmp_path):
    # 0.34: one jq for all outbounds; one missing keeps it waiting, then it fails naming it
    cfg = {"outbounds": CFG["outbounds"] + [{"tag": "main-2-out"}, {"tag": "main-urltest-out"}]}
    (tmp_path / "build.json").write_text(json.dumps(cfg))
    (tmp_path / "config.json").write_text("{}")
    p, calls = _run(tmp_path, "'protected rules changed'")
    assert p.returncode == 1 and "did not take the new servers: main-2-out" in p.stdout
    assert not list((tmp_path / "run").glob("hot.*"))          # nothing left behind


def test_lists_are_fetched_with_curl_first(tmp_path):
    # 0.34: on reserv wget failed every HTTPS download after its whole 30 s, curl took 1 s:
    # "Where does it go?" spent its 40 s on one list
    rel = Path(__file__).resolve().parents[1] / "router/reliability.sh"
    code = subprocess.run(["sed", "-n", "/^guard_fetch() {/,/^}/p", str(rel)], capture_output=True, text=True, check=True).stdout
    script = f'''
curl() {{ echo curl >> "{tmp_path}/calls"; shift 3; printf data > "$2"; }}
wget() {{ echo wget >> "{tmp_path}/calls"; return 4; }}
{code}
guard_fetch https://example.com/x.srs "{tmp_path}/out"
'''
    p = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "calls").read_text().split() == ["curl"] and (tmp_path / "out").read_text() == "data"
