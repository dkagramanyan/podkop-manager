"""SQLite storage: routers, their events and logs, every applied config
version, unapplied drafts, probe settings and runs, keys with their usage and
traffic, saved configs, and the manager's own settings."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .podkop import parse_log

SCHEMA = """
CREATE TABLE IF NOT EXISTS routers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 22,
  host_key TEXT NOT NULL, added REAL NOT NULL, last_event TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, router_id INTEGER NOT NULL, t REAL NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_router ON events (router_id, id);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY, router_id INTEGER NOT NULL, t REAL NOT NULL, line TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', level TEXT NOT NULL DEFAULT 'info'
);
CREATE INDEX IF NOT EXISTS logs_router ON logs (router_id, id);
CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY, router_id INTEGER NOT NULL, t REAL NOT NULL, job TEXT, state TEXT NOT NULL,
  message TEXT, note TEXT, fingerprint_before TEXT, config TEXT NOT NULL, diff TEXT NOT NULL,
  details TEXT, trigger TEXT NOT NULL DEFAULT 'web'
);
CREATE INDEX IF NOT EXISTS versions_router ON versions (router_id, id);
CREATE TABLE IF NOT EXISTS probe_settings (
  router_id INTEGER PRIMARY KEY, keys TEXT NOT NULL DEFAULT '[]', options TEXT NOT NULL DEFAULT '[]',
  schedule TEXT NOT NULL DEFAULT '', auto_apply INTEGER NOT NULL DEFAULT 0,
  main_section TEXT NOT NULL DEFAULT 'main', youtube_section TEXT NOT NULL DEFAULT 'youtube',
  last_run_day TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS probe_runs (
  id INTEGER PRIMARY KEY, router_id INTEGER NOT NULL, started REAL NOT NULL, finished REAL,
  state TEXT NOT NULL, trigger TEXT NOT NULL, result TEXT, version_id INTEGER
);
CREATE TABLE IF NOT EXISTS drafts (
  router_id INTEGER PRIMARY KEY, config TEXT NOT NULL, base TEXT NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auto_rules (
  router_id INTEGER NOT NULL, section TEXT NOT NULL, countries TEXT NOT NULL DEFAULT '[]',
  protocols TEXT NOT NULL DEFAULT '[]', exit_ips TEXT NOT NULL DEFAULT '[]', name_contains TEXT NOT NULL DEFAULT '',
  top INTEGER NOT NULL DEFAULT 5, fallback INTEGER NOT NULL DEFAULT 2, PRIMARY KEY (router_id, section)
);
CREATE TABLE IF NOT EXISTS keys (
  id INTEGER PRIMARY KEY, router_id INTEGER NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '', limit_source TEXT NOT NULL DEFAULT 'provider', limit_gb REAL NOT NULL DEFAULT 0,
  period TEXT NOT NULL DEFAULT '', reset_time TEXT NOT NULL DEFAULT '00:00', action TEXT NOT NULL DEFAULT 'warn',
  threshold INTEGER NOT NULL DEFAULT 90, acted_until REAL, links TEXT NOT NULL DEFAULT '[]',
  title TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '', UNIQUE (router_id, url)
);
CREATE TABLE IF NOT EXISTS key_usage (
  key_id INTEGER NOT NULL, t REAL NOT NULL, used INTEGER NOT NULL, total INTEGER NOT NULL, expire INTEGER
);
CREATE INDEX IF NOT EXISTS key_usage_key ON key_usage (key_id, t);
CREATE TABLE IF NOT EXISTS traffic (
  router_id INTEGER NOT NULL, hour INTEGER NOT NULL, key_id INTEGER NOT NULL, rule TEXT NOT NULL,
  server TEXT NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY (router_id, hour, key_id, rule, server)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS configs (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, t REAL NOT NULL, source TEXT NOT NULL DEFAULT '',
  podkop TEXT NOT NULL DEFAULT '', config TEXT NOT NULL, rules TEXT NOT NULL DEFAULT '{}'
);
"""

KEY_FIELDS = {"label", "limit_source", "limit_gb", "period", "reset_time", "action", "threshold",
              "acted_until", "links", "title", "why"}

# columns added after the first release: (table, column, declaration)
COLUMNS = [
    ("logs", "source", "TEXT NOT NULL DEFAULT ''"),
    ("logs", "level", "TEXT NOT NULL DEFAULT 'info'"),
    ("versions", "trigger", "TEXT NOT NULL DEFAULT 'web'"),
    ("probe_runs", "keys", "TEXT NOT NULL DEFAULT '[]'"),
    ("auto_rules", "max_ping", "INTEGER NOT NULL DEFAULT 700"),
    ("auto_rules", "min_speed", "REAL NOT NULL DEFAULT 40"),
    ("auto_rules", "youtube", "TEXT NOT NULL DEFAULT ''"),
    ("probe_settings", "fix_fingerprint", "INTEGER NOT NULL DEFAULT 0"),
    ("probe_settings", "test_from", "TEXT NOT NULL DEFAULT 'router'"),
    ("auto_rules", "keys", "TEXT NOT NULL DEFAULT '[]'"),
]


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def migrate(self) -> None:
        """Tables and columns of this version, also for a restored backup."""
        with self.lock:
            self.conn.executescript(SCHEMA)
            added = set()
            for table, col, decl in COLUMNS:
                if col not in [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")]:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    added.add((table, col))
            if ("logs", "source") in added:
                rows = self.conn.execute("SELECT id, line FROM logs").fetchall()
                parsed = [(p["source"], p["level"], r[0]) for r in rows for p in [parse_log(r[1])]]
                self.conn.executemany("UPDATE logs SET source = ?, level = ? WHERE id = ?", parsed)
        # 1.28.0 kept a rule's keys with its probe parameters
        for r in self.q("SELECT router_id, section, keys FROM auto_rules WHERE keys != '[]'"):
            if r["section"] not in self.rule_keys(r["router_id"]):
                self.set_rule_keys(r["router_id"], r["section"], json.loads(r["keys"]))
        self.x("UPDATE auto_rules SET keys = '[]' WHERE keys != '[]'")

    def q(self, sql: str, *args) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, *args) -> dict | None:
        rows = self.q(sql, *args)
        return rows[0] if rows else None

    def x(self, sql: str, *args) -> int:
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    # --- settings ---------------------------------------------------------------
    def get_setting(self, key: str, default=None):
        r = self.one("SELECT value FROM settings WHERE key = ?", key)
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value) -> None:
        self.x("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
               key, json.dumps(value))

    # --- routers ----------------------------------------------------------------
    def routers(self) -> list[dict]:
        return self.q("SELECT * FROM routers ORDER BY id")

    def router(self, rid: int) -> dict | None:
        return self.one("SELECT * FROM routers WHERE id = ?", rid)

    def add_router(self, name: str, host: str, port: int, host_key: str) -> int:
        rid = self.x("INSERT INTO routers (name, host, port, host_key, added) VALUES (?, ?, ?, ?, ?)",
                     name, host, port, host_key, time.time())
        self.x("INSERT OR IGNORE INTO probe_settings (router_id) VALUES (?)", rid)
        return rid

    def update_router(self, rid: int, **fields) -> None:
        for k, v in fields.items():
            assert k in {"name", "host", "port", "host_key", "last_event"}
            self.x(f"UPDATE routers SET {k} = ? WHERE id = ?", v, rid)

    def delete_router(self, rid: int) -> None:
        self.x("DELETE FROM key_usage WHERE key_id IN (SELECT id FROM keys WHERE router_id = ?)", rid)
        for table in ("events", "logs", "versions", "probe_runs", "drafts", "auto_rules", "keys", "traffic"):
            self.x(f"DELETE FROM {table} WHERE router_id = ?", rid)
        self.x("DELETE FROM probe_settings WHERE router_id = ?", rid)
        # the hub's per-router settings, so a router that gets this id later starts clean
        for name in ("traffic", "avoid", "moved", "lists_to_rules", "countries_to_rules", "rules_base", "imported",
                     "rule_keys", "rule_keys_base", "rule_links", "rule_links_base", "keys_synced"):
            self.x("DELETE FROM settings WHERE key = ?", f"{name}:{rid}")
        self.x("DELETE FROM routers WHERE id = ?", rid)

    # --- events and logs ----------------------------------------------------------
    def add_event(self, rid: int, etype: str, data: dict, t: float | None = None) -> int:
        return self.x("INSERT INTO events (router_id, t, type, data) VALUES (?, ?, ?, ?)",
                      rid, t or time.time(), etype, json.dumps(data))

    def events(self, rid: int, limit: int = 100) -> list[dict]:
        rows = self.q("SELECT * FROM events WHERE router_id = ? ORDER BY id DESC LIMIT ?", rid, limit)
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def add_log(self, rid: int, line: str, source: str = "", level: str = "info") -> None:
        self.x("INSERT INTO logs (router_id, t, line, source, level) VALUES (?, ?, ?, ?, ?)",
               rid, time.time(), line, source, level)

    def logs(self, rid: int, q: str = "", limit: int = 500, sources: list[str] | None = None,
             levels: list[str] | None = None) -> list[dict]:
        sql, args = "SELECT * FROM logs WHERE router_id = ?", [rid]
        if q:
            sql += " AND line LIKE ?"
            args.append(f"%{q}%")
        for col, values in (("source", sources), ("level", levels)):
            if values:
                sql += f" AND {col} IN ({', '.join('?' * len(values))})"
                args += values
        rows = self.q(sql + " ORDER BY id DESC LIMIT ?", *args, limit)
        return rows[::-1]

    def prune(self, log_days: int = 14, event_days: int = 180, versions: int = 500, probe_results: int = 30) -> None:
        now = time.time()
        self.x("DELETE FROM logs WHERE t < ?", now - log_days * 86400)
        self.x("DELETE FROM events WHERE t < ?", now - event_days * 86400)
        self.x("DELETE FROM key_usage WHERE t < ?", now - 60 * 86400)
        self.x("DELETE FROM traffic WHERE hour < ?", now - 60 * 86400)
        # each version holds a whole config and each probe run all its rows: keep the newest
        self.x("DELETE FROM versions WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER "
               "(PARTITION BY router_id ORDER BY id DESC) AS n FROM versions) WHERE n > ?)", versions)
        self.x("UPDATE probe_runs SET result = NULL WHERE result IS NOT NULL AND id IN (SELECT id FROM (SELECT id, "
               "ROW_NUMBER() OVER (PARTITION BY router_id ORDER BY id DESC) AS n FROM probe_runs) WHERE n > ?)",
               probe_results)

    # --- config versions ------------------------------------------------------------
    def add_version(self, rid: int, config: dict, diff: list, note: str, base: str, trigger: str = "web") -> int:
        return self.x("INSERT INTO versions (router_id, t, state, note, fingerprint_before, config, diff, trigger) "
                      "VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)",
                      rid, time.time(), note, base, json.dumps(config), json.dumps(diff), trigger)

    def update_version(self, vid: int, **fields) -> None:
        for k, v in fields.items():
            assert k in {"job", "state", "message", "details"}
            self.x(f"UPDATE versions SET {k} = ? WHERE id = ?", json.dumps(v) if k == "details" else v, vid)

    def versions(self, rid: int, limit: int = 100, before: int | None = None) -> list[dict]:
        """Newest first, with from the job's answer: the fingerprint the router
        had after it, how long it took, and why it was undone or rejected."""
        rows = self.q("SELECT id, router_id, t, job, state, message, note, diff, details, trigger FROM versions "
                      "WHERE router_id = ? AND id < ? ORDER BY id DESC LIMIT ?", rid, before or 2**62, limit)
        for r in rows:
            r["diff"] = json.loads(r["diff"])
            d = json.loads(r.pop("details") or "null") or {}
            r["fingerprint_after"] = d.get("fingerprint_after")
            r["reason"] = d.get("reason")
            r["problems"] = d.get("problems")
            r["code"] = d.get("code")
            r["seconds"] = _seconds(d.get("started"), d.get("finished"))
        return rows

    def version(self, vid: int) -> dict | None:
        r = self.one("SELECT * FROM versions WHERE id = ?", vid)
        if r:
            for k in ("config", "diff", "details"):
                r[k] = json.loads(r[k]) if r[k] else None
        return r

    # --- probe ------------------------------------------------------------------------
    def probe_settings(self, rid: int) -> dict:
        r = self.one("SELECT * FROM probe_settings WHERE router_id = ?", rid) or {}
        for k in ("keys", "options"):
            r[k] = json.loads(r.get(k) or "[]")
        return r

    def set_probe_settings(self, rid: int, **fields) -> None:
        self.x("INSERT OR IGNORE INTO probe_settings (router_id) VALUES (?)", rid)
        for k, v in fields.items():
            assert k in {"keys", "options", "schedule", "auto_apply", "main_section", "youtube_section",
                         "last_run_day", "fix_fingerprint", "test_from"}
            self.x(f"UPDATE probe_settings SET {k} = ? WHERE router_id = ?",
                   json.dumps(v) if k in ("keys", "options") else v, rid)

    def add_probe_run(self, rid: int, trigger: str, keys: list | None = None) -> int:
        return self.x("INSERT INTO probe_runs (router_id, started, state, trigger, keys) VALUES (?, ?, 'running', ?, ?)",
                      rid, time.time(), trigger, json.dumps(keys or []))

    def last_probe_keys(self, rid: int) -> list:
        """The keys the newest run was started with."""
        r = self.one("SELECT keys FROM probe_runs WHERE router_id = ? ORDER BY id DESC LIMIT 1", rid)
        return json.loads(r["keys"]) if r else []

    def finish_probe_run(self, run_id: int, state: str, result: dict | None, version_id: int | None = None):
        self.x("UPDATE probe_runs SET finished = ?, state = ?, result = ?, version_id = ? WHERE id = ?",
               time.time(), state, json.dumps(result) if result is not None else None, version_id, run_id)

    def probe_runs(self, rid: int, limit: int = 20) -> list[dict]:
        rows = self.q("SELECT p.id, p.started, p.finished, p.state, p.trigger, p.version_id, v.state AS version_state "
                      "FROM probe_runs p LEFT JOIN versions v ON v.id = p.version_id "
                      "WHERE p.router_id = ? ORDER BY p.id DESC LIMIT ?", rid, limit)
        return rows

    def probe_result(self, run_id: int) -> dict | None:
        r = self.one("SELECT result FROM probe_runs WHERE id = ?", run_id)
        return json.loads(r["result"]) if r and r["result"] else None

    def last_probe_result(self, rid: int) -> dict | None:
        """probe-status of the newest finished run."""
        r = self.one("SELECT result FROM probe_runs WHERE router_id = ? AND state = 'done' AND result IS NOT NULL "
                     "ORDER BY id DESC LIMIT 1", rid)
        return json.loads(r["result"]) if r else None

    # --- keys: subscriptions (the probe's keys) and single links, with usage ---------------
    def keys(self, rid: int) -> list[dict]:
        rows = self.q("SELECT * FROM keys WHERE router_id = ? ORDER BY kind DESC, id", rid)
        for r in rows:
            r["links"] = json.loads(r["links"])
        return rows

    def key(self, kid: int) -> dict | None:
        r = self.one("SELECT * FROM keys WHERE id = ?", kid)
        if r:
            r["links"] = json.loads(r["links"])
        return r

    def sync_sub_keys(self, rid: int, urls: list) -> None:
        """One key row per subscription of the probe; removed ones go."""
        for u in urls:
            self.x("INSERT OR IGNORE INTO keys (router_id, kind, url) VALUES (?, 'sub', ?)", rid, u)
        for r in self.q("SELECT id, url FROM keys WHERE router_id = ? AND kind = 'sub'", rid):
            if r["url"] not in urls:
                self.delete_key(r["id"])

    def add_link_key(self, rid: int, url: str, label: str, limit_gb: float) -> int:
        return self.x("INSERT INTO keys (router_id, kind, url, label, limit_source, limit_gb) "
                      "VALUES (?, 'link', ?, ?, 'manual', ?)", rid, url, label, limit_gb)

    def update_key(self, kid: int, **fields) -> None:
        for k, v in fields.items():
            assert k in KEY_FIELDS
            self.x(f"UPDATE keys SET {k} = ? WHERE id = ?", json.dumps(v) if k == "links" else v, kid)

    def delete_key(self, kid: int) -> None:
        self.x("DELETE FROM key_usage WHERE key_id = ?", kid)
        self.x("DELETE FROM traffic WHERE key_id = ?", kid)
        self.x("DELETE FROM keys WHERE id = ?", kid)

    def add_key_usage(self, kid: int, t: float, used: int, total: int, expire: int | None) -> None:
        self.x("INSERT INTO key_usage (key_id, t, used, total, expire) VALUES (?, ?, ?, ?, ?)",
               kid, t, used, total, expire)

    def key_usage(self, kid: int, since: float) -> list[dict]:
        return self.q("SELECT t, used, total, expire FROM key_usage WHERE key_id = ? AND t >= ? ORDER BY t",
                      kid, since)

    def add_traffic(self, rid: int, hour: int, key_id: int, rule: str, server: str, nbytes: int) -> None:
        self.x("INSERT INTO traffic (router_id, hour, key_id, rule, server, bytes) VALUES (?, ?, ?, ?, ?, ?) "
               "ON CONFLICT (router_id, hour, key_id, rule, server) DO UPDATE SET bytes = bytes + excluded.bytes",
               rid, hour, key_id, rule, server, nbytes)

    def traffic(self, rid: int, since: float) -> list[dict]:
        return self.q("SELECT hour, key_id, rule, server, bytes FROM traffic WHERE router_id = ? AND hour >= ?",
                      rid, since)

    # --- drafts: unapplied changes, one per router ----------------------------------------
    def draft(self, rid: int) -> dict | None:
        r = self.one("SELECT config, base, updated FROM drafts WHERE router_id = ?", rid)
        if r:
            r["config"] = json.loads(r["config"])
        return r

    def set_draft(self, rid: int, config: dict, base: str) -> None:
        self.x("INSERT INTO drafts (router_id, config, base, updated) VALUES (?, ?, ?, ?) "
               "ON CONFLICT (router_id) DO UPDATE SET config = excluded.config, base = excluded.base, "
               "updated = excluded.updated", rid, json.dumps(config), base, time.time())

    def delete_draft(self, rid: int) -> None:
        self.x("DELETE FROM drafts WHERE router_id = ?", rid)

    # --- rules whose servers the probe picks by country ---------------------------------
    def auto_rules(self, rid: int) -> list[dict]:
        rows = self.q("SELECT * FROM auto_rules WHERE router_id = ? ORDER BY section", rid)
        for r in rows:
            for k in ("countries", "protocols", "exit_ips"):
                r[k] = json.loads(r[k])
            r.pop("keys")                     # 1.28.0's, now in rule_keys
            if r["youtube"] == "ru":          # before 0.9.0: "sees Russia"
                r["youtube"] = "RU"
        return rows

    def auto_rule(self, rid: int, section: str) -> dict | None:
        return next((r for r in self.auto_rules(rid) if r["section"] == section), None)

    def set_auto_rule(self, rid: int, section: str, rule: dict) -> None:
        self.x("INSERT INTO auto_rules (router_id, section, countries, protocols, exit_ips, name_contains, top, fallback, "
               "max_ping, min_speed, youtube) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (router_id, section) "
               "DO UPDATE SET countries = excluded.countries, protocols = excluded.protocols, exit_ips = excluded.exit_ips, "
               "name_contains = excluded.name_contains, top = excluded.top, fallback = excluded.fallback, "
               "max_ping = excluded.max_ping, min_speed = excluded.min_speed, youtube = excluded.youtube",
               rid, section, json.dumps(rule["countries"]), json.dumps(rule["protocols"]), json.dumps(rule["exit_ips"]),
               rule["name_contains"], rule["top"], rule["fallback"], rule["max_ping"], rule["min_speed"], rule["youtube"])

    def delete_auto_rule(self, rid: int, section: str) -> None:
        self.x("DELETE FROM auto_rules WHERE router_id = ? AND section = ?", rid, section)

    # --- the keys whose servers a URLTest rule takes: {section: [key ids]} ------------------
    def rule_keys(self, rid: int) -> dict:
        return self.get_setting(f"rule_keys:{rid}") or {}

    def set_rule_keys(self, rid: int, section: str, ids: list) -> None:
        rk = self.rule_keys(rid)
        if ids:
            rk[section] = sorted(set(ids))
        else:
            rk.pop(section, None)
        self.set_setting(f"rule_keys:{rid}", rk)

    # --- your own servers a rule with keys takes too: {section: [links]} ----------------------
    def rule_links(self, rid: int) -> dict:
        return self.get_setting(f"rule_links:{rid}") or {}

    def set_rule_links(self, rid: int, section: str, links: list) -> None:
        rl = self.rule_links(rid)
        if links:
            rl[section] = links
        else:
            rl.pop(section, None)
        self.set_setting(f"rule_links:{rid}", rl)

    # --- saved configs (not tied to a router) ------------------------------------
    def configs(self) -> list[dict]:
        rows = self.q("SELECT * FROM configs ORDER BY id DESC")
        for r in rows:
            r["config"], r["rules"] = json.loads(r["config"]), json.loads(r["rules"])
        return rows

    def saved_config(self, cid: int) -> dict | None:
        return next((r for r in self.configs() if r["id"] == cid), None)

    def add_config(self, name: str, config: dict, rules: dict, source: str, podkop: str = "") -> int:
        return self.x("INSERT INTO configs (name, t, source, podkop, config, rules) VALUES (?, ?, ?, ?, ?, ?)",
                      name, time.time(), source, podkop, json.dumps(config), json.dumps(rules))

    def rename_config(self, cid: int, name: str) -> None:
        self.x("UPDATE configs SET name = ? WHERE id = ?", name, cid)

    def delete_config(self, cid: int) -> None:
        self.x("DELETE FROM configs WHERE id = ?", cid)

    def copy_to(self, path: Path) -> None:
        """A consistent copy of the whole database, for a backup."""
        dst = sqlite3.connect(str(path))
        try:
            with self.lock:
                self.conn.backup(dst)
        finally:
            dst.close()

    def restore_from(self, path: Path) -> None:
        """Replaces everything with the database at path (a backup)."""
        src = sqlite3.connect(str(path))
        try:
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"routers", "versions", "settings"} <= tables:
                raise ValueError("the backup's database is not podkop-manager's")
            with self.lock:
                src.backup(self.conn)
        except sqlite3.DatabaseError as e:
            raise ValueError(f"the backup's database is damaged: {e}") from None
        finally:
            src.close()
        self.migrate()


def _seconds(start: str | None, end: str | None) -> int | None:
    """Seconds between two of the agent's UTC times (2026-10-17T21:03:05Z)."""
    try:
        f = "%Y-%m-%dT%H:%M:%SZ"
        return int((datetime.strptime(end, f) - datetime.strptime(start, f)).total_seconds())
    except (TypeError, ValueError):
        return None
