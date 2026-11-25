"""1.60.0: fixes from the review (manager 0.47.0)."""
import asyncio
import datetime as dt
import time

from podkop_manager.agent import AgentError
from podkop_manager.db import DB
from test_web import H, client


def test_prune_keeps_the_newest_full_probe_result_however_many_heal_runs_came_after(tmp_path):
    db = DB(tmp_path / "m.db")
    rid = db.add_router("home", "h", 22, "k")
    full = db.add_probe_run(rid, "schedule")
    db.finish_probe_run(full, "done", {"rows": 1})
    for _ in range(5):
        run = db.add_probe_run(rid, "heal")
        db.finish_probe_run(run, "done", {"rows": 2})
    db.prune(probe_results=3)
    kept = {r["id"]: r["result"] for r in db.q("SELECT id, result FROM probe_runs")}
    assert kept[full] is not None and sum(1 for v in kept.values() if v) == 3 + 1


def test_a_keys_new_limit_is_looked_at_afresh_and_its_address_stays_masked(tmp_path):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        db = c.db
        db.set_probe_settings(1, keys=["https://sub.example/s/secret-token"])
        db.sync_sub_keys(1, ["https://sub.example/s/secret-token"])
        kid = db.keys(1)[0]["id"]
        db.update_key(kid, acted_until=time.time() + 3600, action="avoid", limit_gb=10)
        r = c.put(f"/api/keys/{kid}", headers=H, json={"limit_gb": 50})
        assert r.status_code == 200, r.text
        assert "secret-token" not in r.text and db.key(kid)["acted_until"] is None
        assert c.put("/api/routers/1/probe", headers=H, json={"options": 5}).status_code == 400          # was a 500


def test_a_busy_router_gets_its_nightly_probe_within_the_hour(tmp_path):
    from test_schedule import _hub
    db, hub, rt, rid = _hub(tmp_path)
    db.set_probe_settings(rid, schedule="03:00")
    tries = []

    async def start_probe(r, trigger="manual"):
        tries.append(trigger)
        if len(tries) == 1:
            raise AgentError("busy", "a probe is already running")
        return 1
    hub.start_probe = start_probe
    now = dt.datetime(2026, 11, 26, 3, 5)
    asyncio.run(hub._maybe_nightly(rid, now))
    assert db.probe_settings(rid).get("last_run_day") != "2026-11-26"          # tried again in 30 s
    asyncio.run(hub._maybe_nightly(rid, now + dt.timedelta(seconds=30)))
    assert tries == ["schedule", "schedule"] and db.probe_settings(rid)["last_run_day"] == "2026-11-26"
