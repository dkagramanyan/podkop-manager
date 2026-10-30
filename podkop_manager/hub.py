"""The manager's core: one runtime per router (status polling, the agent's
live event stream, apply jobs, probe runs, key usage and traffic), the
nightly probe schedule, and fan-out of live updates to the browsers."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import hashlib
import io
import os
import platform
import shutil
import tarfile
import time
import urllib.request
from pathlib import Path

from . import configs, podkop, usage
from .agent import Agent, AgentError, check_forward
from .db import DB

log = logging.getLogger("podkop_manager")

FINAL_JOB_STATES = {"applied", "saved", "unchanged", "rejected", "reverted", "failed"}
OK_JOB_STATES = {"applied", "saved", "unchanged"}
# events shown next to the router's name until something newer happens
ALERT_EVENTS = ("reverted", "revert_failed", "apply_interrupted")
POLL_SECONDS = 30
TRAFFIC_SECONDS = 60     # the router's traffic per server
EXIT_SECONDS = 3600      # a section's exit IP is looked up again after this long
EXIT_RETRY_SECONDS = 600     # ... or this long after the lookup failed
USAGE_SECONDS = 600      # the providers' numbers, read through the router
# the programs podkop-probe needs to test servers on this server: busybox's
# sh and tools first (as on a router), then podkop-engine-full's sing-box
PROBE_PATH = os.environ.get("PODKOP_MANAGER_PROBE_PATH") or os.environ.get("PATH", "")
PROBE_SECONDS = 3 * 3600
# podkop-engine-full's sing-box (static, with the SSH outbound) for probes run
# here: downloaded once, when asked for, into the data folder (bin/)
ENGINE_VERSION = "1.13.21-r14"
ENGINE_FILES = {   # machine -> (package arch, SHA-256 of the .ipk)
    "x86_64": ("x86_64", "7e23ea61e0ce0a58f101706a9bbd3badeea804af165ba12d714cc8c97b753d9d"),
    "aarch64": ("aarch64_generic", "275e42156a1e36b71310729b6b5557bb2e91aa3c61a68a4f50eb4c61c9ecb7d9"),
}
ENGINE_FILES["amd64"], ENGINE_FILES["arm64"] = ENGINE_FILES["x86_64"], ENGINE_FILES["aarch64"]


def fetch_engine(dest: Path, machine: str = "") -> None:
    """Downloads podkop-engine-full, checks it and puts its sing-box at dest."""
    arch, sha = ENGINE_FILES.get(machine or platform.machine(), (None, None))
    if not arch:
        raise ValueError(f"podkop-engine has no build for {machine or platform.machine()}")
    url = (f"https://github.com/FiyeroT/podkop-engine/releases/download/v{ENGINE_VERSION}/"
           f"podkop-engine-full_{ENGINE_VERSION}_openwrt_{arch}.ipk")
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("the downloaded podkop-engine is not the expected file")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as ipk:
        inner = ipk.extractfile("./data.tar.gz").read()
    with tarfile.open(fileobj=io.BytesIO(inner), mode="r:gz") as pkg:
        binary = pkg.extractfile("./usr/bin/sing-box").read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".new")
    tmp.write_bytes(binary)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dest)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _list(v) -> list:
    """A UCI option as a list: one value or a list."""
    return v if isinstance(v, list) else [v] if v else []


def manager_minutes(router_minutes: int, utc_offset: str) -> int:
    """A time of day on the router's clock (minutes; its UTC offset like
    "+0300") on the manager's clock. Unknown offset: the same clock."""
    try:
        sign = -1 if utc_offset[0] == "-" else 1
        router_off = sign * (int(utc_offset[1:3]) * 60 + int(utc_offset[3:5]))
    except (IndexError, ValueError):
        return router_minutes
    here = (time.localtime().tm_gmtoff or 0) // 60
    return (router_minutes - router_off + here) % 1440


class RouterRuntime:
    def __init__(self, row: dict, key: Path):
        self.id = row["id"]
        self.row = row
        self.agent = Agent(row["host"], row["port"], row["host_key"], key)
        self.status: dict | None = None
        self.proxies: dict | None = None
        self.names: dict = {}
        self.names_fp = ""
        self.config: dict | None = None    # the last config-get
        self.draft: dict | None = None     # summary of the unapplied draft
        self.alert: dict | None = None     # the last change undone on the router
        self.usage: dict = {"pct": None, "alerts": []}   # daily limits, for the menu and Overview
        self.online = False
        self.error = ""
        self.down_since: float | None = None
        self.checked = 0.0
        self.tasks: list[asyncio.Task] = []
        self.wake = asyncio.Event()
        self.move_retry = 0.0              # when to try a failed usage move again
        self.exits: dict = {}              # section -> {server, ip, country, city, org, t} or {server, error, t}
        self.exit_task: asyncio.Task | None = None
        self.exit_ip = True                # False for an agent before 0.3.2 (no exit-ip)
        self.moving = False                 # a move of rules for the traffic limit is under way
        self.local_probe: dict | None = None    # the last probe run on this server: state, times, folder

    def snapshot(self) -> dict:
        return {"id": self.id, "name": self.row["name"], "host": self.row["host"], "port": self.row["port"],
                "online": self.online, "error": self.error, "checked": self.checked, "down_since": self.down_since,
                "status": self.status, "proxies": self.proxies, "names": self.names,
                "draft": self.draft, "alert": self.alert, "usage": self.usage, "exits": self.exits}


class Hub:
    def __init__(self, db: DB, key: Path, src: Path):
        self.db, self.key, self.src = db, key, src
        # the key the routers let forward connections (and nothing else), for probes run here
        self.forward_key = key.parent / "id_forward"
        self.engine = {"state": "", "error": ""}    # its download: "", "downloading", "failed"
        self.auto_engine = False     # serve: download podkop-engine at start when it isn't there
        self.routers: dict[int, RouterRuntime] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self.tasks: list[asyncio.Task] = []
        self.probes: dict[int, asyncio.Task] = {}
        self.jobs: set[asyncio.Task] = set()      # apply jobs being followed

    # --- lifecycle ------------------------------------------------------------------
    async def start(self) -> None:
        if self.tasks:
            return
        for row in self.db.routers():
            self._start_router(row)
        self.tasks.append(asyncio.create_task(self._scheduler()))
        self.tasks.append(asyncio.create_task(self._pruner()))
        self._resume()
        if self.auto_engine and not shutil.which("sing-box", path=self.probe_path):
            self.download_engine()

    async def stop(self) -> None:
        for rt in list(self.routers.values()):
            await self._stop_router(rt.id)
        for t in [*self.tasks, *self.probes.values(), *self.jobs]:
            t.cancel()
        self.tasks, self.probes, self.jobs = [], {}, set()

    def _start_router(self, row: dict) -> RouterRuntime:
        rt = RouterRuntime(row, self.key)
        self.routers[rt.id] = rt
        ev = self.db.one(f"SELECT type, t FROM events WHERE router_id = ? AND type IN "
                         f"({', '.join('?' * len(ALERT_EVENTS))}) ORDER BY id DESC LIMIT 1", rt.id, *ALERT_EVENTS)
        rt.alert = ev
        self.update_draft(rt)
        self.db.sync_sub_keys(rt.id, self.db.probe_settings(rt.id)["keys"])
        try:
            self._lists_to_rules(rt.id)
            self._countries_to_rules(rt.id)
        except ValueError as e:      # odd old probe options: leave the rules as they are
            log.warning("router %s: old probe settings not converted: %s", rt.id, e)
        rt.tasks = [asyncio.create_task(self._poll_loop(rt)), asyncio.create_task(self._watch_loop(rt)),
                    asyncio.create_task(self._usage_loop(rt))]
        return rt

    async def _stop_router(self, rid: int) -> None:
        rt = self.routers.pop(rid, None)
        probe = self.probes.pop(rid, None)
        if rt:
            for t in rt.tasks + [x for x in (rt.exit_task, probe) if x]:
                t.cancel()
            rt.agent.close()

    def add_router(self, row: dict) -> None:
        self._start_router(row)

    async def remove_router(self, rid: int) -> None:
        await self._stop_router(rid)
        self.db.delete_router(rid)

    def get(self, rid: int) -> RouterRuntime:
        rt = self.routers.get(rid)
        if rt is None:
            raise KeyError(rid)
        return rt

    # --- live updates -----------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, msg: dict) -> None:
        for q in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(msg)

    # --- polling and the event stream ------------------------------------------------
    async def refresh(self, rt: RouterRuntime) -> None:
        try:
            old = (rt.status or {}).get("agent")
            rt.status = await rt.agent.call("status", timeout=30)
            if rt.status.get("agent") != old:
                rt.exit_ip = True                 # a new agent may have exit-ip
            if rt.status.get("fingerprint") != rt.names_fp:
                self._got_config(rt, await rt.agent.call("config-get", timeout=30))
            rt.proxies = None
            if rt.status["podkop"]["running"]:
                with contextlib.suppress(AgentError):
                    rt.proxies = await rt.agent.call("proxies", timeout=30)
            rt.online, rt.error, rt.down_since = True, "", None
            self._check_exits(rt)
        except AgentError as e:
            rt.online, rt.error = False, e.message
            rt.down_since = rt.down_since or time.time()
        except (KeyError, TypeError, AttributeError) as e:     # an answer without the expected fields
            log.warning("status of %s: unexpected answer: %r", rt.row["host"], e)
            rt.online, rt.error = False, "podkop-agent gave an unexpected answer"
            rt.down_since = rt.down_since or time.time()
        rt.checked = time.time()
        self.publish({"type": "router", "router": rt.snapshot()})

    # --- where each section's traffic leaves to the internet ------------------------
    def _check_exits(self, rt: RouterRuntime) -> None:
        """Look up the exit IP of the sections whose server changed, or whose
        last lookup is old, in the background."""
        if not rt.exit_ip or not rt.config or (rt.exit_task and not rt.exit_task.done()):
            return
        targets = podkop.exit_targets(rt.config["config"], rt.proxies)
        for s in [s for s in rt.exits if s not in targets]:
            del rt.exits[s]
        now = time.time()
        todo = {s: t for s, t in targets.items()
                if (e := rt.exits.get(s)) is None or e.get("server") != t
                or now - e["t"] > (EXIT_RETRY_SECONDS if e.get("error") else EXIT_SECONDS)}
        if todo:
            rt.exit_task = asyncio.create_task(self._read_exits(rt, todo))

    async def _read_exits(self, rt: RouterRuntime, todo: dict) -> None:
        for section, target in todo.items():
            try:
                e = await rt.agent.call("exit-ip", section, timeout=30)
                rt.exits[section] = {**e, "t": time.time()}
            except AgentError as e:
                if e.code == "bad_request" and e.message.startswith("unknown command"):
                    rt.exit_ip = False          # an agent before 0.3.2
                    return
                rt.exits[section] = {"server": target, "error": e.message, "t": time.time()}
            self.publish({"type": "router", "router": rt.snapshot()})

    async def _poll_loop(self, rt: RouterRuntime) -> None:
        while True:
            try:
                await self.refresh(rt)
            except Exception:  # noqa: BLE001 - polling must go on whatever happens
                log.exception("poll %s", rt.row["host"])
            rt.wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(rt.wake.wait(), timeout=POLL_SECONDS)

    async def _watch_loop(self, rt: RouterRuntime) -> None:
        delay = 5
        while True:
            try:
                since = self.db.router(rt.id)["last_event"] or ""
                async for m in rt.agent.watch(since):
                    delay = 5
                    self._on_message(rt, m)
            except AgentError as e:
                log.info("watch %s: %s", rt.row["host"], e.message)
            except Exception:  # noqa: BLE001 - keep the stream alive whatever happens
                log.exception("watch %s", rt.row["host"])
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    def _on_message(self, rt: RouterRuntime, m: dict) -> None:
        typ = m.get("type")
        if typ == "hello":
            return
        if typ == "log":
            line = str(m.get("line", ""))
            p = podkop.parse_log(line)
            self.db.add_log(rt.id, line, p["source"], p["level"])
            self.publish({"type": "log", "router_id": rt.id, "line": line, "t": time.time(), **p})
            return
        data = {k: v for k, v in m.items() if k not in ("id", "t", "type")}
        self.db.add_event(rt.id, typ, dict(data, agent_time=m.get("t")))
        if typ in ALERT_EVENTS:
            rt.alert = {"type": typ, "t": time.time()}
        if m.get("id"):
            self.db.update_router(rt.id, last_event=m["id"])
        self.publish({"type": "event", "router_id": rt.id, "event": typ, "data": data, "t": m.get("t")})
        rt.wake.set()

    # --- config ------------------------------------------------------------------------
    def _got_config(self, rt: RouterRuntime, cfg: dict) -> None:
        rt.config = cfg
        rt.names, rt.names_fp = podkop.tag_names(cfg["config"]), cfg["fingerprint"]
        self.update_draft(rt)

    async def config(self, rid: int) -> dict:
        rt = self.get(rid)
        cfg = await rt.agent.call("config-get", timeout=30)
        self._got_config(rt, cfg)
        return cfg

    # --- the draft: changes made in the web interface, not applied yet ---------------
    def update_draft(self, rt: RouterRuntime) -> None:
        """Recount the draft's changes against the router's settings; a draft
        without changes is dropped."""
        d = self.db.draft(rt.id)
        if not d or not rt.config:
            rt.draft = {"changes": None, "base": d["base"], "updated": d["updated"], "stale": None} if d else None
            return
        n = len(podkop.diff(rt.config["config"], d["config"]))
        if n == 0:
            self.db.delete_draft(rt.id)
            rt.draft = None
            return
        rt.draft = {"changes": n, "base": d["base"], "updated": d["updated"],
                    "stale": d["base"] != rt.config["fingerprint"]}

    async def diff(self, rid: int, config: dict) -> dict:
        cur = await self.config(rid)
        return {"fingerprint": cur["fingerprint"], "diff": podkop.diff(cur["config"], config)}

    async def validate(self, rid: int, config: dict) -> list:
        return (await self.get(rid).agent.call("validate", payload={"config": config}, timeout=90))["problems"]

    async def apply(self, rid: int, base: str, config: dict, note: str = "", trigger: str = "web") -> dict:
        rt = self.get(rid)
        cur = await self.config(rid)
        d = podkop.diff(cur["config"], config)
        vid = self.db.add_version(rid, config, d, note, base, trigger)
        try:
            job = (await rt.agent.call("apply", payload={"base": base, "config": config}, timeout=30))["job"]
        except AgentError as e:
            self.db.update_version(vid, state="failed", message=e.message)
            raise
        self.db.update_version(vid, job=job, state="running")
        self._follow_job(rt, vid, job)
        return {"version": vid, "job": job}

    def _follow_job(self, rt: RouterRuntime, vid: int, job: str) -> None:
        task = asyncio.create_task(self._track_job(rt, vid, job))
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        rt.tasks.append(task)               # stopped with the router
        task.add_done_callback(lambda t: t in rt.tasks and rt.tasks.remove(t))

    def _resume(self) -> None:
        """After a restart (or a restore): follow the changes and probe runs
        the router may still be busy with; the agent keeps their state."""
        for v in self.db.q("SELECT id, router_id, job FROM versions WHERE state IN ('pending', 'running')"):
            rt = self.routers.get(v["router_id"])
            if rt and v["job"]:
                self._follow_job(rt, v["id"], v["job"])
            else:
                self.db.update_version(v["id"], state="unknown",
                                       message="the manager restarted while sending this change; check the router")
        for r in self.db.q("SELECT id, router_id, trigger FROM probe_runs WHERE state = 'running'"):
            rt = self.routers.get(r["router_id"])
            local = self.db.probe_settings(r["router_id"]).get("test_from") == "manager"
            if rt and r["router_id"] not in self.probes and not local:
                self.probes[rt.id] = asyncio.create_task(self._track_probe(rt, r["id"], r["trigger"]))
            else:
                self.db.finish_probe_run(r["id"], "failed", {}, None)

    async def _track_job(self, rt: RouterRuntime, vid: int, job: str) -> dict | None:
        deadline = time.time() + 600
        data = None
        while time.time() < deadline:
            try:
                # short waits, so the browser sees checking -> restarting -> done
                data = await rt.agent.call("job", job, "2", timeout=30) or {}
            except AgentError as e:
                log.info("job %s on %s: %s", job, rt.row["host"], e.message)
                await asyncio.sleep(5)
                continue
            self.publish({"type": "job", "router_id": rt.id, "version": vid, "job": data})
            if data.get("state") in FINAL_JOB_STATES:
                break
        state = (data or {}).get("state", "unknown")
        if state not in FINAL_JOB_STATES:
            state = "unknown"
        self.db.update_version(vid, state=state, message=(data or {}).get("message", "no answer from the router"),
                               details=data)
        if state in OK_JOB_STATES:
            # the draft that was applied is done with
            draft, version = self.db.draft(rt.id), self.db.version(vid)
            if draft and version and not podkop.diff(draft["config"], version["config"]):
                self.db.delete_draft(rt.id)
                self.db.set_setting(f"rules_base:{rt.id}", None)
                self.db.set_setting(f"rule_keys_base:{rt.id}", None)
                self.db.set_setting(f"rule_links_base:{rt.id}", None)
                rt.draft = None
        rt.wake.set()
        return data

    async def wait_version(self, vid: int, timeout: float = 600) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            v = self.db.version(vid)
            if v and v["state"] not in ("pending", "running"):
                return v
            await asyncio.sleep(2)
        return self.db.version(vid)

    # --- probe ----------------------------------------------------------------------------
    async def start_probe(self, rid: int, trigger: str = "manual") -> int:
        rt = self.get(rid)
        ps = self.db.probe_settings(rid)
        if not ps.get("keys"):
            raise AgentError("no_keys", "add your keys (subscription URLs) first")
        if rid in self.probes and not self.probes[rid].done():
            raise AgentError("busy", "a probe is already running")
        options = self.probe_options(rid)
        agent = [int(x) for x in (((rt.status or {}).get("agent") or {}).get("version") or "0").split(".") if x.isdigit()]
        if ps.get("test_from") == "manager":
            if why := self.local_probe_missing():
                raise AgentError("no_engine", f"this server can't test servers: {why}")
            if agent < [0, 4, 0]:
                raise AgentError("old_agent", "the router's podkop-agent is too old to test servers here: reinstall it")
            run_id = self.db.add_probe_run(rid, trigger, ps["keys"])
            self.probes[rid] = asyncio.create_task(self._run_local_probe(rt, run_id, trigger, options))
            self.publish({"type": "probe", "router_id": rid, "state": "running", "run": run_id})
            return run_id
        if ps.get("fix_fingerprint") and agent >= [0, 3, 1]:
            options.append("--fix-fingerprint")    # test them as they will be used
        await rt.agent.call("probe-start", payload={"keys": ps["keys"], "options": options}, timeout=30)
        run_id = self.db.add_probe_run(rid, trigger, ps["keys"])
        self.probes[rid] = asyncio.create_task(self._track_probe(rt, run_id, trigger))
        self.publish({"type": "probe", "router_id": rid, "state": "running", "run": run_id})
        return run_id

    async def _track_probe(self, rt: RouterRuntime, run_id: int, trigger: str) -> None:
        st: dict = {}
        end = time.time() + 3 * 3600
        while time.time() < end:
            await asyncio.sleep(15)
            try:
                st = await rt.agent.call("probe-status", timeout=30)
            except AgentError:
                continue
            self.publish({"type": "probe", "router_id": rt.id, "state": st.get("state"), "run": run_id})
            if st.get("state") != "running":
                break
        await self._finish_probe(rt, run_id, trigger, st)

    async def _finish_probe(self, rt: RouterRuntime, run_id: int, trigger: str, st: dict) -> None:
        state = st.get("state", "unknown")
        if state not in ("done", "failed"):
            state = "failed"            # still running after 3 hours, or no answer at all
        vid = None
        ps = self.db.probe_settings(rt.id)
        if state == "done":
            for section, pick in self.auto_picks(rt.id, st).items():
                if not pick["links"]:
                    self._event(rt, "auto_rule_empty", {"section": section})
        if state == "done" and trigger == "schedule" and ps.get("auto_apply"):
            try:
                vid = await self.apply_probe(rt.id, st, note="nightly probe")
            except (AgentError, ValueError) as e:
                self._event(rt, "probe_apply_failed", {"message": str(e)})
        self.db.finish_probe_run(run_id, state, st, vid)
        self.publish({"type": "probe", "router_id": rt.id, "state": state, "run": run_id})

    # --- probe on this server, through the router -------------------------------------------
    @property
    def probe_path(self) -> str:
        """PATH for podkop-probe here: the downloaded sing-box first."""
        return f"{self.key.parent / 'bin'}:{PROBE_PATH}"

    def download_engine(self) -> None:
        """Starts the download of podkop-engine's sing-box (once, kept in the data folder)."""
        if self.engine["state"] == "downloading":
            return

        async def go():
            try:
                await asyncio.to_thread(fetch_engine, self.key.parent / "bin" / "sing-box")
                self.engine = {"state": "", "error": ""}
            except (OSError, ValueError, KeyError, tarfile.TarError) as e:
                self.engine = {"state": "failed", "error": str(e) or type(e).__name__}
            self.publish({"type": "engine", **self.engine})
        self.engine = {"state": "downloading", "error": ""}
        task = asyncio.create_task(go())
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)

    def local_probe_missing(self) -> str:
        """What this server lacks to run podkop-probe itself ("" = nothing)."""
        if not shutil.which("sing-box", path=self.probe_path):
            return {"downloading": "podkop-engine is downloading…"}.get(
                self.engine["state"], "podkop-engine is not downloaded yet")
        miss = [p for p in ("sh", "curl", "jq") if not shutil.which(p, path=self.probe_path)]
        if miss:
            return f"{', '.join(miss)} not found (the Docker image has them)"
        if not self.forward_key.exists():
            return "no forwarding key"
        return ""

    def forward_public(self) -> str:
        import asyncssh
        return asyncssh.read_private_key(str(self.forward_key)).export_public_key().decode().strip()

    async def check_forward(self, rid: int) -> str:
        """Connects with the forwarding key and opens a connection through the
        router; returns the router's answer to that or raises AgentError."""
        rt = self.get(rid)
        return await check_forward(rt.row["host"], rt.row["port"], rt.row["host_key"], self.forward_key)

    async def _run_local_probe(self, rt: RouterRuntime, run_id: int, trigger: str, options: list) -> None:
        """The router builds the servers of the keys with podkop's parser
        (probe-nodes), podkop-probe tests them here with every connection
        going out through the router over SSH (the forwarding key)."""
        d = self.key.parent / "probe" / str(rt.id)
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)              # the servers' links and keys are in there
        for f in d.iterdir():
            f.unlink()
        rt.local_probe = lp = {"state": "running", "started": _utc(), "finished": "", "exit": None, "dir": d}
        rc, proc = 1, None
        try:
            with open(d / "log", "w") as out:
                ps = self.db.probe_settings(rt.id)
                try:
                    nodes = await rt.agent.call("probe-nodes", payload={
                        "keys": ps["keys"], "fix_fingerprint": bool(ps.get("fix_fingerprint"))}, timeout=600)
                except AgentError as e:
                    out.write(f"podkop-manager: the router could not read the keys: {e.message}\n")
                    nodes = None
                lines = [f"{n['link']}\t" + (json.dumps(n["outbound"], separators=(",", ":")) if n.get("outbound")
                                             else "!" + (n.get("why") or "podkop can't use it")) + "\n"
                         for n in nodes or [] if isinstance(n, dict) and n.get("link")]
                if nodes is not None and not lines:
                    out.write("podkop-manager: the keys have no servers\n")
                if lines:
                    (d / "nodes.txt").write_text("".join(lines))
                    out.flush()
                    proc = await asyncio.create_subprocess_exec(
                        "sh", str(self.src / "router" / "probe.sh"), "--no-apply", "--no-color",
                        "-o", str(d / "result.csv"), "--nodes", str(d / "nodes.txt"),
                        "--via", f"root@{rt.row['host']}:{rt.row['port']}", "--via-key", str(self.forward_key),
                        "--via-host-key", rt.row["host_key"], *options,
                        stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=asyncio.subprocess.STDOUT,
                        cwd=d, env={**os.environ, "PATH": self.probe_path}, start_new_session=True)
                    try:
                        rc = await asyncio.wait_for(proc.wait(), PROBE_SECONDS)
                    except asyncio.TimeoutError:
                        proc.terminate()            # TERM: it stops its sing-box and cleans up
                        await proc.wait()
                        out.write("podkop-manager: the probe took longer than 3 hours\n")
                        rc = 1
        finally:
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
            (d / "nodes.txt").unlink(missing_ok=True)
        lp.update(state="done" if rc == 0 else "failed", finished=_utc(), exit=rc)
        await self._finish_probe(rt, run_id, trigger, self._local_status(rt))

    def _local_status(self, rt: RouterRuntime) -> dict:
        """Like the agent's probe-status, for the run on this server."""
        lp = rt.local_probe or {}
        d = lp["dir"]

        def read(name: str) -> str:
            try:
                return (d / name).read_text(errors="replace")
            except OSError:
                return ""
        return {"state": lp["state"], "started": lp["started"], "finished": lp["finished"], "exit": lp["exit"],
                "log": [x for x in read("log").splitlines() if x][-400:], "local": True,
                "rows": podkop.probe_csv_rows(read("result.csv"), read("result.csv.links")),
                "lists": {"main": [], "main_fallback": [], "youtube": [], "youtube_fallback": []}}

    async def probe_status(self, rt: RouterRuntime) -> dict:
        """The last probe run: the one on this server, or the router's."""
        if rt.local_probe:
            return self._local_status(rt)
        if self.db.probe_settings(rt.id).get("test_from") == "manager":
            last = next(iter(self.db.probe_runs(rt.id, 1)), None)
            return (self.db.probe_result(last["id"]) if last else None) or {"state": "none", "log": [], "rows": []}
        return await rt.agent.call("probe-status", timeout=30)

    def _event(self, rt: RouterRuntime, etype: str, data: dict) -> None:
        """An event of the manager itself, stored and sent like the router's."""
        self.db.add_event(rt.id, etype, data)
        self.publish({"type": "event", "router_id": rt.id, "event": etype, "data": data, "t": time.time()})

    def _lists_to_rules(self, rid: int) -> None:
        """Once: the probe's own main and YouTube lists of older versions become
        rules that take servers from the probe, with the same limits."""
        if self.db.get_setting(f"lists_to_rules:{rid}"):
            return
        self.db.set_setting(f"lists_to_rules:{rid}", True)
        ps = self.db.probe_settings(rid)
        if not ps.get("keys") and not self.db.probe_runs(rid):
            return
        opts = ps.get("options") or []
        max_ping, min_speed = podkop.probe_limits(opts)
        top = int(podkop._option(opts, "--top", 10))
        for section, youtube in ((ps.get("main_section"), ""), (ps.get("youtube_section"), "RU")):
            if section and not self.db.auto_rule(rid, section) and not (youtube and "--no-youtube" in opts):
                self.db.set_auto_rule(rid, section, podkop.check_auto_rule(
                    {"top": top, "fallback": 5, "max_ping": max_ping, "min_speed": min_speed, "youtube": youtube}))

    def auto_picks(self, rid: int, status: dict) -> dict:
        """The servers of the last probe for each rule that takes them from it;
        with fix_fingerprint, REALITY links get a fingerprint new servers accept."""
        # never the servers of keys moved away from for their traffic limit
        if avoided := self._avoided_links(rid):
            status = dict(status, rows=[r for r in status.get("rows") or [] if usage.norm(r.get("link") or "") not in avoided])
        # each server's key, for rules that take only some keys; ids of keys removed since don't count,
        # so a rule whose keys are all gone takes every key's servers again
        keys, rk = self.db.keys(rid), self.db.rule_keys(rid)
        subs, index = {k["id"] for k in keys if k["kind"] == "sub"}, usage.link_index(keys)
        rows = [dict(r, key=index.get(usage.norm(r.get("link") or ""))) for r in status.get("rows") or []]
        picks = {r["section"]: podkop.auto_pick(rows, dict(r, keys=[k for k in rk.get(r["section"], []) if k in subs]))
                 for r in self.db.auto_rules(rid)}
        if self.db.probe_settings(rid).get("fix_fingerprint"):
            for p in picks.values():
                p["links"], p["fallback"] = ([podkop.fix_fingerprint(x) for x in p[k]] for k in ("links", "fallback"))
                for row in p["matched"] + p["fallback_rows"]:
                    row["link"] = podkop.fix_fingerprint(row["link"])
        return picks

    def _countries_to_rules(self, rid: int) -> None:
        """Once: the countries the probe page tested become the countries of
        the rules that took any country, so they keep getting the same servers."""
        if self.db.get_setting(f"countries_to_rules:{rid}"):
            return
        self.db.set_setting(f"countries_to_rules:{rid}", True)
        opts = self.db.probe_settings(rid).get("options") or []
        c = opts[opts.index("--countries") + 1] if "--countries" in opts[:-1] else "all"
        codes = list(podkop.EUROPE) if c.lower() in ("europe", "eu") else [] if c in ("all", "") else c.split(",")
        for r in self.db.auto_rules(rid):
            if codes and not r["countries"]:
                self.db.set_auto_rule(rid, r["section"], podkop.check_auto_rule(dict(r, countries=codes)))
        self.db.set_probe_settings(rid, options=[])

    def probe_options(self, rid: int) -> list:
        """The probe's options for a run, from the rules: their countries, the
        loosest limits, and the YouTube check if a rule needs it. Rules pick
        by exit country, so relays whose entry is elsewhere (e.g. whitelist
        bypasses entering in Russia) are tested too."""
        rules = self.db.auto_rules(rid)
        countries = sorted({c for r in rules for c in r["countries"]})
        out = ["--countries", ",".join(countries) if rules and all(r["countries"] for r in rules) else "all",
               "--no-entry-check"]
        if rules:
            out += ["--max-ping", str(max(r["max_ping"] for r in rules)),
                    "--min-speed", f"{min(r['min_speed'] for r in rules):g}"]
        if not any(r["youtube"] for r in rules):
            out.append("--no-youtube")
        return out

    # --- a router that already runs podkop ---------------------------------------------------
    async def import_found(self, rid: int) -> None:
        """Right after a router is added: keep its podkop settings in Configs,
        and take the keys and lists podkop-probe uses there by itself into the
        manager. Nothing on the router changes."""
        if self.db.get_setting(f"imported:{rid}"):
            return
        rt = self.get(rid)
        await self.refresh(rt)
        try:
            cur = await self.config(rid)
        except AgentError as e:
            log.info("import from %s: %s", rt.row["host"], e.message)
            return
        if self.routers.get(rid) is not rt:
            return                  # removed meanwhile
        self.db.set_setting(f"imported:{rid}", True)
        name = f"{rt.row['name']}, as found {time.strftime('%Y-%m-%d %H:%M')}"
        version = (((rt.status or {}).get("podkop")) or {}).get("version") or ""
        with contextlib.suppress(ValueError):
            self.db.add_config(name, configs.check_config(cur["config"]), {}, f"{rt.row['name']}, when added", version)
        found = {"config": name, "keys": 0, "rules": []}
        try:
            pc = await rt.agent.call("probe-config", timeout=30) or {}
        except AgentError:           # an agent before 0.3.1
            pc = {}
        if self.routers.get(rid) is not rt:
            return
        if pc.get("saved"):
            keys = [k for k in pc.get("keys") or [] if k.startswith(("http://", "https://"))]
            if keys and not self.db.probe_settings(rid).get("keys"):
                self.db.set_probe_settings(rid, keys=keys)
                self.db.sync_sub_keys(rid, keys)
                found["keys"] = len(keys)
            found["rules"] = self._probe_rules_from(rid, cur["config"], pc)
        self._event(rt, "imported", found)

    def _probe_rules_from(self, rid: int, config: dict, pc: dict) -> list:
        """podkop-probe's own lists (its main and YouTube sections) as rules that
        take servers from the probe, with its countries and limits."""
        c = str(pc.get("countries") or "")
        countries = list(podkop.EUROPE) if c.lower() in ("europe", "eu") else [] if c in ("all", "") else c.split(",")
        names = {s["name"] for s in config["sections"] if s["type"] == "section"}
        base = {"countries": countries, "top": pc.get("top") or 10, "fallback": 5,
                "max_ping": pc.get("max_ping") or 700, "min_speed": pc.get("min_speed") or 40}
        made = []
        # podkop-probe's ad-free countries: one or more codes, or none for any country
        noads = str(pc.get("youtube_noads") or "").split()
        for section, youtube, used in ((pc.get("section"), "", pc.get("apply_to") in ("both", "main")),
                                       (pc.get("youtube_section"), noads[0].upper() if noads else "plays",
                                        pc.get("youtube") and pc.get("apply_to") in ("both", "youtube"))):
            if used and section in names and not self.db.auto_rule(rid, section):
                with contextlib.suppress(ValueError):
                    self.db.set_auto_rule(rid, section, podkop.check_auto_rule(dict(base, youtube=youtube)))
                    made.append(section)
        return made

    async def take_over_probe(self, rt: RouterRuntime) -> dict:
        """Turn off podkop-probe's own cron line; the manager runs it at that
        time (on the manager's clock) with its keys. Refused, with nothing
        changed, when the manager could not run it."""
        status = rt.status or {}
        cron = ((status.get("probe") or {}).get("cron") or "").split()
        if not (len(cron) >= 2 and cron[0].isdigit() and cron[1].isdigit() and int(cron[0]) < 60 and int(cron[1]) < 24):
            raise AgentError("bad_request", "the router's own run has no single daily time; "
                             "set the time in a rule's Nightly probe (Routing → Rules), then turn the router's run off")
        minutes = manager_minutes(int(cron[1]) * 60 + int(cron[0]), (status.get("router") or {}).get("utc_offset") or "")
        fields = {"auto_apply": 1, "schedule": f"{minutes // 60:02d}:{minutes % 60:02d}"}
        if not self.db.probe_settings(rt.id).get("keys"):
            # routers added before the manager took the probe's keys at once
            pc = await rt.agent.call("probe-config", timeout=30) or {}
            keys = [k for k in pc.get("keys") or [] if isinstance(k, str) and k.startswith(("http://", "https://"))]
            if not keys:
                raise AgentError("no_keys", "the router's podkop-probe has no keys the manager could use; add them in Keys and usage first")
            fields["keys"] = keys
        await rt.agent.call("probe-cron", "off", timeout=30)
        self.db.set_probe_settings(rt.id, **fields)
        if "keys" in fields:
            self.db.sync_sub_keys(rt.id, fields["keys"])
        rt.wake.set()
        return self.db.probe_settings(rt.id)

    def keep_rules(self, rid: int) -> None:
        """Before the first change of a router's probe rules or rules' keys and
        own servers since its last apply: remember them, so discarding the
        draft puts them back."""
        if self.db.get_setting(f"rules_base:{rid}") is None:
            self.db.set_setting(f"rules_base:{rid}", [
                {k: r[k] for k in r if k not in ("router_id", "section")} | {"section": r["section"]}
                for r in self.db.auto_rules(rid)])
        for name in ("rule_keys", "rule_links"):
            if self.db.get_setting(f"{name}_base:{rid}") is None:
                self.db.set_setting(f"{name}_base:{rid}", self.db.get_setting(f"{name}:{rid}") or {})

    def restore_rules(self, rid: int) -> None:
        """Discard: the probe rules and rules' keys and own servers as they were before the draft."""
        for name in ("rule_keys", "rule_links"):
            if (kept := self.db.get_setting(f"{name}_base:{rid}")) is not None:
                self.db.set_setting(f"{name}:{rid}", kept)
                self.db.set_setting(f"{name}_base:{rid}", None)
        base = self.db.get_setting(f"rules_base:{rid}")
        if base is None:
            return
        for r in self.db.auto_rules(rid):
            self.db.delete_auto_rule(rid, r["section"])
        for r in base:
            self.db.set_auto_rule(rid, r["section"], podkop.check_auto_rule(r))
        self.db.set_setting(f"rules_base:{rid}", None)

    def load_config(self, rid: int, saved: dict) -> dict:
        """A saved config as the router's new draft: its rules that take
        servers from the probe replace the router's for those sections, and
        get the last probe's servers right away."""
        config = saved["config"]
        self.keep_rules(rid)
        for s in config["sections"]:
            if s["type"] != "section":
                continue
            if s["name"] in saved["rules"]:
                self.db.set_auto_rule(rid, s["name"], saved["rules"][s["name"]])
            else:
                self.db.delete_auto_rule(rid, s["name"])
        if saved["rules"] and (last := self.db.last_probe_result(rid)):
            with contextlib.suppress(ValueError):
                config = self.probe_config(rid, {"config": config}, last)
        rt = self.get(rid)
        self.db.set_draft(rid, config, (rt.status or {}).get("fingerprint") or "")
        self.update_draft(rt)
        self.publish({"type": "router", "router": rt.snapshot()})
        return config

    # --- URLTest rules that take every server of their keys (keys chosen, no probe) -----------
    def sub_links(self, rid: int) -> list:
        """The router's subscriptions, each with its servers podkop can build
        (with the chrome fingerprint, if the probe uses it)."""
        fix = self.db.probe_settings(rid).get("fix_fingerprint")
        return [{"id": k["id"], "label": usage.key_label(k),
                 "links": [podkop.fix_fingerprint(x) if fix else x for x in k["links"] if podkop.link_protocol(x) in podkop.PROTOCOLS]}
                for k in self.db.keys(rid) if k["kind"] == "sub"]

    def key_rule_links(self, rid: int) -> dict:
        """{section: links} of the rules that take every server of their keys:
        the keys' servers, then the rule's own servers, each once. Rules that
        take links from the probe use their keys there instead."""
        subs = {k["id"]: k["links"] for k in self.sub_links(rid)}
        probe, own = {r["section"] for r in self.db.auto_rules(rid)}, self.db.rule_links(rid)
        out = {}
        for section, ids in self.db.rule_keys(rid).items():
            if section in probe:
                continue
            seen, links = set(), []
            for x in [x for i in ids for x in subs.get(i, [])] + own.get(section, []):
                if usage.norm(x) not in seen:
                    seen.add(usage.norm(x))
                    links.append(x)
            out[section] = links
        return out

    async def sync_key_rules(self, rt: RouterRuntime) -> None:
        """When a provider changed its servers, the rules that take every
        server of its key get the new list (with their own servers) on the
        router (once per list, so a failed apply is not tried again and
        again), and an open draft gets it too, so applying the draft doesn't
        put the old servers back. Not rules whose keys or own servers were
        changed in a draft not applied yet."""
        def unchanged(s: str) -> bool:
            return all((base := self.db.get_setting(f"{name}_base:{rt.id}")) is None
                       or base.get(s) == (self.db.get_setting(f"{name}:{rt.id}") or {}).get(s)
                       for name in ("rule_keys", "rule_links"))
        wanted = {s: links for s, links in self.key_rule_links(rt.id).items() if links and unchanged(s)}
        if not wanted:
            return
        cur = await self.config(rt.id)
        new = json.loads(json.dumps(cur["config"]))
        secs = podkop.sections_by_name(new)
        synced = self.db.get_setting(f"keys_synced:{rt.id}") or {}
        changed = []
        for name, links in wanted.items():
            o = (secs.get(name) or {}).get("options") or {}
            if (o.get("connection_type") or "proxy") != "proxy" or o.get("proxy_config_type") != "urltest":
                continue
            have, want = sorted({usage.norm(x) for x in _list(o.get("urltest_proxy_links"))}), sorted({usage.norm(x) for x in links})
            if want != have and want != synced.get(name):
                o["urltest_proxy_links"] = links
                changed.append(name)
            synced[name] = want
        if changed:
            rk, labels = self.db.rule_keys(rt.id), {k["id"]: k["label"] for k in self.sub_links(rt.id)}
            names = sorted({labels[i] for s in changed for i in rk.get(s, []) if i in labels})
            try:
                await self.apply(rt.id, cur["fingerprint"], new, f"new servers of {', '.join(names)}: {', '.join(changed)}", "keys")
                if d := self.db.draft(rt.id):
                    ds = podkop.sections_by_name(d["config"])
                    for name in changed:
                        if name in ds:
                            ds[name].setdefault("options", {})["urltest_proxy_links"] = wanted[name]
                    self.db.set_draft(rt.id, d["config"], d["base"])
                    self.update_draft(rt)
                    self.publish({"type": "router", "router": rt.snapshot()})
            except (AgentError, ValueError) as e:
                self._event(rt, "keys_sync_failed", {"message": str(e)})
        self.db.set_setting(f"keys_synced:{rt.id}", synced)

    def _avoided_links(self, rid: int) -> set:
        """Links of the keys that passed their traffic threshold and are set to
        move rules to other keys."""
        now = time.time()
        return {usage.norm(link) for k in self.db.keys(rid) if k["action"] == "avoid" and (k["acted_until"] or 0) > now
                for link in (k["links"] if k["kind"] == "sub" else [k["url"]])}

    def probe_config(self, rid: int, cur: dict, status: dict) -> dict:
        """The router's config with the probe's lists and the rules by country
        filled in, without the servers of keys moved away from; ValueError
        when nothing would change."""
        picks = self.auto_picks(rid, status)
        if not any(p["links"] for p in picks.values()):
            raise ValueError("the probe found no server for any rule")
        return podkop.merge_probe(cur["config"], picks)

    async def apply_probe(self, rid: int, status: dict, note: str) -> int:
        cur = await self.config(rid)
        new = self.probe_config(rid, cur, status)
        return (await self.apply(rid, cur["fingerprint"], new, note, "probe"))["version"]

    # --- keys and usage --------------------------------------------------------------------
    async def _usage_loop(self, rt: RouterRuntime) -> None:
        last = 0.0
        while True:
            await asyncio.sleep(TRAFFIC_SECONDS)
            if not rt.online:
                continue
            try:
                await self.read_traffic(rt)
                if time.time() - last >= USAGE_SECONDS:
                    last = time.time()
                    await self.read_usage(rt)
                    await self.sync_key_rules(rt)
            except AgentError as e:
                log.info("usage %s: %s", rt.row["host"], e.message)
            except Exception:  # noqa: BLE001 - keep counting whatever happens
                log.exception("usage %s", rt.row["host"])
            try:
                await self.check_usage(rt)
            except Exception:  # noqa: BLE001
                log.exception("usage check %s", rt.row["host"])

    async def read_usage(self, rt: RouterRuntime) -> None:
        """Each subscription's usage header and server list, fetched on the router."""
        keys = {k["url"]: k for k in self.db.keys(rt.id) if k["kind"] == "sub"}
        if not keys:
            return
        now = time.time()
        for r in await rt.agent.call("sub-info", payload={"keys": list(keys)}, timeout=300) or []:
            k = keys.get(r.get("url"))
            if not k:
                continue
            fields = {"title": usage.parse_title(r.get("title") or ""), "why": r.get("why") or ""}
            if r.get("links"):
                fields["links"] = r["links"]
            self.db.update_key(k["id"], **fields)
            if info := usage.parse_userinfo(r.get("usage") or ""):
                self.db.add_key_usage(k["id"], now, info["used"], info["total"], info["expire"])

    async def read_traffic(self, rt: RouterRuntime) -> None:
        """The bytes per server since the last read, stored by hour, key and rule."""
        cur = await rt.agent.call("traffic", timeout=30)
        last = self.db.get_setting(f"traffic:{rt.id}")
        self.db.set_setting(f"traffic:{rt.id}", cur)
        if not last or not rt.config:
            return
        same = last.get("since") == cur.get("since")
        index = usage.link_index(self.db.keys(rt.id))
        hour = int(time.time() // 3600 * 3600)
        if "links" in cur:
            # agent 0.3.0+: counted per "<rule>\t<link>" when the bytes went through, so a
            # reorder of the servers doesn't move bytes to another server
            totals, base = cur["links"] or {}, (last.get("links") or {}) if same else {}
            where = {k: tuple(k.split("\t", 1)) for k in totals if "\t" in k}
        else:
            totals, base = cur.get("servers") or {}, (last.get("servers") or {}) if same else {}
            where = podkop.tag_links(rt.config["config"])
        for k, total in totals.items():
            n = total - base.get(k, 0)
            if n < 0:
                n = total
            if n > 0 and k in where:
                rule, link = where[k]
                self.db.add_traffic(rt.id, hour, index.get(usage.norm(link), 0), rule, link, n)

    def usage(self, rid: int) -> dict:
        now = time.time()
        since = now - 15 * usage.DAY
        traffic = self.db.traffic(rid, since)
        keys = [usage.key_usage(k, self.db.key_usage(k["id"], since), [r for r in traffic if r["key_id"] == k["id"]], now)
                for k in self.db.keys(rid)]
        return {"now": now, "keys": keys,
                "servers": usage.servers_today(traffic, {k["id"]: k["label"] for k in keys}, now)}

    async def check_usage(self, rt: RouterRuntime) -> None:
        """Act on keys past their threshold once per day, move rules away from
        or back to keys, and refresh the summary for the menu and Overview."""
        u = self.usage(rt.id)
        now = u["now"]
        for k in u["keys"]:
            if usage.over_threshold(k) and not (k["acted_until"] or 0) > now:
                self.db.update_key(k["id"], acted_until=k["next_reset"])
                self._event(rt, "usage_threshold", {"key": k["label"], "pct": k["pct"], "action": k["action"]})
        avoid = sorted(k["id"] for k in self.db.keys(rt.id) if k["action"] == "avoid" and (k["acted_until"] or 0) > now)
        if avoid != (self.db.get_setting(f"avoid:{rt.id}") or []) and now >= rt.move_retry and not rt.moving:
            # one at a time, in the background: it waits until the router took the change
            rt.moving = True
            task = asyncio.create_task(self._move(rt, avoid, [k["label"] for k in u["keys"] if k["id"] in avoid]))
            self.jobs.add(task)
            task.add_done_callback(self.jobs.discard)
        daily = [k for k in u["keys"] if k["daily"] and k["limit"]]
        rt.usage = {"pct": max((k["pct"] for k in daily), default=None),
                    "alerts": [{k_: k[k_] for k_ in ("id", "label", "used", "limit", "pct", "threshold", "action", "forecast")}
                               for k in daily if usage.over_threshold(k) or (k["forecast"] or {}).get("hits_today")]}
        self.publish({"type": "usage", "router_id": rt.id})
        self.publish({"type": "router", "router": rt.snapshot()})

    async def _move(self, rt: RouterRuntime, avoid: list, labels: list) -> None:
        """Remembered once the move went through; a failed one is tried again in 10 minutes."""
        try:
            if await self._move_rules(rt, labels):
                self.db.set_setting(f"avoid:{rt.id}", avoid)
            else:
                rt.move_retry = time.time() + 600
        finally:
            rt.moving = False

    async def _move_rules(self, rt: RouterRuntime, labels: list) -> bool:
        """Rebuild the probe's rules from its last run without the servers of
        the keys moved away from (or with them again, after the reset). Only
        rules that held those servers (or were changed last time) are touched."""
        status = self.db.last_probe_result(rt.id)
        if not status:
            self._event(rt, "usage_move_failed", {"message": "no probe run to take other servers from"})
            return False
        try:
            cur = await self.config(rt.id)
            new = self.probe_config(rt.id, cur, status)
            avoided = self._avoided_links(rt.id)
            touched = set(self.db.get_setting(f"moved:{rt.id}") or []) | {
                sec for sec, link in podkop.tag_links(cur["config"]).values() if usage.norm(link) in avoided}
            old = podkop.sections_by_name(cur["config"])
            secs = [sec if sec["name"] in touched else old[sec["name"]]
                    for sec in new["sections"] if sec["name"] in old or sec["name"] in touched]
            if all(sec["name"] in old for sec in secs):     # nothing added: keep the rules' order
                order = [sec["name"] for sec in cur["config"]["sections"]]
                secs.sort(key=lambda sec: order.index(sec["name"]))
            new = dict(new, sections=secs)
            if podkop.diff(cur["config"], new):
                note = f"traffic limit of {', '.join(labels)}: rules moved to other keys" if labels else "traffic limit reset"
                vid = (await self.apply(rt.id, cur["fingerprint"], new, note, "usage"))["version"]
                v = await self.wait_version(vid)        # done only when the router took it
                if not v or v["state"] not in OK_JOB_STATES:
                    self._event(rt, "usage_move_failed", {"message": (v or {}).get("message") or "the change did not finish"})
                    return False
            self.db.set_setting(f"moved:{rt.id}", sorted(touched) if avoided else [])
            return True
        except (AgentError, ValueError) as e:
            self._event(rt, "usage_move_failed", {"message": str(e)})
            return False

    async def _scheduler(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = dt.datetime.now()
            for rid in list(self.routers):
                try:
                    await self._maybe_nightly(rid, now)
                except Exception:  # noqa: BLE001 - one router's trouble must not stop the others
                    log.exception("nightly probe of router %s", rid)

    async def _maybe_nightly(self, rid: int, now: dt.datetime) -> None:
        """Starts the router's nightly probe within an hour after its time."""
        if rid not in self.routers:
            return
        ps = self.db.probe_settings(rid)
        sched = (ps.get("schedule") or "").strip()
        if not sched or ps.get("last_run_day") == now.strftime("%Y-%m-%d"):
            return
        try:
            hh, mm = (int(x) for x in sched.split(":"))
        except ValueError:
            return
        # within an hour after the set time, so a manager started at
        # noon doesn't run the nightly probe right away
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if 0 <= (now - due).total_seconds() < 3600:
            try:
                await self.start_probe(rid, "schedule")
            except AgentError as e:
                if e.code in ("unreachable", "timeout"):
                    return          # the router is away: try again in 30 s, within the hour
                self._event(self.routers[rid], "probe_not_started", {"message": e.message})
            self.db.set_probe_settings(rid, last_run_day=now.strftime("%Y-%m-%d"))

    async def _pruner(self) -> None:
        while True:
            try:
                self.db.prune()
            except Exception:  # noqa: BLE001 - e.g. a full disk; try again in an hour
                log.exception("prune")
            await asyncio.sleep(3600)
