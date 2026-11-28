"""Agent 0.32 takes away the zapret of agents 0.29-0.31 once: sing-box first,
then nfqws2, the table and the files."""
import json
import subprocess
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "router/podkop-agent"


def _code():
    return subprocess.run(["sed", "-n", "/^# --- zapret leftovers/,/^# --- watched servers/p", str(AGENT)],
                          capture_output=True, text=True, check=True).stdout


STUBS = '''
RUN="$1/run"; mkdir -p "$RUN"
say() { echo "$*" >> "$1/../calls"; }
singbox_pid() { [ -f "$RUN/../sb" ] && echo 42; }
uci() { echo "$RUN/../config.json"; }
nft() { echo "nft $*" >> "$RUN/../calls"; case "$1" in list) [ -f "$RUN/../table" ] ;; delete) rm -f "$RUN/../table" ;; esac; }
hot_apply() { echo "hot_apply $1" >> "$RUN/../calls"; [ -f "$RUN/../reload-fails" ] && return 1
  echo '{"outbounds": [{"tag": "meta-out"}]}' > "$RUN/../config.json"; }
event() { echo "event $1" >> "$RUN/../calls"; }
logger() { :; }
'''


def _run(tmp_path):
    code = STUBS + (_code().replace("ZAPRET_OLD_DIR=/etc/podkop-zapret", 'ZAPRET_OLD_DIR="$1/podkop-zapret"')
                    .replace("ZAPRET_OLD_INIT=/etc/init.d/podkop-zapret", 'ZAPRET_OLD_INIT="$1/init"')) + "\nzapret_leftovers\n"
    p = subprocess.run(["sh", "-c", code, "sh", str(tmp_path)], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    calls = tmp_path / "calls"
    return calls.read_text().splitlines() if calls.exists() else []


def _old_install(tmp_path):
    (tmp_path / "podkop-zapret").mkdir()
    (tmp_path / "podkop-zapret/rules.json").write_text("{}")
    init = tmp_path / "init"
    init.write_text(f'#!/bin/sh\necho "init $1" >> {tmp_path}/calls\n')
    init.chmod(0o755)
    (tmp_path / "table").write_text("")
    (tmp_path / "sb").write_text("")
    (tmp_path / "config.json").write_text(json.dumps({"outbounds": [{"tag": "meta-zapret-direct"}, {"tag": "meta-zapret-out"}, {"tag": "meta-out"}]}))


def test_sing_box_goes_first_then_nfqws2_the_table_and_the_files(tmp_path):
    _old_install(tmp_path)
    calls = _run(tmp_path)
    order = [c.split()[0] + (" " + c.split()[1] if c.startswith(("nft", "init")) else "") for c in calls]
    assert order == ["hot_apply", "init disable", "init stop", "nft delete", "event"], calls
    assert not (tmp_path / "podkop-zapret").exists() and not (tmp_path / "init").exists() and not (tmp_path / "table").exists()
    assert calls[-1] == "event zapret_removed"
    # done once: nothing left, nothing more to do
    (tmp_path / "calls").unlink()
    assert _run(tmp_path) == ["nft list table inet podkop_zapret"]


def test_while_sing_box_cannot_take_the_change_the_table_stays(tmp_path):
    _old_install(tmp_path)
    (tmp_path / "reload-fails").write_text("")
    calls = _run(tmp_path)
    assert [c for c in calls if c.startswith(("nft delete", "init", "event"))] == []
    assert (tmp_path / "table").exists() and (tmp_path / "podkop-zapret").exists()
    (tmp_path / "reload-fails").unlink()
    _run(tmp_path)
    assert not (tmp_path / "table").exists() and not (tmp_path / "podkop-zapret").exists()


def test_with_podkop_stopped_the_leftovers_go_at_once(tmp_path):
    _old_install(tmp_path)
    (tmp_path / "sb").unlink()
    calls = _run(tmp_path)
    assert not any(c.startswith("hot_apply") for c in calls) and "event zapret_removed" in calls
    assert not (tmp_path / "table").exists()
