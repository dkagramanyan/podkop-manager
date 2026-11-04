"""Health summaries must count the complete window without treating log volume as an outage."""
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from podkop_manager.db import DB
from podkop_manager.agent import AgentError
from test_web import H, FakeHub, client


def test_observation_summary_counts_full_window_and_keeps_sources_separate(tmp_path):
    db = DB(tmp_path / "health.db")
    for i in range(350):
        db.observe(1, "router", {"kind": "transport", "scope": "main-1-out", "message": f"timeout {i}"}, {}, now=1000 + i)
    db.observe(1, "active check", {"kind": "transport", "scope": "main-1-out", "message": "paired failure"}, {}, now=1400)
    db.observe(1, "kuma", {"kind": "recovery", "scope": "website", "message": "back up"}, {}, now=1401)
    db.observe(1, "router", {"kind": "dns", "scope": "old"}, {}, now=999)
    db.observe(1, "router", {"kind": "dns", "scope": "future"}, {}, now=2001)
    db.observe(2, "router", {"kind": "transport", "scope": "other router"}, {}, now=1400)
    summary = db.observation_summary(1, 1000, 2000)
    assert summary["total"] == 352 and summary["failures"] == 351
    assert summary["group_count"] == 3
    group = summary["groups"][0]
    assert (group["count"], group["first"], group["last"]) == (350, 1000, 1349)
    assert group["sample"]["message"] == "timeout 349"
    assert group["source"] == "router" and group["kind"] == "transport"
    assert len(db.connection_history(1, 1000)["observations"]) == 300
    assert db.observation_summary(1, 3000, 4000)["groups"] == []


def test_summary_caps_groups_without_truncating_totals(tmp_path):
    db = DB(tmp_path / "health.db")
    for i in range(110):
        db.observe(1, "router", {"kind": "access", "scope": str(i)}, {}, now=1000 + i)
    summary = db.observation_summary(1, 1000, 2000)
    assert summary["group_count"] == summary["total"] == summary["failures"] == 110
    assert len(summary["groups"]) == 100
    assert summary["groups"][0]["scope"] == "109"


def test_health_api_windows_and_authentication(tmp_path):
    with client(tmp_path, with_router=True) as c:
        assert c.get("/api/routers/1/reliability").status_code == 401
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        now = time.time()
        for hours in (.5, 3, 12, 48, 169):
            c.db.observe(1, "router", {"kind": "dns", "scope": "dns"}, {}, now=now - hours * 3600)
        for hours, count in ((1, 1), (6, 2), (24, 3), (168, 4)):
            data = c.get(f"/api/routers/1/reliability?hours={hours}").json()
            assert data["summary"]["total"] == len(data["observations"]) == count
            assert data["summary"]["until"] - data["summary"]["since"] == hours * 3600
        assert c.get("/api/routers/1/reliability?hours=0").status_code == 400
        assert c.get("/api/routers/1/reliability?hours=999999999").status_code == 400
        assert c.get("/api/routers/99/reliability").status_code == 404


def test_manual_collector_error_is_visible_without_overwriting_busy_check(tmp_path, monkeypatch):
    failure = AgentError("diagnostics_failed", "Not enough available memory")

    async def fail(self, rid):
        raise failure

    monkeypatch.setattr(FakeHub, "check_connections", fail, raising=False)
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        assert c.post("/api/routers/1/reliability/check", headers=H).status_code >= 400
        latest = c.get("/api/routers/1/reliability").json()["latest"]
        assert latest["error"] == "Not enough available memory"
        assert latest["code"] == "diagnostics_failed"
        failure = AgentError("busy", "already checking")
        assert c.post("/api/routers/1/reliability/check", headers=H).status_code >= 400
        assert c.get("/api/routers/1/reliability").json()["latest"] == latest


def test_health_verdict_distinguishes_stale_failed_and_partial_checks():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed to check browser health verdicts")
    source = (Path(__file__).resolve().parents[1] / "podkop_manager/static/app.js").read_text()
    helpers = source[source.index("function healthPath("):source.index("async function viewReliability(")]
    script = helpers + r'''
const assert = require('node:assert/strict');
const paths = () => ['https://a.test', 'https://b.test'].map(target => ({target, exit: 0, code: 204, seconds: .1}));
const make = () => ({settings: {interval:60}, checks:[{t:1000, report:{direct:paths(), router_dns:true,
  primary_dns:{ok:true}, provider_dns:true, nodes:{a:paths()}, selected:{main:'a'}}}], latest:{}, agent:{supported:true}});
const verdict = (d, rt = {}) => healthVerdict(d, rt, 1100);
let d = make();
assert.equal(verdict(d).tone, 'ok');
assert.equal(verdict(d, {online:false}).title, 'Router unreachable');
d.summary = {failures:100000}; assert.equal(verdict(d).tone, 'ok'); // log volume is not a current check
assert.equal(healthVerdict(d, {}, 1300).title, 'Results are out of date');
d.latest = {error:'collector failed'}; assert.equal(verdict(d).title, 'Last check could not finish');
d = make(); d.checks = []; assert.equal(verdict(d).title, 'No baseline yet');
d = make(); d.agent.supported = false; assert.equal(verdict(d).title, 'Diagnostics unavailable');
d = make(); d.checks[0].report.direct.forEach(r => {r.exit = 28; r.code = 0;});
assert.equal(verdict(d).title, 'Direct internet is failing');
d = make(); d.checks[0].report.router_dns = false; assert.equal(verdict(d).title, 'DNS needs attention');
d = make(); d.checks[0].report.nodes.a.forEach(r => r.code = 403);
assert.equal(healthPath(d.checks[0].report.nodes.a), 'degraded'); assert.equal(verdict(d).tone, 'warn');
d = make(); d.checks[0].report.nodes = {}; assert.equal(verdict(d).title, 'Checks are incomplete');
d = make(); d.checks[0].report.selected.extra = 'untested'; assert.equal(verdict(d).title, 'Checks are incomplete');
d = make(); d.checks[0].report.payload = {'diagnostic-direct':paths().map(r => ({...r, bytes:1024}))};
assert.equal(verdict(d).title, 'Direct downloads need attention');
d = make(); d.checks[0].report.payload = {'a':paths().map(r => ({...r, code:403, bytes:65536}))};
assert.equal(verdict(d).tone, 'warn');
assert.equal(healthPath(paths().slice(0, 1)), 'unknown');
'''
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
