"""The web interface: login, a JSON API for the browser, and a live stream
(server-sent events). The pages themselves are static/app.js."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, backup, configs, podkop
from .agent import AgentError, forward_line, install_command, pair, parse_pairing, release_tag
from .db import DB
from .hub import Hub

STATIC = Path(__file__).parent / "static"


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 300_000).hex()
    return f"pbkdf2_sha256${salt}${h}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, _h = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def create_app(db: DB, hub: Hub, public_key: str, password: str, username: str = "admin") -> FastAPI:
    """username and password: the web login, from the environment, not stored."""
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        await hub.start()
        yield
        await hub.stop()

    app = FastAPI(title="podkop-manager", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    secret = db.get_setting("session_secret")
    if not secret:
        secret = secrets.token_hex(32)
        db.set_setting("session_secret", secret)
    password_hash = hash_password(password)
    keys = {"public": public_key}     # replaced when a backup is restored
    restoring = asyncio.Lock()
    # a new password logs everyone out
    secret += hashlib.sha256(f"{username}\n{password}".encode()).hexdigest()
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="podkop_manager",
                       max_age=14 * 86400, same_site="strict", https_only=False)

    @app.exception_handler(AgentError)
    async def _agent_error(_: Request, e: AgentError):
        return JSONResponse({"error": e.as_dict()}, status_code=502 if e.code in ("unreachable", "timeout") else 400)

    def field(body: dict, name: str) -> str:
        if not isinstance(body.get(name), str) or not body[name]:
            raise HTTPException(400, f"{name} is required")
        return body[name]

    def user(request: Request) -> None:
        if not request.session.get("auth"):
            raise HTTPException(401, "log in first")
        # a page from another site can't add this header to its requests
        if request.method not in ("GET", "HEAD") and request.headers.get("x-podkop-manager") != "1":
            raise HTTPException(403, "missing request header")

    def config_of(body: dict) -> dict:
        cfg = body.get("config") if isinstance(body, dict) else None
        if not isinstance(cfg, dict) or not isinstance(cfg.get("sections"), list):
            raise HTTPException(400, "expected {\"config\": {\"sections\": [...]}}")
        return cfg

    def router_or_404(rid: int):
        try:
            return hub.get(rid)
        except KeyError:
            raise HTTPException(404, "no such router") from None

    # --- pages ----------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index(request: Request):
        if not request.session.get("auth"):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(STATIC / "app.html")

    @app.get("/login")
    async def login_page():
        return FileResponse(STATIC / "login.html")

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        name_ok = hmac.compare_digest(str(form.get("user", "")).strip().encode(), username.encode())
        # the slow hash runs off the event loop, so guessing can't stall the live updates
        if await asyncio.to_thread(check_password, str(form.get("password", "")), password_hash) and name_ok:
            request.session["auth"] = True
            return RedirectResponse("/", status_code=303)
        await asyncio.sleep(1)
        return RedirectResponse("/login?bad=1", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # --- API ------------------------------------------------------------------------
    api = Depends(user)

    @app.get("/api/manager", dependencies=[api])
    async def manager_info():
        missing = hub.local_probe_missing()
        return {"version": __version__, "public_key": keys["public"],
                "local_probe": {"ready": not missing, "missing": missing, "engine": hub.engine,
                                "forward_line": "" if missing else forward_line(hub.forward_public())}}

    @app.get("/api/schema", dependencies=[api])
    async def schema():
        return podkop.SCHEMA

    @app.get("/api/routers", dependencies=[api])
    async def routers():
        return [rt.snapshot() for rt in hub.routers.values()]

    @app.post("/api/engine/download", dependencies=[api])
    async def engine_download():
        """podkop-engine for probes run here: downloaded once into the data folder."""
        hub.download_engine()
        return hub.engine

    @app.post("/api/install-command", dependencies=[api])
    async def router_command(body: dict):
        """The command that installs podkop-agent and this manager's key on a
        router; it prints the line Add router takes."""
        allow = [x.strip() for x in str(body.get("allow_clash", "")).replace(",", " ").split() if x.strip()]
        if not all(re.fullmatch(r"[0-9A-Fa-f.:/]+", x) for x in allow):
            raise HTTPException(400, "computers for the Clash API: IP addresses or subnets")
        fwd = hub.forward_public() if body.get("forward") and hub.forward_key.exists() else ""
        return {"command": install_command(release_tag(hub.src), keys["public"], fwd, allow,
                                           body.get("close_clash", True) is not False)}

    @app.post("/api/routers", dependencies=[api])
    async def add_router(body: dict):
        """A router the install command ran on: the line it printed, and the
        address the manager reaches it at if that differs."""
        try:
            p = parse_pairing(str(body.get("pairing", "")))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        host = str(body.get("host") or "").strip() or p["host"]
        try:
            port = int(body.get("port") or p["port"])
        except (TypeError, ValueError):
            port = 0
        if not 0 < port < 65536:
            raise HTTPException(400, "port must be a number from 1 to 65535")
        if any(r["host"] == host and r["port"] == port for r in db.routers()):
            raise HTTPException(400, f"{host} is already added")
        await pair(host, port, p["host_key"], hub.key)
        rid = db.add_router(str(body.get("name") or "").strip() or host, host, port, p["host_key"])
        hub.add_router(db.router(rid))
        # podkop as it is: kept in Configs, and the probe it runs by itself taken into Keys and rules
        task = asyncio.create_task(hub.import_found(rid))
        hub.jobs.add(task)
        task.add_done_callback(hub.jobs.discard)
        return {"id": rid}

    @app.patch("/api/routers/{rid}", dependencies=[api])
    async def rename_router(rid: int, body: dict):
        rt = router_or_404(rid)
        name = str(body.get("name", "")).strip()
        if name:
            db.update_router(rid, name=name)
            rt.row["name"] = name
        return rt.snapshot()

    @app.delete("/api/routers/{rid}", dependencies=[api])
    async def delete_router(rid: int, uninstall: bool = False):
        rt = router_or_404(rid)
        note = ""
        if uninstall:
            try:
                await rt.agent.call("uninstall", timeout=20)
            except AgentError as e:
                note = f"podkop-agent was not removed from the router: {e.message}"
        await hub.remove_router(rid)
        return {"ok": True, "note": note}

    @app.post("/api/routers/{rid}/refresh", dependencies=[api])
    async def refresh(rid: int):
        rt = router_or_404(rid)
        await hub.refresh(rt)
        return rt.snapshot()

    @app.get("/api/routers/{rid}/config", dependencies=[api])
    async def get_config(rid: int):
        rt = router_or_404(rid)
        try:
            return await hub.config(rid)
        except AgentError as e:
            # the router is away: its settings as last read, so the draft can still be seen
            if e.code not in ("unreachable", "timeout") or not rt.config:
                raise
            return dict(rt.config, cached=True)

    @app.post("/api/routers/{rid}/diff", dependencies=[api])
    async def diff(rid: int, body: dict):
        router_or_404(rid)
        cfg = config_of(body)
        return await hub.diff(rid, cfg)

    @app.post("/api/routers/{rid}/validate", dependencies=[api])
    async def validate(rid: int, body: dict):
        router_or_404(rid)
        cfg = config_of(body)
        return {"problems": await hub.validate(rid, cfg)}

    @app.post("/api/routers/{rid}/apply", dependencies=[api])
    async def apply(rid: int, body: dict):
        router_or_404(rid)
        trigger = str(body.get("trigger") or "web")
        if trigger not in ("web", "restore", "probe"):
            raise HTTPException(400, "trigger must be web, restore or probe")
        return await hub.apply(rid, str(body.get("base", "")), config_of(body), str(body.get("note", "")), trigger)

    @app.get("/api/routers/{rid}/versions", dependencies=[api])
    async def versions(rid: int, limit: int = 30, before: int | None = None):
        router_or_404(rid)
        return db.versions(rid, max(1, min(limit, 200)), before)

    # --- the draft ------------------------------------------------------------------
    @app.get("/api/routers/{rid}/draft", dependencies=[api])
    async def get_draft(rid: int):
        rt = router_or_404(rid)
        d = db.draft(rid)
        return dict(d, **(rt.draft or {})) if d else None

    @app.put("/api/routers/{rid}/draft", dependencies=[api])
    async def put_draft(rid: int, body: dict):
        rt = router_or_404(rid)
        cfg = body.get("config")
        if not isinstance(cfg, dict) or not isinstance(cfg.get("sections"), list):
            raise HTTPException(400, "expected {\"config\": {\"sections\": [...]}, \"base\": ...}")
        # the draft the page started from: another tab, or "Load into draft", may have changed it since
        if "rev" in body and body["rev"] != ((db.draft(rid) or {}).get("updated")):
            raise HTTPException(409, "the draft was changed elsewhere")
        db.set_draft(rid, cfg, str(body.get("base", "")))
        hub.update_draft(rt)
        hub.publish({"type": "router", "router": rt.snapshot()})
        return rt.draft

    @app.delete("/api/routers/{rid}/draft", dependencies=[api])
    async def delete_draft(rid: int, discard: bool = False, rev: float | None = None):
        """discard: the user threw the draft away, so the probe rules go back
        too; rev: delete only the draft the page started from."""
        rt = router_or_404(rid)
        if rev is not None and rev != ((db.draft(rid) or {}).get("updated")):
            raise HTTPException(409, "the draft was changed elsewhere")
        if discard:
            hub.restore_rules(rid)
        db.delete_draft(rid)
        hub.update_draft(rt)
        hub.publish({"type": "router", "router": rt.snapshot()})
        return {"ok": True}

    @app.get("/api/versions/{vid}", dependencies=[api])
    async def version(vid: int):
        v = db.version(vid)
        if not v:
            raise HTTPException(404, "no such version")
        return v

    @app.post("/api/routers/{rid}/pin", dependencies=[api])
    async def pin(rid: int, body: dict):
        rt = router_or_404(rid)
        r = await rt.agent.call("pin", field(body, "section"), field(body, "server"))
        rt.wake.set()
        return r

    @app.post("/api/routers/{rid}/delays", dependencies=[api])
    async def delays(rid: int, body: dict):
        rt = router_or_404(rid)
        return await rt.agent.call("delays", field(body, "section"), timeout=40)

    @app.post("/api/routers/{rid}/service", dependencies=[api])
    async def service(rid: int, body: dict):
        rt = router_or_404(rid)
        r = await rt.agent.call("service", field(body, "action"), timeout=90)
        rt.wake.set()
        return r

    @app.get("/api/routers/{rid}/events", dependencies=[api])
    async def events(rid: int, limit: int = 100):
        router_or_404(rid)
        return db.events(rid, max(1, min(limit, 1000)))

    @app.get("/api/routers/{rid}/logs", dependencies=[api])
    async def logs(rid: int, q: str = "", limit: int = 500, source: str = "", level: str = ""):
        """Stored lines, oldest first; source and level take comma separated values."""
        router_or_404(rid)
        split = lambda v: [x for x in v.split(",") if x]  # noqa: E731
        rows = db.logs(rid, q, max(1, min(limit, 5000)), split(source), split(level))
        for r in rows:
            p = podkop.parse_log(r["line"])
            r["time"], r["msg"] = p["time"], p["msg"]
        return rows

    @app.get("/api/routers/{rid}/logs/recent", dependencies=[api])
    async def recent_logs(rid: int, lines: int = 300):
        """The router's own log buffer, read now."""
        rt = router_or_404(rid)
        return [dict(podkop.parse_log(line), line=line)
                for line in await rt.agent.call("logs", str(min(lines, 2000)))]

    # --- probe ------------------------------------------------------------------------
    @app.get("/api/routers/{rid}/probe", dependencies=[api])
    async def probe(rid: int):
        rt = router_or_404(rid)
        st = None
        ps = db.probe_settings(rid)
        rules = db.auto_rules(rid)
        with contextlib.suppress(AgentError):
            st = await hub.probe_status(rt)
        if st:
            picks = hub.auto_picks(rid, st)
            st["rows"] = podkop.probe_rows(st, hub.probe_options(rid), picks)
            st["keys"] = podkop.probe_keys(st.get("log") or [], db.last_probe_keys(rid))
            secs = podkop.sections_by_name(rt.config["config"]) if rt.config else {}
            for r in rules:
                pick = picks[r["section"]]
                r["picked"], r["picked_fallback"] = len(pick["links"]), len(pick["fallback"])
                o = (secs.get(r["section"]) or {}).get("options") or {}
                r["differs"] = bool(pick["links"]) and r["section"] in secs and (
                    _as_list(o.get("urltest_proxy_links")) != pick["links"]
                    or _as_list(o.get("urltest_fallback_links")) != pick["fallback"])
        return {"settings": ps, "status": st, "runs": db.probe_runs(rid), "auto_rules": rules}

    @app.put("/api/routers/{rid}/probe", dependencies=[api])
    async def probe_settings(rid: int, body: dict):
        router_or_404(rid)
        fields = {}
        if "keys" in body:
            if not isinstance(body["keys"], list):
                raise HTTPException(400, "keys must be a list of addresses")
            fields["keys"] = [str(k).strip() for k in body["keys"]
                              if re.fullmatch(r"https?://\S+", str(k).strip())]
        if "options" in body:
            fields["options"] = [str(o) for o in body["options"]]
        if "schedule" in body:
            s = str(body["schedule"]).strip()
            if s and not _hhmm(s):
                raise HTTPException(400, "time must be HH:MM")
            fields["schedule"] = s
        if "auto_apply" in body:
            fields["auto_apply"] = 1 if body["auto_apply"] else 0
        if "fix_fingerprint" in body:
            fields["fix_fingerprint"] = 1 if body["fix_fingerprint"] else 0
        if "test_from" in body:
            if body["test_from"] not in ("router", "manager"):
                raise HTTPException(400, "test_from must be router or manager")
            fields["test_from"] = body["test_from"]
        for k in ("main_section", "youtube_section"):
            if k in body:
                fields[k] = str(body[k]).strip()
        db.set_probe_settings(rid, **fields)
        if "keys" in fields:
            db.sync_sub_keys(rid, fields["keys"])
        return db.probe_settings(rid)

    # --- keys and usage -------------------------------------------------------------------
    @app.get("/api/routers/{rid}/usage", dependencies=[api])
    async def get_usage(rid: int):
        router_or_404(rid)
        return hub.usage(rid)

    @app.post("/api/routers/{rid}/usage/refresh", dependencies=[api])
    async def refresh_usage(rid: int):
        rt = router_or_404(rid)
        await hub.read_usage(rt)
        await hub.check_usage(rt)
        return hub.usage(rid)

    @app.post("/api/routers/{rid}/keys", dependencies=[api])
    async def add_key(rid: int, body: dict):
        """A subscription URL (the probe tests its servers) or a single link
        with a limit of your own."""
        router_or_404(rid)
        link = str(body.get("link", "")).strip()
        if link.startswith(("http://", "https://")):
            keys = db.probe_settings(rid)["keys"]
            if link in keys:
                raise HTTPException(400, "this subscription is already a key")
            db.set_probe_settings(rid, keys=keys + [link])
            db.sync_sub_keys(rid, keys + [link])
            kid = next(k["id"] for k in db.keys(rid) if k["url"] == link)
            if body.get("label"):
                db.update_key(kid, label=str(body["label"]).strip()[:60])
            return {"id": kid}
        if podkop.link_protocol(link) not in podkop.PROTOCOLS:
            raise HTTPException(400, f"a key is a subscription URL (https://) or a link: "
                                     f"{', '.join(p + '://' for p in podkop.PROTOCOLS)}")
        if any(k["url"] == link for k in db.keys(rid)):
            raise HTTPException(400, "this link is already a key")
        kid = db.add_link_key(rid, link, str(body.get("label", "")).strip()[:60], _gb(body.get("limit_gb", 0)))
        return {"id": kid}

    @app.put("/api/keys/{kid}", dependencies=[api])
    async def update_key(kid: int, body: dict):
        k = db.key(kid)
        if not k:
            raise HTTPException(404, "no such key")
        fields = {}
        if "label" in body:
            fields["label"] = str(body["label"]).strip()[:60]
        if "limit_gb" in body:
            fields["limit_gb"] = _gb(body["limit_gb"])
        for name, allowed in (("limit_source", ("provider", "manual")), ("period", ("", "day", "month", "none")),
                              ("action", ("warn", "avoid"))):
            if name in body:
                if body[name] not in allowed:
                    raise HTTPException(400, f"{name} must be one of {', '.join(a or '(auto)' for a in allowed)}")
                fields[name] = body[name]
        if "reset_time" in body:
            if not _hhmm(str(body["reset_time"])):
                raise HTTPException(400, "time must be HH:MM")
            fields["reset_time"] = str(body["reset_time"])
        if "threshold" in body:
            try:
                fields["threshold"] = int(body["threshold"])
            except (TypeError, ValueError):
                fields["threshold"] = 0
            if not 50 <= fields["threshold"] <= 100:
                raise HTTPException(400, "the threshold is 50 to 100%")
        if k["kind"] == "link":
            fields.pop("limit_source", None)
        db.update_key(kid, **fields)
        if rt := hub.routers.get(k["router_id"]):
            await hub.check_usage(rt)
        return db.key(kid)

    @app.delete("/api/keys/{kid}", dependencies=[api])
    async def delete_key(kid: int):
        k = db.key(kid)
        if not k:
            raise HTTPException(404, "no such key")
        if k["kind"] == "sub":
            keys = [u for u in db.probe_settings(k["router_id"])["keys"] if u != k["url"]]
            db.set_probe_settings(k["router_id"], keys=keys)
            db.sync_sub_keys(k["router_id"], keys)
        else:
            db.delete_key(kid)
        return {"ok": True}

    @app.post("/api/routers/{rid}/probe/take-over", dependencies=[api])
    async def probe_take_over(rid: int):
        """podkop-probe ran by itself on the router (cron): the manager runs it
        from now on, at the same time, applying the result like it did."""
        return await hub.take_over_probe(router_or_404(rid))

    @app.post("/api/routers/{rid}/probe/start", dependencies=[api])
    async def probe_start(rid: int):
        router_or_404(rid)
        return {"run": await hub.start_probe(rid, "manual")}

    @app.post("/api/routers/{rid}/probe/check-forward", dependencies=[api])
    async def probe_check_forward(rid: int):
        """The router lets the forwarding key through (probes run here)."""
        router_or_404(rid)
        return {"ok": True, "message": await hub.check_forward(rid)}

    @app.post("/api/routers/{rid}/probe/preview", dependencies=[api])
    async def probe_preview(rid: int):
        rt = router_or_404(rid)
        st = await hub.probe_status(rt)
        cur = await hub.config(rid)
        try:
            new = hub.probe_config(rid, cur, st)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"fingerprint": cur["fingerprint"], "config": new, "diff": podkop.diff(cur["config"], new)}

    # --- rules whose servers the probe picks by country --------------------------------
    @app.get("/api/routers/{rid}/auto-rules", dependencies=[api])
    async def auto_rules(rid: int):
        router_or_404(rid)
        return db.auto_rules(rid)

    @app.get("/api/routers/{rid}/auto-rules/{section}", dependencies=[api])
    async def get_auto_rule(rid: int, section: str):
        router_or_404(rid)
        r = db.auto_rule(rid, section)
        if not r:
            raise HTTPException(404, "this rule takes its servers from links")
        return r

    @app.put("/api/routers/{rid}/auto-rules/{section}", dependencies=[api])
    async def put_auto_rule(rid: int, section: str, body: dict):
        router_or_404(rid)
        if not re.fullmatch(r"[A-Za-z0-9_]+", section):
            raise HTTPException(400, "rule names are letters, digits and _")
        try:
            rule = podkop.check_auto_rule(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        hub.keep_rules(rid)
        db.set_auto_rule(rid, section, rule)
        return db.auto_rule(rid, section)

    @app.delete("/api/routers/{rid}/auto-rules/{section}", dependencies=[api])
    async def delete_auto_rule(rid: int, section: str):
        router_or_404(rid)
        hub.keep_rules(rid)
        db.delete_auto_rule(rid, section)
        return {"ok": True}

    # --- the keys whose servers a URLTest rule takes ---------------------------------------
    @app.get("/api/routers/{rid}/rule-keys", dependencies=[api])
    async def rule_keys(rid: int):
        """The subscriptions with the servers podkop can build, and each rule's keys."""
        router_or_404(rid)
        return {"subs": hub.sub_links(rid), "rules": db.rule_keys(rid)}

    @app.put("/api/routers/{rid}/rule-keys/{section}", dependencies=[api])
    async def put_rule_keys(rid: int, section: str, body: dict):
        router_or_404(rid)
        if not re.fullmatch(r"[A-Za-z0-9_]+", section):
            raise HTTPException(400, "rule names are letters, digits and _")
        ids = body.get("keys")
        subs = {k["id"] for k in db.keys(rid) if k["kind"] == "sub"}
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) and i in subs for i in ids):
            raise HTTPException(400, "keys are ids of this router's subscriptions")
        hub.keep_rules(rid)
        db.set_rule_keys(rid, section, ids)
        return {"keys": db.rule_keys(rid).get(section, [])}

    @app.get("/api/routers/{rid}/auto-rules/{section}/preview", dependencies=[api])
    async def preview_auto_rule(rid: int, section: str):
        """What the rule gets from the last probe run: matched servers,
        fallback and the ones left out with the reason."""
        rt = router_or_404(rid)
        rule = db.auto_rule(rid, section)
        if not rule:
            raise HTTPException(404, "this rule takes its servers from links")
        st = None
        with contextlib.suppress(AgentError):
            st = await hub.probe_status(rt)
        run = {k: (st or {}).get(k) for k in ("state", "started", "finished")}
        if run["state"] == "failed":
            run["log"] = (st.get("log") or [])[-30:]
        if not st or not st.get("rows"):
            last = next((r for r in db.probe_runs(rid) if r["state"] == "done"), None)
            st = (db.probe_result(last["id"]) if last else None) or {}
        pick = hub.auto_picks(rid, st).get(section) or {"matched": [], "fallback_rows": [], "left_out": []}
        ps = db.probe_settings(rid)
        return {"rule": rule, "finished": st.get("finished"), "has_links": any(r.get("link") for r in st.get("rows") or []),
                "run": run, "schedule": ps.get("schedule") or "",
                "keys": len(ps.get("keys") or []),
                "countries": sorted({r["country"] for r in st.get("rows") or [] if len(r.get("country") or "") == 2}),
                "matched": pick["matched"], "fallback": pick["fallback_rows"],
                "left_out": pick["left_out"]}

    # --- saved configs: archive, export, import -------------------------------------------
    def config_or_404(cid: int) -> dict:
        c = db.saved_config(cid)
        if not c:
            raise HTTPException(404, "no such config")
        return c

    def config_row(c: dict) -> dict:
        rules = [s for s in c["config"]["sections"] if s["type"] == "section"]
        return {k: c[k] for k in ("id", "name", "t", "source", "podkop")} | {
            "rules": [s["name"] for s in rules], "from_probe": sorted(c["rules"])}

    def config_name(name, fallback: str) -> str:
        name = str(name or "").strip()[:80]
        return name or fallback

    @app.get("/api/configs", dependencies=[api])
    async def saved_configs():
        return [config_row(c) for c in db.configs()]

    @app.post("/api/configs", dependencies=[api])
    async def save_config(body: dict):
        """Saves a router's running config ("from": "router") or its draft."""
        try:
            rt = router_or_404(int(body.get("router_id") or 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "router_id is a number") from None
        if body.get("from") == "draft":
            d = db.draft(rt.id)
            if not d:
                raise HTTPException(400, "this router has no draft")
            config, source = d["config"], f"{rt.row['name']}, draft"
        else:
            config, source = (await hub.config(rt.id))["config"], rt.row["name"]
        try:
            config = configs.check_config(config)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        names = {s["name"] for s in config["sections"]}
        rules = {r["section"]: podkop.check_auto_rule(r) for r in db.auto_rules(rt.id) if r["section"] in names}
        version = (((rt.status or {}).get("podkop")) or {}).get("version") or ""
        cid = db.add_config(config_name(body.get("name"), f"{source} {time.strftime('%Y-%m-%d %H:%M')}"),
                            config, rules, source, version)
        return config_row(config_or_404(cid))

    @app.post("/api/configs/import", dependencies=[api])
    async def import_config(body: dict):
        """A file's text: our JSON export or podkop's /etc/config/podkop."""
        try:
            f = configs.read(str(body.get("text") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        file = str(body.get("file") or "").strip()[:120]
        cid = db.add_config(config_name(body.get("name") or f["name"], file or "Imported"),
                            f["config"], f["rules"], f"file {file}".strip(), f["podkop"])
        return config_row(config_or_404(cid))

    @app.get("/api/configs/{cid}/file", dependencies=[api])
    async def export_config(cid: int):
        c = config_or_404(cid)
        data = configs.to_file(c["name"], c["config"], c["rules"], c["podkop"])
        fname = re.sub(r"[^A-Za-z0-9_.-]+", "-", c["name"]).strip("-")[:60] or "config"
        return Response(json.dumps(data, ensure_ascii=False, indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="podkop-{fname}.json"'})

    @app.put("/api/configs/{cid}", dependencies=[api])
    async def rename_config(cid: int, body: dict):
        c = config_or_404(cid)
        db.rename_config(cid, config_name(body.get("name"), c["name"]))
        return config_row(config_or_404(cid))

    @app.delete("/api/configs/{cid}", dependencies=[api])
    async def delete_config(cid: int):
        config_or_404(cid)
        db.delete_config(cid)
        return {"ok": True}

    @app.post("/api/routers/{rid}/configs/{cid}/load", dependencies=[api])
    async def load_config(rid: int, cid: int):
        """Puts a saved config into the router's draft, to review and apply."""
        router_or_404(rid)
        hub.load_config(rid, config_or_404(cid))
        return {"ok": True}

    # --- the whole manager: backup and restore ---------------------------------------------
    @app.get("/api/backup", dependencies=[api])
    async def get_backup():
        data = await asyncio.to_thread(backup.make, db, hub.key)
        return Response(data, media_type="application/gzip", headers={
            "Content-Disposition": f'attachment; filename="podkop-manager-{time.strftime("%Y%m%d-%H%M")}.tar.gz"'})

    @app.post("/api/backup", dependencies=[api])
    async def restore_backup(request: Request):
        """Replaces everything with a backup; the routers reconnect with its key."""
        if restoring.locked():
            raise HTTPException(409, "a restore is already running")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > backup.MAX_SIZE:
                raise HTTPException(400, "the file is too big for a backup")
        async with restoring:
            await hub.stop()
            try:
                keys["public"] = await asyncio.to_thread(backup.restore, db, hub.key, bytes(data))
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            finally:
                await hub.start()
        return {"ok": True, "routers": len(hub.routers)}

    # --- live stream --------------------------------------------------------------------
    @app.get("/api/stream", dependencies=[api])
    async def stream(request: Request):
        q = hub.subscribe()

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while not await request.is_disconnected():
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    return app


def _as_list(v) -> list:
    return [] if v in (None, "") else v if isinstance(v, list) else [v]


def _gb(v) -> float:
    try:
        gb = float(v or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "the limit is a number of GB") from None
    if not 0 <= gb <= 100000:
        raise HTTPException(400, "the limit is a number of GB")
    return gb


def _hhmm(s: str) -> bool:
    try:
        h, m = s.split(":")
        return 0 <= int(h) < 24 and 0 <= int(m) < 60 and len(m) == 2
    except ValueError:
        return False
