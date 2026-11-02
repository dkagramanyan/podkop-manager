"""Connection observations and conservative, reversible recovery decisions.

Log bursts and Kuma notifications are evidence, never votes to evict a server.
Only paired active checks (same targets through direct and proxy paths) may
change eligibility. Identities survive URLTest reordering and display renames.
"""
from __future__ import annotations

import hashlib
import math
import re
import time

DEFAULTS = {"enabled": False, "auto_recover": False, "interval": 60,
            "failures": 2, "cooldown": 600, "recovery_successes": 2}


def identity(link: str) -> str:
    return hashlib.sha256(link.split("#", 1)[0].encode()).hexdigest()


def settings(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("unknown reliability setting")
    out = dict(DEFAULTS, **value)
    for k in ("enabled", "auto_recover"):
        if not isinstance(out[k], bool):
            raise ValueError(f"{k} must be true or false")
    for k, lo, hi in (("interval", 30, 300), ("failures", 2, 10),
                      ("cooldown", 60, 3600), ("recovery_successes", 2, 10)):
        if type(out[k]) is not int or not lo <= out[k] <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}")
    if out["auto_recover"] and not out["enabled"]:
        raise ValueError("enable monitoring before automatic recovery")
    return out


def classify(message: str, code: int = 0) -> str | None:
    s = message.lower()
    if code in (401, 403, 429) or re.search(r"(?:status code|http[/\d.]*|http status)\s*[:=]?\s*(401|403|429)\b", s):
        return "access"
    if "dns:" in s or "could not resolve" in s or "enotfound" in s:
        return "dns"
    if any(x in s for x in ("timeout", "timed out", "deadline exceeded", "connection reset",
                            "connection refused", "connection lost", "stream aborted", "download stopped",
                            "protocol error", "tls:", "100% packet loss")):
        return "transport"
    return None


def log_observation(line: str) -> dict | None:
    kind = classify(line)
    switch = re.search(r"urltest\[([^]]+)\]: switched from outbound (\S+) to (\S+)", line)
    if switch:
        return {"kind": "switch", "scope": switch[1], "message": line[-1000:],
                "previous": switch[2], "server": switch[3]}
    if not kind:
        return None
    tags = re.findall(r"outbound/[^\s\[]+\[([^]]+)\]", line)
    context = re.search(r"\boutbound=([^\s]+)", line)
    dest = re.search(r"\bdestination=([^\s]+)", line)
    return {"kind": kind, "scope": tags[-1] if tags else context[1] if context else "unattributed",
            "destination": dest[1] if dest else "", "message": line[-1000:]}


def selections(config: dict, proxies: dict | None) -> dict:
    groups = {g["name"]: g for g in (proxies or {}).get("groups", [])}
    result = {}
    for s in config.get("sections", []):
        if s.get("options", {}).get("connection_type") != "proxy":
            continue
        tag, seen = s["name"] + "-out", set()
        while tag in groups and tag not in seen:
            seen.add(tag)
            tag = groups[tag].get("now")
        if tag and tag not in groups:
            result[s["name"]] = tag
    return result


def checked_path(rows: list[dict]) -> str:
    """Two independent endpoint successes; access denials are not transport failures."""
    if len({r.get("target") for r in rows}) < 2:
        return "unknown"
    if all(r.get("exit") == 0 and 200 <= r.get("code", 0) < 400 for r in rows):
        return "up"
    if all(r.get("exit") in (5, 6, 7, 18, 28, 35, 52, 55, 56, 60, 92, 97)
           and r.get("code", 0) not in (401, 403, 429) for r in rows):
        return "down"
    return "degraded"


def payload_state(rows: list[dict]) -> str:
    outcome = checked_path(rows)
    if outcome == "up" and not all(v.get("bytes") == 65536 for v in rows):
        return "degraded"
    return outcome


def controls_work(report: dict) -> bool:
    return (checked_path(report.get("direct", [])) == "up" and report.get("router_dns") is True
            and not ((report.get("primary_dns") or {}).get("ok") is False
                     and report.get("provider_dns") is not True))


def advance(previous: dict, report: dict, links: dict, options: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    state = {k: dict(v) for k, v in previous.items()}
    current_tags = {identity(link): tag for tag, link in links.items()}
    for key, item in state.items():
        if key in current_tags:
            item["tag"] = current_tags[key]
    # Unknown DNS, collector failures, a WAN outage or a single blocked test
    # destination must break the consecutive-failure sequence.
    trustworthy = controls_work(report)
    seen = set()
    for tag, rows in report.get("nodes", {}).items():
        if tag not in links:
            continue
        key = identity(links[tag])
        if key in seen:
            continue
        seen.add(key)
        old = state.get(key, {})
        if now - old.get("checked", 0) > max(180, options["interval"] * 3):
            old = dict(old, failures=0, successes=0)
        s = dict(old, tag=tag, checked=now, status=checked_path(rows))
        timings = [v["seconds"] * 1000 for v in rows if v.get("exit") == 0 and isinstance(v.get("seconds"), (int, float))]
        s["response_ms"] = round(sum(timings) / len(timings)) if timings else None
        payload = report.get("payload", {})
        if payload_state(payload.get("diagnostic-direct", [])) == "up":
            body_state = payload_state(payload.get(tag, []))
            if body_state == "down":
                s["status"] = "down"
            s["download_status"] = body_state
        # Once a payload stall is observed, a tiny HEAD-style success cannot
        # clear it. The next check must include a fresh paired payload test.
        elif old.get("download_status") == "down" and s["status"] == "up":
            s["status"] = "unknown"
        s["failures"] = old.get("failures", 0) + 1 if trustworthy and s["status"] == "down" else 0
        s["successes"] = old.get("successes", 0) + 1 if trustworthy and s["status"] == "up" else 0
        recent = [v for v in old.get("recent", []) if now - v[0] < 86400][-119:]
        recent.append([now, int(trustworthy and s["status"] == "down")])
        s.update(recent=recent, samples=len(recent), failed_samples=sum(v[1] for v in recent))
        if s["failures"] >= options["failures"] and not old.get("quarantined"):
            s.update(quarantined=True, until=now + options["cooldown"], reason="repeated paired transport failures")
        if old.get("quarantined") and now >= old.get("until", 0) and s["successes"] >= options["recovery_successes"]:
            s.update(quarantined=False, until=0, reason="cooldown elapsed and recovery checks passed")
        state[key] = s
    # Expire histories of removed nodes and cap persisted state.
    allowed = {identity(v) for v in links.values()}
    return {k: v for k, v in state.items() if k in allowed and now - v.get("checked", 0) < 7 * 86400}


def eligibility(config: dict, links: dict, state: dict, report: dict, now: float) -> list[str]:
    """Exclude only with a freshly verified replacement in the SAME group."""
    if not controls_work(report):
        return []
    banned = []
    for s in config.get("sections", []):
        if s.get("options", {}).get("proxy_config_type") != "urltest":
            continue
        tags = [t for t in links if t.startswith(s["name"] + "-")
                and re.fullmatch(re.escape(s["name"]) + r"-(?:fallback-)?\d+-out", t)]
        healthy = [t for t in tags if state.get(identity(links[t]), {}).get("status") == "up"
                   and not state.get(identity(links[t]), {}).get("quarantined")
                   and now - state.get(identity(links[t]), {}).get("checked", 0) < 120]
        if healthy:
            banned += [t for t in tags if state.get(identity(links[t]), {}).get("quarantined")]
    return sorted(banned)


def rank(row: dict, profile: str) -> tuple:
    def number(k, default):
        try:
            v = float(row.get(k))
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default
    history = row.get("health") or {}
    # Recent confirmed failures take precedence over short synthetic speed tests.
    penalty = history.get("failed_samples", 0) / max(1, history.get("samples", 0))
    if profile == "streaming":
        return penalty, -number("mbit", 0), number("p90", number("median", 1e9))
    if profile == "interactive":
        return penalty, number("p90", number("median", 1e9)), number("median", 1e9)
    return penalty, number("median", 1e9)
