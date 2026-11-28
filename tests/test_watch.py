"""1.60.0: watched servers (agent 0.30.0, manager 0.47.0): the router tests
each one every minute and tells when one stops answering and when it is back."""
import hashlib
import json
import subprocess
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "router/podkop-agent"
LINK = "vless://u@de1.example:443?type=tcp#DE 1"
MD5 = hashlib.md5(LINK.encode()).hexdigest()
CFG = {"sections": [{"name": "settings", "type": "settings", "options": {}},
                    {"name": "main", "type": "section", "options": {"connection_type": "proxy", "proxy_config_type": "urltest",
                                                                   "urltest_proxy_links": ["vless://x@other:1#X", LINK]}}]}


def _code():
    return subprocess.run(["sed", "-n", "/^# --- watched servers: an alert/,/^# --- commands/p", str(AGENT)],
                          capture_output=True, text=True, check=True).stdout


STUBS = r'''
RUN="$1"; ETC="$1/etc"; mkdir -p "$ETC"; TAB=$(printf '\t')
reply() { printf '%s\n' "$1"; exit 0; }
fail() { echo "$1: $2" >&2; exit 1; }
event() { echo "$1 $2" >> "$RUN/events"; }
link_md5() { printf '%s' "$1" | md5sum | cut -c 1-32; }
seen_fp() { echo fp1; }
seen_json() { cat "$RUN/cfg.json"; }
proxies() { echo '{"proxies": {"main-2-out": {}, "main-1-out": {}}}'; }
clash_get() { if [ -f "$RUN/up" ]; then echo '{"delay": 120}'; else echo '{"message": "timeout"}'; fi; }
heal_wan() { [ ! -f "$RUN/wan-down" ]; }
'''


def _sh(tmp_path, body, stdin=""):
    return subprocess.run(["sh", "-c", STUBS + _code() + body, "sh", str(tmp_path)], input=stdin,
                          capture_output=True, text=True, timeout=30)


def _events(tmp_path):
    ev = tmp_path / "events"
    return [line.split(" ", 1) for line in ev.read_text().splitlines()] if ev.exists() else []


def test_set_checks_and_keeps_the_list(tmp_path):
    (tmp_path / "cfg.json").write_text(json.dumps(CFG))
    p = _sh(tmp_path, "\ncmd_server_watch set", json.dumps({"servers": [{"section": "main", "md5": MD5, "name": "DE 1"}]}))
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "etc/watched").read_text() == f"main\t{MD5}\tDE 1\n"
    assert json.loads(p.stdout) == [{"section": "main", "md5": MD5, "name": "DE 1", "state": "new"}]
    bad = _sh(tmp_path, "\ncmd_server_watch set", json.dumps({"servers": [{"section": "main;rm", "md5": MD5}]}))
    assert "bad_request" in bad.stderr
    _sh(tmp_path, "\ncmd_server_watch set", json.dumps({"servers": []}))
    assert not (tmp_path / "etc/watched").exists()


def test_a_watched_server_is_down_after_3_failed_minutes_and_up_at_its_first_answer(tmp_path):
    (tmp_path / "cfg.json").write_text(json.dumps(CFG))
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/watched").write_text(f"main\t{MD5}\tDE 1\n")
    rounds = "\nfor i in 1 2 3 4; do swatch_round; done"          # one test every 4 daemon rounds
    for _ in range(2):
        assert _sh(tmp_path, rounds).returncode == 0
    assert _events(tmp_path) == []
    p = _sh(tmp_path, rounds + "\nswatch_status")
    assert p.returncode == 0, p.stderr
    ev = _events(tmp_path)
    assert [e[0] for e in ev] == ["server_down"] and json.loads(ev[0][1]) == {"section": "main", "name": "DE 1", "server": "main-2-out"}
    assert json.loads(p.stdout)[0]["state"] == "down"
    assert _sh(tmp_path, rounds).returncode == 0 and len(_events(tmp_path)) == 1      # told once
    (tmp_path / "up").write_text("")
    assert _sh(tmp_path, rounds).returncode == 0
    ev = _events(tmp_path)
    assert ev[-1][0] == "server_up" and json.loads(ev[-1][1])["section"] == "main"


def test_no_alert_while_the_providers_line_is_down_and_gone_when_the_link_left_the_rule(tmp_path):
    (tmp_path / "cfg.json").write_text(json.dumps(CFG))
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/watched").write_text(f"main\t{MD5}\tDE 1\n")
    (tmp_path / "wan-down").write_text("")
    for _ in range(4):
        assert _sh(tmp_path, "\nfor i in 1 2 3 4; do swatch_round; done").returncode == 0
    assert _events(tmp_path) == []
    cfg = json.loads(json.dumps(CFG))
    cfg["sections"][1]["options"]["urltest_proxy_links"] = ["vless://x@other:1#X"]
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    (tmp_path / "etc/watched").touch()
    import os
    import time
    later = time.time() + 5
    os.utime(tmp_path / "etc/watched", (later, later))           # the tags are looked up again
    p = _sh(tmp_path, "\nfor i in 1 2 3 4; do swatch_round; done\nswatch_status")
    assert json.loads(p.stdout)[0]["state"] == "gone"


# --- the manager's side -------------------------------------------------------------------

import asyncio  # noqa: E402

from podkop_manager import notify  # noqa: E402
from test_web import H, FakeHub, FakeRouter, client  # noqa: E402


class WatchAgent:
    def __init__(self):
        self.servers = [{"section": "yt", "md5": "f" * 32, "name": "kept", "state": "up"}]

    async def call(self, name, *args, payload=None, timeout=60):
        assert name == "server-watch"
        if args[0] == "set":
            self.servers = [dict(x, state="new") for x in payload["servers"]]
        return self.servers


def test_watch_one_server_keeps_the_routers_others_and_shows_its_link(tmp_path, monkeypatch):
    with client(tmp_path, with_router=True) as c:
        c.post("/login", data={"user": "admin", "password": "secret-pass"})
        rt = FakeRouter()
        rt.agent, rt.status = WatchAgent(), {"agent": {"capabilities": ["server-watch"]}}
        rt.config = {"config": CFG}
        monkeypatch.setattr(FakeHub, "get", lambda self, rid: rt)
        r = c.put("/api/routers/1/watched", headers=H, json={"add": [{"section": "main", "link": LINK}]})
        assert r.status_code == 200, r.text
        got = {x["md5"]: x for x in r.json()}
        assert set(got) == {"f" * 32, MD5} and got[MD5]["name"] == "DE 1" and got[MD5]["link"] == LINK
        r = c.put("/api/routers/1/watched", headers=H, json={"remove": [{"section": "main", "link": LINK}]})
        assert [x["md5"] for x in r.json()] == ["f" * 32]
        assert c.put("/api/routers/1/watched", headers=H, json={"add": [{"section": "a b", "link": LINK}]}).status_code == 400


def test_alerts_switched_off_quiet_hours_and_late_ones(tmp_path, monkeypatch):
    import datetime as dt
    assert notify.quiet_now({"quiet": "23:00-08:00"}, dt.datetime(2026, 1, 1, 2, 0))
    assert not notify.quiet_now({"quiet": "23:00-08:00"}, dt.datetime(2026, 1, 1, 12, 0))
    assert notify.text("home", "server_down", {"section": "yt", "name": "DE 1"}).startswith("home: yt: watched server DE 1 does not answer")
    from test_heal import _hub, _cfg
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    db.set_setting("notify", {"telegram_token": "T", "telegram_chat": "1", "webhook_url": "http://hook/x", "off": ["group_down"]})
    sent, down = [], {"telegram": True}

    def send(s, message, payload=None, only=None, silent=False):
        want = notify.channels(s) if only is None else notify.channels(s) & only
        sent.append((message, sorted(want)))
        return {c: f"{c}: down" for c in want if down.get(c)}

    monkeypatch.setattr(notify, "send", send)

    async def no_wait(_):
        return None

    async def go():
        monkeypatch.setattr(asyncio, "sleep", no_wait)
        hub._on_message(rt, {"type": "group_down", "id": "b:1", "t": "now", "section": "main"})       # switched off
        hub._on_message(rt, {"type": "server_down", "id": "b:2", "t": "now", "section": "main", "name": "DE 1"})
        await asyncio.gather(*hub.jobs)
    asyncio.run(go())
    # the webhook got it once; only Telegram, which failed, was tried again
    assert [w for _, w in sent] == [["telegram", "webhook"], ["telegram"], ["telegram"], ["telegram"]]
    assert rt.alert["type"] == "server_down"
    q = db.get_setting("notify_queue")
    assert len(q) == 1 and q[0]["channels"] == ["telegram"]
    down.clear(); sent.clear()
    asyncio.run(hub.flush_notifications())
    assert sent[0][0].startswith("(late, from ") and sent[0][1] == ["telegram"] and db.get_setting("notify_queue") is None


def test_a_watched_server_without_a_name_is_tested_not_gone(tmp_path):
    # 0.30: with an empty name, read with IFS=tab moved the tag into the name: always "gone"
    (tmp_path / "cfg.json").write_text(json.dumps(CFG))
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/watched").write_text(f"main\t{MD5}\t\n")
    (tmp_path / "up").write_text("")
    p = _sh(tmp_path, "\nfor i in 1 2 3 4; do swatch_round; done\nswatch_status")
    assert p.returncode == 0, p.stderr
    st = json.loads(p.stdout)[0]
    assert st["state"] == "up" and st["server"] == "main-2-out" and st["name"] == ""
