"""Keys and their traffic: the provider's numbers (the subscription-userinfo
header, read through the router every 10 minutes), the traffic per server
counted on the router, limits, today's use, a forecast and the last 14 days.

Times are unix seconds; days start at the key's reset time in the manager's
local time zone (TZ).
"""

from __future__ import annotations

import base64
import datetime as dt
import re

from .podkop import link_host, link_name

GB = 1024 ** 3
DAY = 86400


def parse_userinfo(header: str) -> dict | None:
    """'upload=1; download=2; total=3; expire=4' -> {used, total, expire}."""
    vals = {k.lower(): int(v) for k, v in re.findall(r"(\w+)=(\d+)", header or "")}
    if "upload" not in vals and "download" not in vals:
        return None
    return {"used": vals.get("upload", 0) + vals.get("download", 0), "total": vals.get("total", 0),
            "expire": vals.get("expire") or None}


def parse_title(header: str) -> str:
    if header.startswith("base64:"):
        try:
            return base64.b64decode(header[7:]).decode().strip()
        except (ValueError, UnicodeDecodeError):
            return ""
    return header.strip()


def norm(link: str) -> str:
    """A link without its #name and TLS fingerprint, to match the same server in
    different lists (the manager may have changed the fingerprint)."""
    return re.sub(r"([?&])fp=[^&#]*&?", r"\1", link.split("#", 1)[0].strip()).rstrip("?&")


def mask(url: str) -> str:
    """sub.example.com/s/…k9Q for a subscription's address: enough to tell keys
    apart, not enough to use one. The address itself stays on the server."""
    m = re.match(r"https?://([^/?#]+)([^#]*)", url or "")
    if not m:
        return (url or "")[:12] + "…"
    host, path = m.group(1).rsplit("@", 1)[-1], m.group(2)
    return f"{host}{path[:path.find('/', 1) + 1 or 1]}…{path[-3:]}" if len(path) > 8 else host + ("/…" if len(path) > 1 else "")


def shown(key: dict) -> str:
    """What the interface shows of a key's address."""
    if key["kind"] == "sub":
        return mask(key["url"])
    return f"{key['url'].split('://', 1)[0]}://…@{link_host(key['url'])}"


def link_index(keys: list) -> dict:
    """norm(link) -> key id, for every link of every key."""
    out = {}
    for k in keys:
        for link in (k["links"] if k["kind"] == "sub" else [k["url"]]):
            out.setdefault(norm(link), k["id"])
    return out


def key_label(key: dict) -> str:
    if key["label"] or key["title"]:
        return key["label"] or key["title"]
    if key["kind"] == "link":
        return link_name(key["url"])
    return key["url"].split("://", 1)[-1].split("/", 1)[0]


def last_reset(now: float, hhmm: str) -> float:
    """The latest moment at or before `now` when the clock showed hh:mm."""
    t = dt.datetime.fromtimestamp(now).astimezone()
    try:
        h, m = (int(x) for x in (hhmm or "00:00").split(":"))
    except ValueError:
        h, m = 0, 0
    r = t.replace(hour=h, minute=m, second=0, microsecond=0)
    if r > t:
        r -= dt.timedelta(days=1)
    return r.timestamp()


def increments(samples: list) -> list:
    """Provider samples (t, used, ...) -> (t, bytes used since the previous
    sample); a counter that went down was reset, so all of it is new."""
    out = []
    for prev, cur in zip(samples, samples[1:]):
        d = cur["used"] - prev["used"]
        out.append((cur["t"], d if d >= 0 else cur["used"]))
    return out


def _drops(samples: list) -> list:
    """Times when the provider's counter went down (a reset)."""
    return [cur["t"] for prev, cur in zip(samples, samples[1:]) if cur["used"] < prev["used"]]


def _value_at(series: list, t: float) -> float:
    """The running total at time t (the last point at or before t)."""
    v = 0.0
    for pt, pv in series:
        if pt > t:
            break
        v = pv
    return v


def key_usage(key: dict, samples: list, traffic: list, now: float) -> dict:
    """Everything the usage page shows for one key.

    samples: the provider's numbers [{t, used, total, expire}] by time;
    traffic: the router's counts for this key [{hour, rule, server, bytes}]."""
    provider = key["limit_source"] == "provider" and bool(samples)
    latest = samples[-1] if samples else None
    drops = _drops(samples)
    if key["period"]:
        period = key["period"]
    elif provider:
        period = "day" if drops and drops[-1] > now - 3 * DAY else "none"
    else:
        period = "day"
    reset = key["reset_time"] or "00:00"
    if provider and period == "day" and drops:
        reset = dt.datetime.fromtimestamp(drops[-1]).astimezone().strftime("%H:00")
    limit = (latest["total"] if provider else int(key["limit_gb"] * GB)) or 0

    # bytes as they came in: from the provider when it reports, else the router
    # (the current hour's bytes count as of now)
    router_inc = sorted((min(r["hour"] + 3600, now), r["bytes"]) for r in traffic)
    inc = increments(samples) if samples else router_inc
    start = last_reset(now, reset)
    if period == "month":
        d = dt.datetime.fromtimestamp(start).astimezone()
        start = d.replace(day=1).timestamp() if d.day != 1 else start
    day_start = last_reset(now, reset)
    daily = period == "day"

    def summed(a: float, b: float) -> int:
        return sum(v for t, v in inc if a < t <= b)

    if provider:
        # a reading from before the reset is yesterday's: nothing used yet until the next one
        used = latest["used"] if not daily or latest["t"] > start else 0
        series = [(start, 0.0)] + [(s["t"], float(s["used"])) for s in samples if s["t"] > start] if daily else []
    else:
        used = summed(start, now) if period != "none" else sum(v for _, v in inc)
        run, series = 0.0, [(start, 0.0)]
        for t, v in inc:
            if start < t <= now:
                run += v
                series.append((min(t, now), run))
    if daily and series:
        series.append((now, float(used)))

    forecast = None
    if daily and limit and series:
        then = max(start, now - 3 * 3600)
        rate = (used - _value_at(series, then)) / max(now - then, 600)
        nxt = start + DAY
        eta = now + (limit - used) / rate if rate > 0 and used < limit else None
        thr = limit * key["threshold"] / 100
        eta_thr = now + (thr - used) / rate if rate > 0 and used < thr else None
        forecast = {"rate": rate, "eta": eta, "eta_threshold": eta_thr,
                    "hits_today": used >= limit or (eta is not None and eta < nxt)}

    days = []
    for n in range(13, -1, -1):
        a = day_start - n * DAY
        if provider and daily:
            v = max([s["used"] for s in samples if a < s["t"] <= a + DAY] or [0])
        else:
            v = summed(a, a + DAY)
        days.append({"t": a, "bytes": v, "hit": bool(daily and limit and v >= limit), "today": n == 0})

    by_rule: dict = {}
    for r in traffic:
        if r["hour"] + 3600 > day_start:
            by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + r["bytes"]

    return {
        "id": key["id"], "kind": key["kind"], "shown": shown(key), "label": key_label(key),
        "title": key["title"], "why": key["why"], "servers": len(key["links"]),
        "limit_source": "provider" if provider else "manual", "settings": {
            k: key[k] for k in ("label", "limit_source", "limit_gb", "period", "reset_time", "action", "threshold")},
        "action": key["action"], "threshold": key["threshold"], "acted_until": key["acted_until"],
        "period": period, "daily": daily, "reset": reset, "start": start, "next_reset": start + DAY if daily else None,
        "limit": limit, "used": used, "pct": round(used / limit * 100, 1) if limit else None,
        "today": summed(day_start, now) if not (provider and daily) else used,
        "expire": latest["expire"] if latest else None, "read": latest["t"] if latest else None,
        "by_rule": sorted(({"rule": k, "bytes": v} for k, v in by_rule.items()), key=lambda x: -x["bytes"]),
        "series": [[round(t), round(v)] for t, v in series], "forecast": forecast, "days": days,
    }


def over_threshold(u: dict) -> bool:
    """A key with a daily limit that passed its action threshold."""
    return bool(u["daily"] and u["limit"] and u["used"] >= u["limit"] * u["threshold"] / 100)


def servers_today(traffic: list, labels: dict, now: float) -> list:
    """The router's counts since local midnight by server: [{server, name, key,
    rule, bytes}], most traffic first. labels: key id -> key label."""
    since = last_reset(now, "00:00")
    agg: dict = {}
    for r in traffic:
        if r["hour"] + 3600 <= since:
            continue
        k = (r["server"], r["key_id"], r["rule"])
        agg[k] = agg.get(k, 0) + r["bytes"]
    return sorted(({"server": s, "name": link_name(s) if "://" in s else s, "key": labels.get(k, ""),
                    "rule": rule, "bytes": b} for (s, k, rule), b in agg.items()), key=lambda x: -x["bytes"])
