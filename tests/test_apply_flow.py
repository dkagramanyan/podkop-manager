"""1.60.0: the draft's life around an apply (manager 0.47.0): kept while the
router applies, done only by the job's end, moved onto settings the router
changed since, and a loaded config's switches tied to the change they came with."""
import asyncio
import copy

from podkop_manager import podkop
from podkop_manager.agent import AgentError
from test_schedule import BASE, Agent, _hub, changed


def _cfg(agent):
    return {"config": copy.deepcopy(agent.config), "fingerprint": agent.fp}


def test_draft_stays_while_the_router_applies_and_goes_when_the_job_ends(tmp_path):
    # 1.59: a status poll saw the new settings mid-apply and dropped the draft (its cleanup and switches were lost)
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["a.com", "b.com"])
    db.set_draft(rid, target, "fp1", base_config=BASE)
    db.set_setting(f"router_pending:{rid}", {"kill_switch": ["main"]})
    fired = []
    hub.after_apply_now = lambda r: fired.append(r.id)

    class Slow(Agent):
        state = "restarting"

        async def call(self, name, *args, **kw):
            if name == "job":
                return {"state": self.state}
            return await super().call(name, *args, **kw)

    rt.agent = Slow(BASE)

    async def go():
        r = await hub.apply(rid, "fp1", target)
        # the router wrote the settings, podkop is still starting: a poll reads them
        rt.agent.config, rt.agent.fp = copy.deepcopy(target), "fp2"
        hub._got_config(rt, _cfg(rt.agent))
        assert db.draft(rid) is not None and rt.draft["applying"] and fired == []
        rt.agent.state = "applied"
        for t in list(hub.jobs):
            await t
        assert db.draft(rid) is None and fired == [rid]
        assert db.version(r["version"])["state"] == "applied" and rt.undone is None
    asyncio.run(go())


def test_a_reverted_apply_keeps_the_draft_and_says_so_until_the_next_one(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["x.com"])
    db.set_draft(rid, target, "fp1", base_config=BASE)

    class Reverts(Agent):
        async def call(self, name, *args, **kw):
            if name == "job":
                return {"state": "reverted", "message": "podkop did not start", "reason": "sing-box: bad link"}
            return await super().call(name, *args, **kw)

    rt.agent = Reverts(BASE)

    async def go():
        r = await hub.apply(rid, "fp1", target)
        for t in list(hub.jobs):
            await t
        assert db.draft(rid)["config"] == target
        assert rt.undone == {"version": r["version"], "state": "reverted", "t": rt.undone["t"], "message": "sing-box: bad link"}
        assert db.get_setting(f"last_undone:{rid}")["version"] == r["version"]
    asyncio.run(go())


def test_a_draft_follows_settings_the_router_changed_since_when_they_do_not_clash(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    mine = changed(BASE, "main", user_domains=["a.com", "b.com"])
    db.set_draft(rid, mine, "fp1", base_config=BASE)
    # the nightly probe gave youtube new servers
    rt.agent.config, rt.agent.fp = changed(BASE, "youtube", urltest_proxy_links=["vless://z@h:1#Z"]), "fp2"
    hub._got_config(rt, _cfg(rt.agent))
    d = db.draft(rid)
    assert d["base"] == "fp2" and not rt.draft["stale"] and rt.draft["changes"] == 1
    by = podkop.sections_by_name(d["config"])
    assert by["youtube"]["options"]["urltest_proxy_links"] == ["vless://z@h:1#Z"]
    assert by["main"]["options"]["user_domains"] == ["a.com", "b.com"]
    # the same option changed on both sides: it stays stale and names it
    db.set_draft(rid, changed(d["config"], "youtube", urltest_proxy_links=["vless://m@h:1#M"]), "fp2")
    rt.agent.config, rt.agent.fp = changed(rt.agent.config, "youtube", urltest_proxy_links=["vless://q@h:1#Q"]), "fp3"
    hub._got_config(rt, _cfg(rt.agent))
    assert rt.draft["stale"] and rt.draft["conflicts"] == ["youtube / urltest_proxy_links: changed on the router too"]


def test_a_scheduled_change_carries_a_loaded_configs_switches(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    fired = []
    hub.after_apply_now = lambda r: fired.append(r.id)
    target = changed(BASE, "main", user_domains=["z.com"])

    async def go():
        # scheduled: the switches wait for that change, not for the next draft
        db.set_draft(rid, target, "fp1", base_config=BASE)
        db.set_setting(f"router_pending:{rid}", {"kill_switch": ["youtube"]})
        vid = await hub.schedule(rid, "fp1", target, "", 2e9)
        assert db.get_setting(f"pending_version:{rid}") == vid
        other = changed(BASE, "youtube", urltest_proxy_links=["vless://o@h:1#O"])
        r = await hub.apply(rid, "fp1", other)
        for t in list(hub.jobs):
            await t
        assert fired == [] and db.version(r["version"])["state"] == "applied"
        db.x("UPDATE versions SET due = 1 WHERE id = ?", vid)
        await hub._run_due()
        for t in list(hub.jobs):
            await t
        assert fired == [rid] and db.get_setting(f"pending_version:{rid}") is None
    asyncio.run(go())


def test_a_reboot_during_an_apply_ends_its_version_from_the_routers_event(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["r.com"])

    class Rebooted(Agent):
        async def call(self, name, *args, **kw):
            if name == "job":
                await asyncio.sleep(0.01)
                raise AgentError("not_found", f"no job {args[0]}")
            return await super().call(name, *args, **kw)

    rt.agent = Rebooted(BASE)

    async def go():
        r = await hub.apply(rid, "fp1", target)
        await asyncio.sleep(0.05)
        hub._on_message(rt, {"id": "b:1", "t": "x", "type": "apply_interrupted", "job": r["job"],
                                   "message": "the apply was interrupted (reboot or killed); the old settings were put back"})
        for t in list(hub.jobs):
            await asyncio.wait_for(t, 10)
        v = db.version(r["version"])
        assert v["state"] == "reverted" and "interrupted" in v["message"]
        assert rt.undone["state"] == "reverted"
    asyncio.run(go())


def test_switches_of_a_busy_router_are_tried_again(tmp_path, monkeypatch):
    db, hub, rt, rid = _hub(tmp_path)
    monkeypatch.setattr(hub, "AFTER_APPLY_WAITS", (0.01, 0.02))
    calls = []

    class Busy(Agent):
        async def call(self, name, *args, payload=None, **kw):
            if name == "strict":
                calls.append(name)
                if len(calls) < 2:
                    raise AgentError("busy", "an apply is in progress")
                return {}
            if name == "status":
                return {}
            return await super().call(name, *args, payload=payload, **kw)

    rt.agent = Busy(BASE)
    hub.refresh = lambda r: asyncio.sleep(0)
    db.set_setting(f"router_pending:{rid}", {"kill_switch": ["main"]})
    asyncio.run(hub._after_apply(rt))
    assert calls == ["strict", "strict"] and db.get_setting(f"router_pending:{rid}") is None
    ev = db.events(rid, 5)[0]
    assert ev["type"] == "settings_on" and ev["data"]["done"] == ["kill switch: main"]


def test_a_revert_after_a_poll_saw_the_new_settings_keeps_the_draft_and_its_switches(tmp_path):
    # the poll during the restart stored the new settings; the router then put the old ones back
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["n.com"])
    db.set_draft(rid, target, "fp1", base_config=BASE)
    db.set_setting(f"router_pending:{rid}", {"kill_switch": ["main"]})
    fired = []
    hub.after_apply_now = lambda r: fired.append(r.id)

    class RevertsLater(Agent):
        state = "restarting"

        async def call(self, name, *args, **kw):
            if name == "job":
                if self.state == "reverted":
                    return {"state": "reverted", "message": "podkop did not come up"}
                return {"state": self.state}
            return await super().call(name, *args, **kw)

    rt.agent = RevertsLater(BASE)

    async def go():
        await hub.apply(rid, "fp1", target)
        rt.agent.config, rt.agent.fp = copy.deepcopy(target), "fp2"
        hub._got_config(rt, _cfg(rt.agent))                 # the poll mid-restart
        rt.agent.config, rt.agent.fp = copy.deepcopy(BASE), "fp1"
        rt.agent.state = "reverted"
        for t in list(hub.jobs):
            await t
        assert db.draft(rid)["config"] == target and fired == []
        assert db.get_setting(f"router_pending:{rid}") == {"kill_switch": ["main"]}
    asyncio.run(go())


def test_the_browser_hears_the_end_only_once_the_applied_draft_is_gone(tmp_path):
    # 1.62: the final job message went out before the draft was deleted: a page that reloaded
    # then found the draft and showed "settings changed on the router" or "N changes not applied"
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["a.com"])
    db.set_draft(rid, target, "fp1", base_config=BASE)
    seen = []
    hub.publish = lambda m: seen.append((m.get("type"), (m.get("job") or {}).get("state"), db.draft(rid) is not None))

    async def go():
        await hub.apply(rid, "fp1", target)
        for t in list(hub.jobs):
            await t
    asyncio.run(go())
    final = [s for s in seen if s[0] == "job" and s[1] == "applied"]
    assert final == [("job", "applied", False)]


def test_a_tracker_that_dies_ends_its_change_so_the_draft_is_not_applying_for_ever(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    target = changed(BASE, "main", user_domains=["a.com"])
    db.set_draft(rid, target, "fp1", base_config=BASE)

    class Odd(Agent):
        async def call(self, name, *args, **kw):
            if name == "job":
                return ["not", "a", "dict"]
            return await super().call(name, *args, **kw)

    rt.agent = Odd(BASE)

    async def go():
        r = await hub.apply(rid, "fp1", target)
        for t in list(hub.jobs):
            await t
        return r
    r = asyncio.run(go())
    assert db.version(r["version"])["state"] == "unknown"
    assert not (rt.draft or {}).get("applying")


def test_switches_left_by_a_restart_are_set_at_start_not_with_the_next_draft(tmp_path):
    db, hub, rt, rid = _hub(tmp_path)
    db.set_setting(f"router_pending:{rid}", {"kill_switch": ["main"]})
    fired = []
    hub.after_apply_now = lambda r: fired.append(r.id)
    hub._resume()
    assert fired == [rid]
    # with a draft it waits for that draft
    fired.clear()
    db.set_draft(rid, BASE, "fp1", base_config=BASE)
    hub._resume()
    assert fired == []
