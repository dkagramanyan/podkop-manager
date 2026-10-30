import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from podkop_manager.agent import AgentError
from podkop_manager.db import DB
from podkop_manager.hub import Hub
from podkop_manager import web
from podkop_manager.web import create_app, hash_password, check_password


class FakeAgent:
    async def call(self, *args, **kw):
        raise AgentError("unreachable", "no router in tests")


class FakeRouter:
    id = 1
    draft = None
    agent = FakeAgent()
    status = {"fingerprint": "fp1", "podkop": {"version": "0.7.24"}}
    row = {"name": "home"}
    local_probe = None

    def snapshot(self):
        return {"id": self.id, "draft": self.draft}


class FakeHub:
    src = Path(__file__).resolve().parents[1]
    key = Path("/nonexistent/id_ed25519")
    forward_key = Path("/nonexistent/id_forward")
    engine = {"state": "", "error": ""}
    auto_picks = Hub.auto_picks
    usage = Hub.usage
    load_config = Hub.load_config
    keep_rules = Hub.keep_rules
    restore_rules = Hub.restore_rules
    probe_config = Hub.probe_config
    probe_status = Hub.probe_status
    _avoided_links = Hub._avoided_links
    sub_links = Hub.sub_links

    def local_probe_missing(self):
        return "sing-box not found"

    async def config(self, rid):
        return {"fingerprint": "fp1", "config": {"sections": [
            {"name": "settings", "type": "settings", "options": {"shutdown_correctly": "1"}},
            {"name": "main", "type": "section", "options": {"connection_type": "proxy", "proxy_config_type": "urltest"}}]}}

    def __init__(self, db=None):
        self.db = db
        self.routers = {1: FakeRouter()} if db else {}
        self.published = []
        self.jobs = set()

    def publish(self, msg):
        self.published.append(msg)

    def add_router(self, row):
        pass

    async def import_found(self, rid):
        pass

    def update_draft(self, rt):
        d = self.db.draft(rt.id)
        rt.draft = {"changes": 1, "base": d["base"], "updated": d["updated"]} if d else None

    async def start(self):
        pass

    async def check_usage(self, rt):
        pass

    async def stop(self):
        pass

    def subscribe(self):
        return asyncio.Queue()

    def unsubscribe(self, q):
        pass

    def get(self, rid):
        return self.routers[rid]


def client(tmp_path, with_router=False):
    db = DB(tmp_path / "m.db")
    if with_router:
        db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA host")
    c = TestClient(create_app(db, FakeHub(db if with_router else None), "ssh-ed25519 AAAA test",
                              "secret-pass"))
    c.db = db
    return c


H = {"X-Podkop-Manager": "1"}


def test_password_hash():
    h = hash_password("x" * 10)
    assert check_password("x" * 10, h) and not check_password("y" * 10, h)


def test_login_required(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/api/schema").status_code == 401
        assert c.get("/", follow_redirects=False).headers["location"] == "/login"
        r = c.post("/login", data={"user": "admin", "password": "wrong"}, follow_redirects=False)
        assert r.headers["location"] == "/login?bad=1"
        r = c.post("/login", data={"user": "root", "password": "secret-pass"}, follow_redirects=False)
        assert r.headers["location"] == "/login?bad=1"


def test_login_and_header_check(tmp_path):
    with client(tmp_path) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        assert c.get("/api/schema").json()["podkop"] == "0.7.24"
        assert c.get("/api/manager").json()["public_key"].startswith("ssh-ed25519")
        assert c.get("/api/routers").json() == []
        # a write without the manager's own header (e.g. from another site) is refused
        assert c.post("/api/routers", json={"host": "x"}).status_code == 403
        assert c.get("/api/routers/5/config").status_code == 404
        # the web interface's files are checked on every load, so an update shows without clearing the cache
        for path in ("/", "/login", "/static/app.js", "/static/app.css"):
            assert c.get(path).headers["cache-control"] == "no-cache", path
        etag = c.get("/static/app.js").headers["etag"]
        assert c.get("/static/app.js", headers={"If-None-Match": etag}).status_code == 304


def test_draft_api(tmp_path):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        assert c.get("/api/routers/1/draft").json() is None
        cfg = {"sections": [{"name": "settings", "type": "settings", "options": {}}]}
        assert c.put("/api/routers/1/draft", json={"config": {"x": 1}}, headers=H).status_code == 400
        assert c.put("/api/routers/1/draft", json={"config": cfg, "base": "fp1"}, headers=H).json()["base"] == "fp1"
        d = c.get("/api/routers/1/draft").json()
        assert d["config"] == cfg and d["changes"] == 1
        assert c.delete("/api/routers/1/draft", headers=H).json() == {"ok": True}
        assert c.get("/api/routers/1/draft").json() is None


def test_logs_filters_and_parsed_fields(tmp_path):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        c.db.add_log(1, "Sat Oct 17 23:18:40 2026 user.notice podkop: [info] Stopping", "podkop", "info")
        c.db.add_log(1, "Sat Oct 17 23:18:48 2026 daemon.err sing-box[1]: WARN[0000] slow", "sing-box", "warn")
        c.db.add_log(1, "Sat Oct 17 23:18:49 2026 daemon.err sing-box[1]: ERROR[0000] dead", "sing-box", "error")
        assert len(c.get("/api/routers/1/logs").json()) == 3
        rows = c.get("/api/routers/1/logs?source=sing-box&level=warn,error").json()
        assert [r["msg"] for r in rows] == ["slow", "dead"] and rows[0]["time"] == "23:18:48"
        assert [r["msg"] for r in c.get("/api/routers/1/logs?source=podkop,agent").json()] == ["Stopping"]


def test_versions_trigger_reason_and_paging(tmp_path):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        db = c.db
        v1 = db.add_version(1, {"sections": []}, [], "first", "fp0")
        db.update_version(v1, state="applied", details={"state": "applied", "fingerprint_after": "fp1",
                                                        "started": "2026-10-17T21:03:05Z",
                                                        "finished": "2026-10-17T21:03:21Z"})
        v2 = db.add_version(1, {"sections": []}, [], "second", "fp1", "restore")
        db.update_version(v2, state="reverted", details={"reason": "podkop did not start"})
        vs = c.get("/api/routers/1/versions").json()
        assert [v["id"] for v in vs] == [v2, v1]
        assert vs[0]["trigger"] == "restore" and vs[0]["reason"] == "podkop did not start"
        assert vs[1]["trigger"] == "web" and vs[1]["fingerprint_after"] == "fp1" and vs[1]["seconds"] == 16
        assert [v["id"] for v in c.get(f"/api/routers/1/versions?before={v2}").json()] == [v1]


def test_add_router_with_the_line_the_command_printed(tmp_path, monkeypatch):
    paired = []

    async def fake_pair(host, port, host_key, key):
        paired.append((host, port, host_key))
        if host == "10.0.0.9":
            raise AgentError("auth", "the router does not accept this manager's key: run the command on it first")
        return {"version": "0.4.0", "protocol": 1}

    monkeypatch.setattr(web, "pair", fake_pair)
    with client(tmp_path) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        cmd = c.post("/api/install-command", json={"allow_clash": "192.168.1.10", "close_clash": True}, headers=H).json()["command"]
        assert "/releases/download/v" in cmd and "--key 'ssh-ed25519 AAAA test'" in cmd
        assert "--allow-clash 192.168.1.10" in cmd and "--forward-key" not in cmd and "--keep-clash-open" not in cmd
        assert c.post("/api/install-command", json={"allow_clash": "1.2.3.4; reboot"}, headers=H).status_code == 400
        assert c.post("/api/routers", json={"pairing": "podkop-router 192.168.1.1"}, headers=H).status_code == 400
        line = "podkop-router 192.168.1.1 22 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKey"
        r = c.post("/api/routers", json={"pairing": line, "host": "10.0.0.9"}, headers=H)
        assert r.status_code == 400 and r.json()["error"]["code"] == "auth"
        assert c.db.routers() == []
        r = c.post("/api/routers", json={"pairing": line, "name": "home"}, headers=H)
        assert r.status_code == 200
        (row,) = c.db.routers()
        assert (row["name"], row["host"], row["port"], row["host_key"]) == (
            "home", "192.168.1.1", 22, "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKey")
        assert c.post("/api/routers", json={"pairing": line}, headers=H).status_code == 400   # already added
    assert paired[-1] == ("192.168.1.1", 22, "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKey")


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE logs (id INTEGER PRIMARY KEY, router_id INTEGER NOT NULL, t REAL NOT NULL,"
                      " line TEXT NOT NULL);"
                      "INSERT INTO logs (router_id, t, line) VALUES"
                      " (1, 1, 'Sat Oct 17 23:18:48 2026 daemon.err sing-box[1]: ERROR[0000] dead');"
                      "CREATE TABLE versions (id INTEGER PRIMARY KEY, router_id INTEGER NOT NULL, t REAL NOT NULL,"
                      " job TEXT, state TEXT NOT NULL, message TEXT, note TEXT, fingerprint_before TEXT,"
                      " config TEXT NOT NULL, diff TEXT NOT NULL, details TEXT);")
    con.close()
    db = DB(path)
    db.add_log(1, "x", "podkop", "warn")
    assert db.logs(1, levels=["warn"])[0]["source"] == "podkop"
    assert db.logs(1, sources=["sing-box"], levels=["error"])[0]["line"].endswith("dead")    # old line, filled in
    assert db.versions(1) == []


def test_auto_rules_api_and_preview_from_last_run(tmp_path):
    from test_podkop import ROWS
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        assert c.put("/api/routers/1/auto-rules/main", json={"youtube": "ru", "max_ping": 500}, headers=H).json()["youtube"] == "RU"
        assert c.put("/api/routers/1/auto-rules/bad-name", json={}, headers=H).status_code == 400
        assert c.put("/api/routers/1/auto-rules/netflix", json={"countries": ["Poland"]}, headers=H).status_code == 400
        r = c.put("/api/routers/1/auto-rules/netflix", json={"countries": ["pl"], "protocols": ["vless"], "top": 2,
                                                             "fallback": 1}, headers=H).json()
        assert r["countries"] == ["PL"] and r["top"] == 2
        assert [x["section"] for x in c.get("/api/routers/1/auto-rules").json()] == ["main", "netflix"]
        run = c.db.add_probe_run(1, "manual")
        c.db.finish_probe_run(run, "done", {"finished": "2026-10-17T21:00:00Z", "rows": ROWS})
        p = c.get("/api/routers/1/auto-rules/netflix/preview").json()
        assert [m["name"] for m in p["matched"]] == ["PL Fast", "PL Warsaw 5"]
        assert [m["name"] for m in p["fallback"]] == ["PL Krakow 1"] and p["has_links"]
        assert p["run"]["state"] is None         # the router does not answer: no run state
        assert c.delete("/api/routers/1/auto-rules/netflix", headers=H).json() == {"ok": True}
        assert c.get("/api/routers/1/auto-rules/netflix").status_code == 404


def test_keys_api(tmp_path):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        c.put("/api/routers/1/probe", json={"keys": ["https://sub.example.com/a", "not a url"]}, headers=H)
        assert [(k["kind"], k["url"]) for k in c.db.keys(1)] == [("sub", "https://sub.example.com/a")]
        assert c.post("/api/routers/1/keys", json={"link": "vmess://x"}, headers=H).status_code == 400
        sub2 = c.post("/api/routers/1/keys", json={"link": "https://sub.example.com/b", "label": "b"}, headers=H).json()["id"]
        assert c.db.probe_settings(1)["keys"] == ["https://sub.example.com/a", "https://sub.example.com/b"]
        assert c.delete(f"/api/keys/{sub2}", headers=H).json() == {"ok": True}
        assert c.db.probe_settings(1)["keys"] == ["https://sub.example.com/a"]
        kid = c.post("/api/routers/1/keys", json={"link": "vless://x@h:1#fra", "limit_gb": 10}, headers=H).json()["id"]
        assert c.post("/api/routers/1/keys", json={"link": "vless://x@h:1#fra"}, headers=H).status_code == 400
        assert c.put(f"/api/keys/{kid}", json={"threshold": 20}, headers=H).status_code == 400
        assert c.put(f"/api/keys/{kid}", json={"reset_time": "25:00"}, headers=H).status_code == 400
        k = c.put(f"/api/keys/{kid}", json={"action": "avoid", "threshold": 80, "limit_source": "provider"}, headers=H).json()
        assert (k["action"], k["threshold"], k["limit_source"]) == ("avoid", 80, "manual")   # a link has no provider
        u = c.get("/api/routers/1/usage").json()
        assert [(x["label"], x["limit"]) for x in u["keys"]] == [("sub.example.com", 0), ("fra", 10 * 1024 ** 3)]
        assert c.delete(f"/api/keys/{kid}", headers=H).json() == {"ok": True}
        c.put("/api/routers/1/probe", json={"keys": []}, headers=H)
        assert c.db.keys(1) == []


def test_configs_save_export_import_load(tmp_path):
    from test_configs import UCI
    from test_podkop import ROWS
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        c.put("/api/routers/1/auto-rules/main", json={"countries": ["PL"], "top": 1, "fallback": 0}, headers=H)
        saved = c.post("/api/configs", json={"router_id": 1, "name": "now"}, headers=H).json()
        assert (saved["name"], saved["source"], saved["podkop"], saved["from_probe"]) == ("now", "home", "0.7.24", ["main"])
        r = c.get(f"/api/configs/{saved['id']}/file")
        assert "podkop-now.json" in r.headers["content-disposition"]
        assert "shutdown_correctly" not in r.text and r.json()["rules"]["main"]["countries"] == ["PL"]
        assert c.post("/api/configs", json={"router_id": 1, "from": "draft"}, headers=H).status_code == 400
        assert c.post("/api/configs", json={"router_id": "abc"}, headers=H).status_code == 400
        assert c.put("/api/routers/1/probe", json={"fix_fingerprint": True}, headers=H).json()["fix_fingerprint"] == 1
        assert c.post("/api/routers/1/diff", json={"config": "x"}, headers=H).status_code == 400
        # a file back in: our JSON, and podkop's own UCI
        again = c.post("/api/configs/import", json={"text": r.text, "file": "podkop-now.json"}, headers=H).json()
        assert (again["name"], again["source"], again["from_probe"]) == ("now", "file podkop-now.json", ["main"])
        uci = c.post("/api/configs/import", json={"text": UCI, "file": "podkop"}, headers=H).json()
        assert (uci["name"], uci["rules"], uci["from_probe"]) == ("podkop", ["main"], [])
        assert c.post("/api/configs/import", json={"text": "nonsense here"}, headers=H).status_code == 400
        # loading puts it into the draft; its rules get the last probe's servers
        c.db.delete_auto_rule(1, "main")
        run = c.db.add_probe_run(1, "manual")
        c.db.finish_probe_run(run, "done", {"finished": "2026-10-17T21:00:00Z", "rows": ROWS})
        assert c.post(f"/api/routers/1/configs/{again['id']}/load", headers=H).json() == {"ok": True}
        assert c.db.auto_rule(1, "main")["countries"] == ["PL"]
        main = c.db.draft(1)["config"]["sections"][1]["options"]
        assert main["urltest_proxy_links"] == [r["link"] for r in ROWS if r["node"] == "PL Fast"]
        c.post(f"/api/routers/1/configs/{uci['id']}/load", headers=H)     # a UCI file has no probe rules
        assert c.db.auto_rule(1, "main") is None
        assert c.put(f"/api/configs/{uci['id']}", json={"name": "lab"}, headers=H).json()["name"] == "lab"
        assert c.delete(f"/api/configs/{uci['id']}", headers=H).json() == {"ok": True}
        assert [x["name"] for x in c.get("/api/configs").json()] == ["now", "now"]


def test_draft_from_another_tab_is_not_overwritten(tmp_path):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        cfg = {"sections": [{"name": "settings", "type": "settings", "options": {"a": "1"}}]}
        rev = c.put("/api/routers/1/draft", json={"config": cfg, "base": "fp1", "rev": None}, headers=H).json()["updated"]
        # tab B saves on top of the same draft
        assert c.put("/api/routers/1/draft", json={"config": cfg, "rev": rev}, headers=H).status_code == 200
        # tab A still holds the old revision: refused, both for a save and for dropping an empty draft
        assert c.put("/api/routers/1/draft", json={"config": cfg, "rev": rev}, headers=H).status_code == 409
        assert c.delete(f"/api/routers/1/draft?rev={rev}", headers=H).status_code == 409
        assert c.put("/api/routers/1/draft", json={"config": cfg, "rev": None}, headers=H).status_code == 409


def test_discard_puts_the_probe_rules_back(tmp_path):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        c.db.set_auto_rule(1, "main", podkop_rule(countries=["DE"]))
        c.put("/api/routers/1/auto-rules/main", json={"countries": ["PL"]}, headers=H)
        c.put("/api/routers/1/auto-rules/netflix", json={}, headers=H)
        c.delete("/api/routers/1/draft", headers=H)                  # a page dropping an empty draft: rules stay
        assert sorted(r["section"] for r in c.db.auto_rules(1)) == ["main", "netflix"]
        c.delete("/api/routers/1/draft?discard=1", headers=H)        # Discard: as before the edits
        assert [(r["section"], r["countries"]) for r in c.db.auto_rules(1)] == [("main", ["DE"])]
        c.delete("/api/routers/1/auto-rules/main", headers=H)       # a new round of edits snapshots again
        c.delete("/api/routers/1/draft?discard=1", headers=H)
        assert [r["section"] for r in c.db.auto_rules(1)] == ["main"]


def podkop_rule(**kw):
    from podkop_manager import podkop
    return podkop.check_auto_rule(kw)


def test_rule_keys_api(tmp_path):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        c.put("/api/routers/1/probe", json={"keys": ["https://sub.example.com/a"]}, headers=H)
        kid = c.db.keys(1)[0]["id"]
        c.db.update_key(kid, links=["vless://a@h:1#A", "vmess://x"])
        assert c.put("/api/routers/1/rule-keys/main", json={"keys": [kid]}, headers=H).json() == {"keys": [kid], "links": []}
        for bad in ([kid + 1], ["a"], [True], "x"):
            assert c.put("/api/routers/1/rule-keys/main", json={"keys": bad}, headers=H).status_code == 400
        r = c.get("/api/routers/1/rule-keys").json()
        assert r["rules"] == {"main": [kid]} and r["subs"][0]["links"] == ["vless://a@h:1#A"]   # only what podkop builds
        # own servers: kept when only keys are sent, each once, only podkop's protocols
        r = c.put("/api/routers/1/rule-keys/main", json={"keys": [kid], "links": [" trojan://m@h:2#M", "trojan://m@h:2#M"]}, headers=H).json()
        assert r == {"keys": [kid], "links": ["trojan://m@h:2#M"]}
        assert c.put("/api/routers/1/rule-keys/main", json={"keys": [kid]}, headers=H).json()["links"] == ["trojan://m@h:2#M"]
        for bad in (["vmess://x"], "trojan://m@h:2", [1]):
            assert c.put("/api/routers/1/rule-keys/main", json={"keys": [kid], "links": bad}, headers=H).status_code == 400
        assert c.get("/api/routers/1/rule-keys").json()["links"] == {"main": ["trojan://m@h:2#M"]}
        assert c.put("/api/routers/1/rule-keys/main", json={"keys": [], "links": []}, headers=H).json() == {"keys": [], "links": []}
        assert c.get("/api/routers/1/rule-keys").json()["rules"] == {}
