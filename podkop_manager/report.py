"""A support report: what one needs to see to help with a router, with the
secrets taken out. Server addresses become host-1, host-2, ... (the same
server keeps its number through the whole report), ids, keys and passwords
in links and outbounds become "…", subscription addresses are left out.
The rules' own domains and address lists stay: they are what the question is
usually about, so read the file before sending it to anyone."""

from __future__ import annotations

import json
import re
import time

from . import __version__
from .podkop import link_host

LINK_OPTIONS = ("proxy_string", "selector_proxy_links", "urltest_proxy_links", "urltest_fallback_links")
_LINK = re.compile(r"\b(?:vless|vmess|trojan|ss|hysteria2|hy2|tuic|socks5?|https?)://[^\s\"'<>\\]+")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_SECRET_PARAM = re.compile(r"([?&](?:pbk|sid|password|pass|auth|token|key|obfs-password|spx|path|serviceName|host|sni|authority|extra|fm)=)[^&#\s\"]*")
# also inside a JSON value that is itself JSON (podkop's outbound_json), where the quotes come as \"
_Q = r'\\?"'
_SECRET_JSON = re.compile(r'(' + _Q + r'(?:uuid|password|auth_str|private_key|public_key|short_id|pre_shared_key|secret|token|username|user)'
                          + _Q + r'\s*:\s*)(' + _Q + r')(?:[^"\\]|\\[^"])*' + _Q)
_SERVER_JSON = re.compile(_Q + r'(?:server|server_name|host|address)' + _Q + r'\s*:\s*' + _Q + r'([^"\\]+)' + _Q)


class Scrub:
    def __init__(self, hide: list | None = None):
        self.hosts: dict[str, str] = {}
        self.hide = sorted({h for h in hide or [] if h}, key=len, reverse=True)   # e.g. subscription addresses

    def host(self, h: str) -> str:
        h = h.lower()
        return self.hosts.setdefault(h, f"host-{len(self.hosts) + 1}") if h else ""

    def learn(self, text: str) -> None:
        """Remember every server named in links and outbounds of this text."""
        for m in _LINK.finditer(text):
            if not m.group(0).startswith("http"):
                self.host(link_host(m.group(0)))
        for m in _SERVER_JSON.finditer(text):
            self.host(m.group(1))

    def link(self, link: str) -> str:
        """vless://…@host-3:443?type=grpc&security=reality&pbk=…"""
        proto, rest = link.split("://", 1)
        body = rest.partition("#")[0]         # the name goes too: providers put the server's address in it
        host = link_host(link)
        tail = body.rsplit("@", 1)[-1]
        port = ""
        if host and (m := re.match(r"\[?[^\]/?]*\]?:(\d+)", tail)):
            port = ":" + m.group(1)
        query = "?" + tail.split("?", 1)[1] if "?" in tail else ""
        return f"{proto}://…@{self.host(host) or '…'}{port}{_SECRET_PARAM.sub(r'\1…', query)}"

    def text(self, text: str) -> str:
        for x in self.hide:
            text = text.replace(x, "subscription …")
        self.learn(text)
        text = _LINK.sub(lambda m: m.group(0) if m.group(0).startswith("http") else self.link(m.group(0)), text)
        text = _SECRET_JSON.sub(lambda m: f"{m.group(1)}{m.group(2)}…{m.group(2)}", text)
        text = _UUID.sub("…", text)
        # longest first, so that a.b.example is not cut by b.example
        for h in sorted(self.hosts, key=len, reverse=True):
            text = re.sub(r"(?<![A-Za-z0-9.-])" + re.escape(h) + r"(?![A-Za-z0-9-]|\.[A-Za-z0-9])", self.hosts[h], text, flags=re.I)
        return text


def build(router: dict, status: dict | None, config: dict | None, events: list, versions: list, logs: list,
          extra: dict | None = None, now: float | None = None, hide: list | None = None) -> str:
    s = Scrub(hide)
    stamp = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) + "Z" if t else "-"
    # the servers of the settings first: the log names them by address alone
    s.learn(json.dumps(config or {}, ensure_ascii=False))
    out = [f"podkop-manager support report, {stamp(now or time.time())}",
           f"manager {__version__}, router \"{router.get('name', '')}\"",
           "Secrets are removed: servers are host-N, ids, keys and passwords are …, subscription addresses are left out.",
           "The rules' own domains and addresses are kept. Read it before you send it.", ""]

    def part(title: str, body: str) -> None:
        out.extend([f"=== {title} ===", s.text(body), ""])

    st = dict(status or {})
    proxies = st.pop("proxies", None)
    part("Router", json.dumps(st, ensure_ascii=False, indent=1) if st else "no status: the router did not answer")
    if isinstance(proxies, dict):
        delay = {x.get("name"): x.get("delay") for x in proxies.get("servers") or []}
        lines = []
        for g in proxies.get("groups") or []:
            members = [m for m in g.get("all") or [] if m in delay]
            lines.append(f"{g.get('name')}: {g.get('type')}, now {g.get('now')}"
                         + (f", {sum(1 for m in members if delay[m])} of {len(members)} servers answered" if members else ""))
        part("Server groups", "\n".join(lines) or "none")
    for k, v in (extra or {}).items():
        part(k, json.dumps(v, ensure_ascii=False, indent=1) if not isinstance(v, str) else v)
    part("podkop settings", json.dumps(config, ensure_ascii=False, indent=1) if config else "not read")
    part("Last changes", "\n".join(
        f"#{v['id']} {stamp(v['t'])} {v.get('trigger') or ''} {v.get('state') or ''} {v.get('message') or ''} {v.get('note') or ''}".rstrip()
        for v in versions) or "none")
    part("Events", "\n".join(f"{stamp(e['t'])} {e['type']} {json.dumps(e.get('data') or {}, ensure_ascii=False)}" for e in events) or "none")
    part("Router log", "\n".join(logs) or "not read")
    return "\n".join(out)
