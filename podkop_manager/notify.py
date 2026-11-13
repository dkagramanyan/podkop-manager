"""Notifications about what happened on the routers: a Telegram bot and/or a
webhook (JSON POST), set in Settings. Only the events worth waking up for."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("podkop_manager.notify")

DEFAULTS = {"telegram_token": "", "telegram_chat": "", "webhook_url": "", "daily": False}


def _servers(d: dict) -> str:
    n = len(d.get("borrowed") or [])
    return f"borrowed {n} server{'s' if n != 1 else ''}" if n else "nothing to borrow"


# event -> text; an event that is not here is not sent
TEXT = {
    "group_down": lambda d: f"{d.get('section')}: no server answers ({_servers(d)})",
    "group_recovered": lambda d: f"{d.get('section')}: its servers answer again",
    "strict_closed": lambda d: f"{d.get('section')}: kill switch closed, connections are refused",
    "strict_open": lambda d: f"{d.get('section')}: kill switch open again",
    "podkop_down": lambda d: "podkop is down" + (f": {d['failsafe']}" if d.get("failsafe") else ""),
    "podkop_up": lambda d: "podkop is up again",
    "recovered": lambda d: f"watchdog: {d.get('action') or 'podkop restarted'}",
    "recovery_failed": lambda d: f"watchdog could not bring podkop back: {d.get('reason') or ''}",
    "recovery_gave_up": lambda d: "watchdog gave up after 3 tries in an hour",
    "vpn_down": lambda d: "VPN is down" + (f": {d['last']}" if d.get("last") else "") + ("" if d.get("running") else " (OpenVPN is not running)"),
    "vpn_up": lambda d: "VPN is up again",
    "clash_api_silent": lambda d: "sing-box runs but does not answer; podkop is restarted",
    "reverted": lambda d: f"a change was reverted: {d.get('reason') or d.get('message') or ''}",
    "revert_failed": lambda d: "a change failed and could not be reverted",
    "apply_interrupted": lambda d: "an apply was interrupted; the settings before it are back",
    "schedule_failed": lambda d: f"scheduled change #{d.get('version')} not applied: {d.get('message') or ''}",
    "change_not_applied": lambda d: f"change #{d.get('version')} ({d.get('trigger')}) ended as {d.get('state')}: {d.get('message') or ''}",
    "probe_failed": lambda d: f"the {d.get('trigger') or ''} probe failed: {d.get('message') or ''}",
    "probe_not_started": lambda d: f"the nightly probe did not start: {d.get('message') or ''}",
    "probe_apply_failed": lambda d: f"the probe's servers were not applied: {d.get('message') or ''}",
    "heal_probe_failed": lambda d: f"the self-heal probe failed: {d.get('message') or ''}",
    "agent_stuck": lambda d: "podkop-agent's watchdog on the router stands still (no restart of podkop, no kill switch updates); "
                             "restart it: /etc/init.d/podkop-agent restart",
    "agent_ok": lambda d: "podkop-agent's watchdog runs again",
    "router_offline": lambda d: f"the manager cannot reach the router: {d.get('message') or ''}",
    "router_online": lambda d: "the router answers again",
}


def text(router: str, event: str, data: dict) -> str | None:
    f = TEXT.get(event)
    if not f:
        return None
    try:
        return f"{router}: {f(data or {})}".strip().rstrip(":")[:1000]
    except Exception:  # noqa: BLE001 - an event with unexpected fields still gets through
        return f"{router}: {event}"


def configured(s: dict) -> bool:
    return bool((s.get("telegram_token") and s.get("telegram_chat")) or s.get("webhook_url"))


def send(s: dict, message: str, payload: dict | None = None) -> list[str]:
    """Blocking (run it in a thread). Returns the errors, one per channel that failed."""
    errors = []
    if s.get("telegram_token") and s.get("telegram_chat"):
        try:
            body = urllib.parse.urlencode({"chat_id": s["telegram_chat"], "text": message,
                                           "disable_web_page_preview": "true"}).encode()
            with urllib.request.urlopen(f"https://api.telegram.org/bot{s['telegram_token']}/sendMessage", body, timeout=20) as r:
                r.read()
        except urllib.error.HTTPError as e:
            errors.append(f"Telegram: {e.code} {e.read()[:200].decode(errors='replace')}")
        except (OSError, ValueError) as e:
            errors.append(f"Telegram: {e}")
    if s.get("webhook_url"):
        try:
            req = urllib.request.Request(s["webhook_url"], json.dumps({"text": message, **(payload or {})}).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
        except urllib.error.HTTPError as e:
            errors.append(f"webhook: {e.code}")
        except (OSError, ValueError) as e:
            errors.append(f"webhook: {e}")
    return errors
