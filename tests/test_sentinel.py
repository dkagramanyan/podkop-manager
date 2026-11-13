"""podkop-sentinel (the script inside router/install.sh), protected rules and
safe mode (router/podkop-agent)."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Box:
    """The sentinel with its system paths under a folder and stand-ins for
    the programs it calls; rounds are run one at a time ("once")."""

    def __init__(self, tmp: Path):
        self.root, self.bin = tmp / "root", tmp / "bin"
        text = subprocess.run(["sh", str(ROOT / "router/install.sh"), "--print-sentinel"],
                              capture_output=True, text=True, check=True).stdout
        assert text.rstrip().endswith("# end of podkop-sentinel")
        text = re.sub(r"/(?:etc|usr|tmp)/", lambda m: str(self.root) + m[0], text)
        for d in ("etc/podkop-agent", "etc/rc.d", "etc/init.d", "usr/bin", "tmp/podkop-agent", "tmp/sing-box"):
            (self.root / d).mkdir(parents=True)
        self.bin.mkdir()
        self.script = self.root / "usr/bin/podkop-sentinel"
        self.script.write_text(text)
        self.log = tmp / "calls"
        self.log.write_text("")
        for name in ("podkop", "podkop-agent"):
            self._exe(self.root / "etc/init.d" / name, f'echo "{name} $1" >> "{self.log}"\n')
            (self.root / "etc/rc.d" / f"S99{name}").write_text("")
        self._exe(self.root / "usr/bin/podkop-agent",
                  f'[ "$1" = _event ] && echo "event $2 $3" >> "{self.log}"\n# writes $RUN/beat\n')
        cfg = self.root / "tmp/sing-box/config.json"
        cfg.write_text(json.dumps({"experimental": {"clash_api": {"external_controller": "127.0.0.1:9090"}}}))
        self._exe(self.bin / "uci", f'echo "{cfg}"\n')
        self._exe(self.bin / "logger", "exit 0\n")
        # a rule answers while its name is in the file "up"; the line while "wan" is there
        self._exe(self.bin / "curl", f'for a; do u=$a; done; s=${{u##*/proxies/}}; s=${{s%%-out/delay*}}\n'
                                     f'grep -qx "$s" "{tmp}/up" 2>/dev/null && echo \'{{"delay":120}}\' || echo \'{{"message":"timeout"}}\'\n')
        self._exe(self.bin / "ping", f'[ -f "{tmp}/wan" ]\n')
        self._exe(self.bin / "ip", "echo 'default via 10.0.0.1 dev wan'\n")
        self.tmp = tmp
        (tmp / "wan").write_text("")
        self.up("main")
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", PODKOP_SENTINEL_BOOT="0",
                        PODKOP_SENTINEL_SETTLE="0", PODKOP_SENTINEL_TRIAL="0", PODKOP_SENTINEL_RETRY="0")

    @staticmethod
    def _exe(path, body):
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def up(self, *rules):
        (self.tmp / "up").write_text("".join(r + "\n" for r in rules))

    def protect(self, *rules):
        (self.root / "etc/podkop-agent/protected").write_text("".join(r + "\n" for r in rules))

    def beat(self, age=0):
        f = self.root / "tmp/podkop-agent/beat"
        f.write_text("")
        os.utime(f, (time.time() - age,) * 2)

    def run(self, *args, **env):
        p = subprocess.run(["sh", str(self.script), *(args or ("once",))], env=dict(self.env, **env),
                           capture_output=True, text=True, timeout=30)
        assert p.returncode == 0, p.stderr
        return p.stdout

    def calls(self):
        return self.log.read_text().splitlines()

    @property
    def mark(self):
        return self.root / "etc/podkop-agent/safe-mode"


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def test_a_daemon_that_stands_still_is_restarted_at_most_three_times_an_hour(box):
    box.beat()
    box.run()
    assert box.calls() == []                                  # alive: nothing to do
    box.beat(age=700)
    for _ in range(5):
        box.run(PODKOP_SENTINEL_START="0")
    assert box.calls().count("podkop-agent restart") == 3
    assert not box.mark.exists()                              # a still daemon is no reason for safe mode


def test_nothing_is_judged_while_podkop_is_off_or_an_apply_runs(box):
    box.protect("main")
    box.up()
    (box.root / "etc/podkop-agent/paused").write_text("")
    for _ in range(7):
        box.run()
    assert box.calls() == [] and not box.mark.exists()


def test_nothing_is_judged_before_ntp_has_set_the_clock(box, tmp_path):
    box.beat()
    box.protect("main")
    box.up()
    mark = tmp_path / "clock"
    for _ in range(7):                                        # after a power cut: TLS fails until the clock is right
        box.run(PODKOP_CLOCK_MARK=str(mark), PODKOP_SENTINEL_CLOCK="999999999")
    assert box.calls() == [] and not box.mark.exists()
    mark.write_text("")
    for _ in range(5):
        box.run(PODKOP_CLOCK_MARK=str(mark), PODKOP_SENTINEL_CLOCK="999999999")
    assert box.mark.exists()


def test_safe_mode_when_a_protected_rule_fails_and_plain_podkop_helps(box):
    box.beat()
    box.protect("main")
    box.up()                                                  # main stopped answering
    for _ in range(4):
        box.run()
    assert not box.mark.exists()                              # not before 5 checks in a row
    (box.root / "tmp/podkop-agent/heal").mkdir()
    (box.root / "tmp/podkop-agent/heal/state.json").write_text("{}")
    box.run()
    assert box.mark.exists() and "main did not answer" in box.mark.read_text()
    calls = box.calls()
    assert calls[:3] == ["podkop-agent stop", "podkop restart", "podkop-agent start"] and calls[3].startswith("event safe_mode ")
    assert json.loads(calls[3].split(" ", 2)[2])["trial"] is True
    assert not (box.root / "tmp/podkop-agent/heal/state.json").exists()       # the agent's additions are gone
    box.up("main")                                            # with plain podkop it works
    box.run()
    box.run()
    assert box.mark.exists() and box.calls()[-1].startswith("event safe_mode_confirmed")
    box.up()                                                  # later failures change nothing: it stays until switched off
    box.run()
    assert box.mark.exists()
    box.run("leave", "test")
    assert not box.mark.exists() and "podkop-agent restart" in box.calls()


def test_safe_mode_is_left_when_plain_podkop_does_not_help(box):
    box.beat()
    box.protect("main")
    box.up()
    for _ in range(5):
        box.run()
    assert box.mark.exists()
    box.run()                                                 # still failing after the trial time
    assert not box.mark.exists()
    assert any(c.startswith("event safe_mode_off") for c in box.calls())
    n = len(box.calls())
    for _ in range(8):                                        # and it is not tried again for hours
        box.run()
    assert not box.mark.exists() and "podkop restart" not in box.calls()[n:]


def test_no_safe_mode_without_the_providers_line_or_without_protected_rules(box):
    box.beat()
    box.up()
    for _ in range(7):                                        # nothing protected
        box.run()
    box.protect("main")
    (box.tmp / "wan").unlink()                                # the line is down: not the agent's doing
    for _ in range(7):
        box.run()
    assert not box.mark.exists() and box.calls() == []


def _agent(start, end):
    return subprocess.run(["sed", "-n", f"/^{start}/,/^{end}/p", str(ROOT / "router/podkop-agent")],
                          capture_output=True, text=True, check=True).stdout


BASE = {"outbounds": [
    {"type": "vless", "tag": "main-1-out", "server": "a", "transport": {"type": "grpc"}},
    {"type": "vless", "tag": "main-2-out", "server": "b"},
    {"type": "urltest", "tag": "main-urltest-out", "outbounds": ["main-1-out", "main-2-out"]},
    {"type": "selector", "tag": "main-out", "outbounds": ["main-urltest-out"]},
    {"type": "vless", "tag": "youtube-1-out", "server": "c"},
    {"type": "urltest", "tag": "youtube-urltest-out", "outbounds": ["youtube-1-out"]}],
    "route": {"rules": [{"rule_set": ["main-rs"], "outbound": "main-out"}, {"rule_set": ["yt"], "outbound": "youtube-out"}],
              "final": "direct-out"},
    "dns": {"servers": [{"tag": "dns-server"}]}}


@pytest.mark.parametrize("change,ok", [
    (lambda c: None, True),
    # what the agent adds on purpose
    (lambda c: c["outbounds"][0]["transport"].update(idle_timeout="15s", ping_timeout="15s"), True),
    (lambda c: c["outbounds"][2].update(interrupt_exist_connections=False), True),
    (lambda c: (c["outbounds"].append({"type": "socks", "tag": "main-strict-out"}),
                c["outbounds"][3]["outbounds"].append("main-strict-out")), True),
    (lambda c: c["route"]["rules"][0].update(source_ip_cidr=["10.0.0.2/32"]), True),
    # another rule is free to change
    (lambda c: c["outbounds"][5]["outbounds"].append("main-1-out"), True),
    # a protected rule is not
    (lambda c: c["outbounds"][2]["outbounds"].append("youtube-1-out"), False),
    (lambda c: c["outbounds"][2]["outbounds"].pop(), False),
    (lambda c: c["outbounds"][1].update(server="evil"), False),
    (lambda c: c["outbounds"].pop(1), False),
    (lambda c: c["route"]["rules"][0].update(outbound="youtube-out"), False),
    (lambda c: c["route"]["rules"][0].update(rule_set=[]), False),
    (lambda c: c["route"].update(final="main-out"), False),
    (lambda c: c["dns"]["servers"].append({"tag": "x"}), False),
])
def test_a_config_that_changes_a_protected_rule_is_not_written(tmp_path, change, ok):
    new = json.loads(json.dumps(BASE))
    change(new)
    (tmp_path / "a.json").write_text(json.dumps(BASE))
    (tmp_path / "b.json").write_text(json.dumps(new))
    (tmp_path / "protected").write_text("main\n")
    script = (f'ETC="{tmp_path}"; TAB="$(printf "\\t")"\nevent() {{ echo "$1" >> "{tmp_path}/events"; }}\nlogger() {{ :; }}\n'
              + _agent("# --- safe mode and protected rules", "# safe-mode .on.off.")
              + f'\nprotect_ok "{tmp_path}/a.json" "{tmp_path}/b.json" && echo ok || echo refused\n')
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == ("ok" if ok else "refused"), out.stderr
    assert (tmp_path / "events").exists() is not ok
    (tmp_path / "protected").unlink()                         # nothing protected: anything goes
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "ok"
