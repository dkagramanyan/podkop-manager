"""Compatibility across independent Manager/router updates and installer rollback."""
import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from podkop_manager.agent import AgentError, connection_support, upgrade_command
from podkop_manager.db import DB
from podkop_manager.hub import Hub, RouterRuntime

ROOT = Path(__file__).resolve().parents[1]
CAPS = ["diagnostics", "eligibility"]


@pytest.mark.parametrize("info,expected", [
    ({"version": "0.7.4"}, False), ({"version": "0.8.0"}, True),
    ({"version": "0.10.0"}, True), ({}, None),
    ({"version": "0.8.1", "capabilities": []}, False),
    ({"version": "0.8.1", "capabilities": ["diagnostics"]}, False),
    ({"version": "0.8.1", "capabilities": CAPS}, True),
])
def test_agent_compatibility(info, expected):
    assert connection_support(info)["supported"] is expected


def test_upgrade_command_has_no_pairing_or_firewall_options():
    cmd = upgrade_command("v1.31.1")
    assert "/v1.31.1/install.sh" in cmd and "--upgrade --release v1.31.1" in cmd
    assert "--key" not in cmd and "clash" not in cmd
    assert "/latest/download/install.sh" in upgrade_command("latest")
    assert "--release latest" not in upgrade_command("latest")


@pytest.mark.parametrize("missing", [("unsupported", "reinstall agent for diagnostics"),
                                    ("bad_request", "unknown command diagnostics")])
def test_old_agent_can_be_upgraded_without_restarting_manager(tmp_path, missing):
    db = DB(tmp_path / "manager.db")
    rid = db.add_router("test", "router", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", ROOT)
    rt = RouterRuntime(db.router(rid), tmp_path / "key")
    hub.routers[rid] = rt
    calls = []

    class Agent:
        info = {"version": "0.7.4"}
        diagnostic_error = None

        async def call(self, name, **kwargs):
            calls.append(name)
            if name == "version":
                return dict(self.info)
            if name == "config-get":
                return {"config": {"sections": []}, "fingerprint": "fp"}
            if name == "proxies":
                return {"groups": []}
            if name == "diagnostics":
                if self.diagnostic_error:
                    raise AgentError(*self.diagnostic_error)
                return {"fingerprint": "fp"}
            raise AssertionError(name)

    rt.agent = Agent()
    # A stale cached status must not make an unsupported call possible.
    rt.status = {"agent": {"version": "0.8.0"}}
    for _ in range(2):
        with pytest.raises(AgentError, match="Update the router agent") as err:
            asyncio.run(hub.check_connections(rid))
        assert err.value.code == "agent_upgrade"
    assert calls == ["version", "version"]
    assert db.connection_history(rid)["checks"] == []
    rt.agent.info = {"version": "0.8.0"}
    rt.agent.diagnostic_error = missing
    with pytest.raises(AgentError, match="Update the router agent") as err:
        asyncio.run(hub.check_connections(rid))
    assert err.value.code == "agent_upgrade"
    assert connection_support(rt.status["agent"])["supported"] is False
    rt.agent.info = {"version": "0.8.1", "capabilities": CAPS}
    rt.agent.diagnostic_error = None
    asyncio.run(hub.check_connections(rid))
    assert len(db.connection_history(rid)["checks"]) == 1
    assert connection_support(rt.status["agent"])["supported"] is True
    # An engine limitation needs its own error, not an agent-upgrade prompt.
    rt.agent.diagnostic_error = ("unsupported", "engine cannot run these diagnostics")
    with pytest.raises(AgentError, match="engine cannot run these diagnostics") as err:
        asyncio.run(hub.check_connections(rid))
    assert err.value.code == "unsupported"
    assert connection_support(rt.status["agent"])["supported"] is True


def test_old_agent_settings_and_upgrade_api(tmp_path, monkeypatch):
    from test_web import client, H, FakeAgent, FakeHub, FakeRouter
    calls = []

    async def call(self, name, **kwargs):
        calls.append(name)
        if name == "version":
            return {"version": "0.7.4"}
        raise AssertionError(name)

    monkeypatch.setattr(FakeAgent, "call", call)
    monkeypatch.setattr(FakeRouter, "status", {"agent": {"version": "0.7.4"}})
    monkeypatch.setattr(FakeHub, "check_connections", Hub.check_connections, raising=False)
    monkeypatch.setattr(FakeHub, "probes", {}, raising=False)
    monkeypatch.setattr(FakeRouter, "connection_lock", asyncio.Lock(), raising=False)
    with client(tmp_path, with_router=True) as c:
        assert c.get("/api/routers/1/agent-upgrade").status_code == 401
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        health = c.get("/api/routers/1/reliability").json()
        assert health["agent"]["supported"] is False
        assert "0.7.4" in health["agent"]["message"]
        for method, url, body in [("put", "/api/routers/1/reliability", {"enabled": True}),
                                  ("post", "/api/routers/1/reliability/check", {})]:
            resp = getattr(c, method)(url, headers=H, json=body)
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "agent_upgrade"
        assert not c.db.get_setting("reliability:1", {}).get("enabled")
        # Turning it off must work even without diagnostics or eligibility.
        assert c.put("/api/routers/1/reliability", headers=H, json={"enabled": False}).status_code == 200
        assert calls == ["version", "version"]
        assert "--upgrade" in c.get("/api/routers/1/agent-upgrade").json()["command"]


@pytest.fixture
def router_files(tmp_path):
    """Run the real shell scripts with only their system paths relocated.

    No writes escape the temporary router root; only init and sing-box are
    fakes. jq, flock, version/capability detection and file rollback are real.
    """
    root, src, bins = (tmp_path / name for name in ("root", "release", "bin"))
    src.mkdir(); bins.mkdir()

    def relocate(text):
        return re.sub(r"/(?:etc|usr|tmp)/", lambda m: str(root) + m[0], text)

    def write(path, text, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if executable:
            path.chmod(0o755)
        return path

    for path in ("etc/config/podkop", "etc/config/firewall", "etc/dropbear/authorized_keys",
                 "etc/podkop-agent/paused", "etc/podkop-agent/pins", "etc/podkop-probe.conf"):
        write(root / path, f"keep {path}\n")
    write(root / "etc/sysupgrade.conf", "# user entries\n")
    write(root / "usr/bin/podkop", "#!/bin/sh\nexit 0\n", True)
    write(root / "usr/lib/podkop/sing_box_config_facade.sh", "")
    write(root / "usr/bin/podkop-agent", "#!/bin/sh\n# previous agent\nexit 0\n", True)
    write(root / "usr/bin/podkop-probe", "#!/bin/sh\n# previous probe\n", True)
    write(root / "usr/share/podkop-agent/install.sh", "# previous installer\n")
    (root / "tmp").mkdir()
    init = '#!/bin/sh\necho "LABEL:$1" >> "$TEST_INIT_LOG"\n[ "$1" != running ] || exit "$TEST_STOPPED"\n'
    write(root / "etc/init.d/podkop-agent", init.replace("LABEL", "old") + "exit 0\n", True)
    for name in ("podkop-agent", "probe.sh", "reliability.sh"):
        write(src / name, relocate((ROOT / "router" / name).read_text()))
    write(src / "podkop-agent.init", init.replace("LABEL", "new") +
          '# podkop-agent daemon\n[ "$1" != restart ] || exit "$TEST_RESTART_FAIL"\nexit 0\n')
    script = write(tmp_path / "install.sh", relocate((ROOT / "router/install.sh").read_text()))
    write(bins / "sing-box", "#!/bin/sh\necho tools.decode-link\n", True)
    env = dict(os.environ, PATH=f"{bins}:{os.environ['PATH']}", TEST_INIT_LOG=str(tmp_path / "init.log"),
               TEST_STOPPED="0", TEST_RESTART_FAIL="0")
    env.pop("SSH_ORIGINAL_COMMAND", None)
    env.pop("SSH_CONNECTION", None)

    def run(*args):
        return subprocess.run(["sh", str(script), "--upgrade", "--src", str(src), *args],
                              env=env, text=True, capture_output=True, timeout=15)

    return root, src, env, run


@pytest.mark.parametrize("stopped", [False, True])
def test_upgrade_preserves_pairing_settings_and_service_state(router_files, stopped):
    root, src, env, run = router_files
    env["TEST_STOPPED"] = "1" if stopped else "0"
    before = {p: p.read_bytes() for p in (root / "etc").rglob("*")
              if p.is_file() and "init.d" not in p.parts and p.name != "sysupgrade.conf"}
    result = run()
    assert result.returncode == 0, result.stderr
    assert "upgraded" in result.stdout
    assert all(p.read_bytes() == data for p, data in before.items())
    assert (root / "usr/share/podkop-agent/reliability.sh").read_bytes() == (src / "reliability.sh").read_bytes()
    version = subprocess.check_output([str(root / "usr/bin/podkop-agent"), "version"], env=env, text=True)
    assert json.loads(version)["data"]["capabilities"] == CAPS
    log = Path(env["TEST_INIT_LOG"]).read_text()
    assert ("new:restart" in log) is not stopped
    assert "enable" not in log and "stop\n" not in log


@pytest.mark.parametrize("failure", ["download", "restart", "helper", "busy", "mixed_options"])
def test_failed_upgrade_preserves_previous_install(router_files, failure):
    root, src, env, run = router_files
    before = {p: p.read_bytes() for parent in (root / "etc", root / "usr")
              for p in parent.rglob("*") if p.is_file()}
    args = ()
    lock = None
    if failure == "download":
        (src / "reliability.sh").write_text("# truncated\n")
    elif failure == "helper":
        (src / "reliability.sh").write_text("# end of podkop reliability\n")
    elif failure == "restart":
        env["TEST_RESTART_FAIL"] = "1"
    elif failure == "mixed_options":
        args = ("--keep-clash-open",)
    else:
        import fcntl
        (root / "tmp/podkop-agent").mkdir()
        lock = (root / "tmp/podkop-agent/apply.lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = run(*args)
    finally:
        if lock:
            lock.close()
    assert result.returncode != 0
    reason = {"download": "could not get reliability.sh", "restart": "could not restart the updated agent",
              "helper": "missing connection health commands", "busy": "an apply or recovery is running",
              "mixed_options": "cannot be combined"}[failure]
    assert reason in result.stderr
    assert all(p.read_bytes() == data for p, data in before.items()), result.stderr
    assert not (root / "usr/share/podkop-agent/reliability.sh").exists()
    if failure in ("restart", "helper"):
        assert "restoring the previous agent" in result.stderr
        assert "old:restart" in Path(env["TEST_INIT_LOG"]).read_text()
