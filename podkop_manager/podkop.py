"""podkop's settings: the option schema of podkop 0.7.24 (from its LuCI app),
the diff between two configs, putting the probe's picks into a config,
reading podkop-probe's results and the router's log lines.

A config is what podkop-agent's config-get returns:
{"sections": [{"name", "type", "anonymous", "options": {opt: str | [str]}}]}
"""

from __future__ import annotations

import copy
import csv
import io
import ipaddress
import re
import time
from collections import OrderedDict
from urllib.parse import unquote

from . import reliability

# kind: select (one of values), combo (free text with suggestions), text,
# number, flag ("0"/"1"), list (several values), textarea, devices / device
# (network devices of the router), interfaces (logical interfaces), community
# (podkop's community lists), section (one of the proxy/vpn sections).
# depends: any of the conditions must hold; a condition is {option: value}.

SECTION_OPTIONS = [
    {"name": "connection_type", "label": "Connection type", "kind": "select",
     "values": ["proxy", "vpn", "block", "exclusion"]},
    {"name": "proxy_config_type", "label": "Configuration type", "kind": "select",
     "values": ["url", "selector", "urltest", "outbound"], "default": "url",
     "depends": [{"connection_type": "proxy"}]},
    {"name": "proxy_string", "label": "Proxy link", "kind": "textarea",
     "depends": [{"proxy_config_type": "url"}]},
    {"name": "outbound_json", "label": "Outbound (sing-box JSON)", "kind": "textarea",
     "depends": [{"proxy_config_type": "outbound"}]},
    {"name": "selector_proxy_links", "label": "Selector links", "kind": "list",
     "depends": [{"proxy_config_type": "selector"}]},
    {"name": "urltest_proxy_links", "label": "URLTest links", "kind": "list",
     "depends": [{"proxy_config_type": "urltest"}]},
    {"name": "urltest_fallback_links", "label": "Fallback links", "kind": "list",
     "help": "Used only when all URLTest links fail",
     "depends": [{"proxy_config_type": "urltest"}]},
    {"name": "urltest_check_interval", "label": "URLTest check interval", "kind": "select",
     "values": ["30s", "1m", "3m", "5m"], "default": "3m", "depends": [{"proxy_config_type": "urltest"}]},
    {"name": "urltest_tolerance", "label": "URLTest tolerance, ms", "kind": "number", "default": "50",
     "depends": [{"proxy_config_type": "urltest"}]},
    {"name": "urltest_testing_url", "label": "URLTest testing URL", "kind": "combo",
     "values": ["https://www.gstatic.com/generate_204", "https://cp.cloudflare.com/generate_204",
                "https://captive.apple.com", "https://connectivity-check.ubuntu.com",
                "https://www.youtube.com/generate_204"],
     "default": "https://www.gstatic.com/generate_204", "depends": [{"proxy_config_type": "urltest"}]},
    {"name": "urltest_download_check", "label": "Download check", "kind": "select",
     "values": ["default", "off", "custom"], "default": "default", "depends": [{"proxy_config_type": "urltest"}]},
    {"name": "urltest_download_url", "label": "Download check URL", "kind": "combo",
     "values": ["https://speed.cloudflare.com/__down?bytes=65536",
                "https://cachefly.cachefly.net/1mb.test"],
     "default": "https://speed.cloudflare.com/__down?bytes=65536",
     "depends": [{"proxy_config_type": "urltest", "urltest_download_check": "custom"}]},
    {"name": "enable_udp_over_tcp", "label": "UDP over TCP", "kind": "flag", "default": "0",
     "depends": [{"connection_type": "proxy"}]},
    {"name": "interface", "label": "VPN interface", "kind": "device", "depends": [{"connection_type": "vpn"}]},
    {"name": "domain_resolver_enabled", "label": "Domain resolver", "kind": "flag", "default": "0",
     "depends": [{"connection_type": "vpn"}]},
    {"name": "domain_resolver_dns_type", "label": "Resolver DNS protocol", "kind": "select",
     "values": ["doh", "dot", "udp"], "default": "udp", "depends": [{"domain_resolver_enabled": "1"}]},
    {"name": "domain_resolver_dns_server", "label": "Resolver DNS server", "kind": "combo",
     "values": ["1.1.1.1", "8.8.8.8", "9.9.9.9", "dns.adguard-dns.com"], "default": "8.8.8.8",
     "depends": [{"domain_resolver_enabled": "1"}]},
    {"name": "community_lists", "label": "Community lists", "kind": "community"},
    {"name": "user_domain_list_type", "label": "User domains", "kind": "select",
     "values": ["disabled", "dynamic", "text"], "default": "disabled"},
    {"name": "user_domains", "label": "User domains", "kind": "list",
     "depends": [{"user_domain_list_type": "dynamic"}]},
    {"name": "user_domains_text", "label": "User domains (text)", "kind": "textarea",
     "depends": [{"user_domain_list_type": "text"}]},
    {"name": "user_subnet_list_type", "label": "User subnets", "kind": "select",
     "values": ["disabled", "dynamic", "text"], "default": "disabled"},
    {"name": "user_subnets", "label": "User subnets", "kind": "list",
     "depends": [{"user_subnet_list_type": "dynamic"}]},
    {"name": "user_subnets_text", "label": "User subnets (text)", "kind": "textarea",
     "depends": [{"user_subnet_list_type": "text"}]},
    {"name": "local_domain_lists", "label": "Local domain lists (files)", "kind": "list"},
    {"name": "local_subnet_lists", "label": "Local subnet lists (files)", "kind": "list"},
    {"name": "remote_domain_lists", "label": "Remote domain lists (URLs)", "kind": "list"},
    {"name": "remote_subnet_lists", "label": "Remote subnet lists (URLs)", "kind": "list"},
    {"name": "fully_routed_ips", "label": "Fully routed IPs", "kind": "list",
     "depends": [{"connection_type": "proxy"}, {"connection_type": "vpn"}]},
    {"name": "mixed_proxy_enabled", "label": "Mixed proxy", "kind": "flag", "default": "0",
     "depends": [{"connection_type": "proxy"}, {"connection_type": "vpn"}]},
    {"name": "mixed_proxy_port", "label": "Mixed proxy port", "kind": "number",
     "depends": [{"mixed_proxy_enabled": "1"}]},
    {"name": "resolve_real_ip_for_routing", "label": "Resolve real IP for routing", "kind": "flag",
     "default": "0", "depends": [{"connection_type": "proxy"}, {"connection_type": "vpn"}]},
]

SETTINGS_OPTIONS = [
    {"name": "dns_type", "label": "DNS protocol", "kind": "select", "values": ["doh", "dot", "udp"],
     "default": "udp"},
    {"name": "dns_server", "label": "DNS server", "kind": "combo",
     "values": ["1.1.1.1", "8.8.8.8", "9.9.9.9", "dns.adguard-dns.com", "unfiltered.adguard-dns.com",
                "family.adguard-dns.com"], "default": "8.8.8.8"},
    {"name": "bootstrap_dns_server", "label": "Bootstrap DNS server", "kind": "combo",
     "values": ["77.88.8.8", "77.88.8.1", "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "9.9.9.11"],
     "default": "77.88.8.8"},
    {"name": "dns_rewrite_ttl", "label": "DNS rewrite TTL", "kind": "number", "default": "60"},
    {"name": "source_network_interfaces", "label": "Source interfaces", "kind": "devices",
     "default": ["br-lan"]},
    {"name": "enable_output_network_interface", "label": "Output interface", "kind": "flag", "default": "0"},
    {"name": "output_network_interface", "label": "Output interface device", "kind": "device",
     "depends": [{"enable_output_network_interface": "1"}]},
    {"name": "enable_badwan_interface_monitoring", "label": "Bad WAN monitoring", "kind": "flag",
     "default": "0"},
    {"name": "badwan_monitored_interfaces", "label": "Monitored interfaces", "kind": "interfaces",
     "depends": [{"enable_badwan_interface_monitoring": "1"}]},
    {"name": "badwan_reload_delay", "label": "Monitoring delay, ms", "kind": "number", "default": "2000",
     "depends": [{"enable_badwan_interface_monitoring": "1"}]},
    {"name": "enable_yacd", "label": "YACD dashboard", "kind": "flag", "default": "0"},
    {"name": "enable_yacd_wan_access", "label": "YACD from WAN", "kind": "flag", "default": "0",
     "depends": [{"enable_yacd": "1"}]},
    {"name": "yacd_secret_key", "label": "YACD secret", "kind": "text",
     "depends": [{"enable_yacd_wan_access": "1"}]},
    {"name": "enable_utls_fallback", "label": "uTLS fallback", "kind": "flag", "default": "0"},
    {"name": "disable_quic", "label": "Disable QUIC", "kind": "flag", "default": "0"},
    {"name": "update_interval", "label": "List update interval", "kind": "select",
     "values": ["1h", "3h", "12h", "1d", "3d"], "default": "1d"},
    {"name": "download_lists_via_proxy", "label": "Download lists via proxy", "kind": "flag", "default": "0"},
    {"name": "download_lists_via_proxy_section", "label": "Via section", "kind": "section",
     "depends": [{"download_lists_via_proxy": "1"}]},
    {"name": "dont_touch_dhcp", "label": "Don't touch DHCP", "kind": "flag", "default": "0"},
    {"name": "config_path", "label": "sing-box config file", "kind": "select",
     "values": ["/etc/sing-box/config.json", "/tmp/sing-box/config.json"],
     "default": "/etc/sing-box/config.json"},
    {"name": "cache_path", "label": "sing-box cache file", "kind": "text", "default": "/tmp/sing-box/cache.db"},
    {"name": "log_level", "label": "Log level", "kind": "select",
     "values": ["trace", "debug", "info", "warn", "error", "fatal", "panic"], "default": "warn"},
    {"name": "exclude_ntp", "label": "Exclude NTP", "kind": "flag", "default": "0"},
    {"name": "routing_excluded_ips", "label": "Routing excluded IPs", "kind": "list"},
]

# written by podkop itself on every start and stop
INTERNAL_OPTIONS = {"shutdown_correctly"}

SCHEMA = {"podkop": "0.7.24", "settings": SETTINGS_OPTIONS, "section": SECTION_OPTIONS,
          "internal": sorted(INTERNAL_OPTIONS)}

YOUTUBE_PING_URL = "https://www.youtube.com/generate_204"


def sections_by_name(config: dict) -> dict:
    return {s["name"]: s for s in config.get("sections", [])}


def diff(old: dict, new: dict) -> list[dict]:
    """What changes from old to new: one entry per section/option, plus the
    order of sections when it changed."""
    out = []
    a, b = sections_by_name(old), sections_by_name(new)
    for name, sec in b.items():
        if name not in a:
            out.append({"section": name, "change": "added", "type": sec["type"], "options": sec["options"]})
    for name in a:
        if name not in b:
            out.append({"section": name, "change": "removed"})
    for name, sec in b.items():
        if name not in a:
            continue
        oo, no = a[name].get("options", {}), sec.get("options", {})
        for opt in list(oo) + [k for k in no if k not in oo]:
            if opt in INTERNAL_OPTIONS:
                continue
            if oo.get(opt) != no.get(opt):
                out.append({"section": name, "option": opt, "change": "changed",
                            "old": oo.get(opt), "new": no.get(opt)})
    order_a = [s for s in a if s in b]
    order_b = [s for s in b if s in a]
    if order_a != order_b:
        out.append({"change": "order", "old": order_a, "new": order_b})
    return out


def rebase(base: dict, target: dict, cur: dict) -> tuple[dict, list[str]]:
    """The change from base to target made on cur, the router's settings now
    (moved on since base, e.g. by the nightly probe): only what the change
    touches is taken from target, the rest stays as cur has it. A section or
    option that cur changed too, differently, is a conflict -> (config, [why])."""
    a, b, c = sections_by_name(base), sections_by_name(target), sections_by_name(cur)
    out = {name: copy.deepcopy(sec) for name, sec in c.items()}
    conflicts, order = [], None
    for ch in diff(base, target):
        name, kind = ch.get("section"), ch["change"]
        if kind == "added":
            if name in c and c[name] != b[name]:
                conflicts.append(f"{name}: added here, and the router has another {name}")
            else:
                out[name] = copy.deepcopy(b[name])
        elif kind == "removed":
            if name in c and c[name] != a[name]:
                conflicts.append(f"{name}: removed here, but changed on the router")
            else:
                out.pop(name, None)
        elif kind == "changed":
            if name not in c:
                conflicts.append(f"{name}: changed here, but removed on the router")
                continue
            now = (c[name].get("options") or {}).get(ch["option"])
            if now != ch["old"] and now != ch["new"]:
                conflicts.append(f"{name} / {ch['option']}: changed on the router too")
            elif ch["new"] is None:
                out[name].setdefault("options", {}).pop(ch["option"], None)
            else:
                out[name].setdefault("options", {})[ch["option"]] = copy.deepcopy(ch["new"])
        elif kind == "order":
            if [n for n in a if n in c] != [n for n in c if n in a]:
                conflicts.append("the order of rules: changed on the router too")
            order = [n for n in b]
    cur_order = [n for n in c if n in out]
    if order is None:
        # the router's order; a section added here goes after the one before it in target
        names = list(cur_order)
        for i, n in enumerate(b):
            if n in out and n not in names:
                prev = next((m for m in reversed(list(b)[:i]) if m in names), None)
                names.insert(names.index(prev) + 1 if prev else 0, n)
    else:
        names = [n for n in order if n in out] + [n for n in cur_order if n not in order]
    result = copy.deepcopy(cur)
    result["sections"] = [out[n] for n in names]
    return result, conflicts


def _set_urltest(sec: dict, links: list, fallback: list) -> None:
    o = sec.setdefault("options", {})
    o["connection_type"] = "proxy"
    o["proxy_config_type"] = "urltest"
    o["urltest_proxy_links"] = list(links)
    if fallback:
        o["urltest_fallback_links"] = list(fallback)
    else:
        o.pop("urltest_fallback_links", None)


def merge_probe(config: dict, picks: dict) -> dict:
    """The probe's picks put into a config: {section: {"links", "fallback",
    "youtube"}} from auto_pick become the URLTest of existing sections;
    rules that need YouTube test their servers against YouTube. An empty
    pick leaves its section alone."""
    cfg = copy.deepcopy(config)
    by = sections_by_name(cfg)
    for name, pick in picks.items():
        if name in by and pick["links"]:
            _set_urltest(by[name], pick["links"], pick["fallback"])
            if pick.get("youtube"):
                by[name]["options"]["urltest_testing_url"] = YOUTUBE_PING_URL
    return cfg


def link_name(link: str) -> str:
    """A server's name: the #fragment of its link, else its host."""
    if "#" in link:
        return unquote(link.split("#", 1)[1].replace("+", " "))
    rest = link.split("://", 1)[-1]
    return rest.split("@", 1)[-1].split("?", 1)[0].split("/", 1)[0]


def tag_links(config: dict) -> dict:
    """sing-box outbound tag -> (section, link), the way podkop names outbounds."""
    out = {}
    for s in config.get("sections", []):
        o = s.get("options", {})
        name = s["name"]

        def add(links, mid=""):
            for i, link in enumerate(links if isinstance(links, list) else [links] if links else []):
                out[f"{name}-{mid}{i + 1}-out"] = (name, link)

        if o.get("proxy_config_type") == "urltest":
            add(o.get("urltest_proxy_links"))
            add(o.get("urltest_fallback_links"), "fallback-")
        elif o.get("proxy_config_type") == "selector":
            add(o.get("selector_proxy_links"))
        elif o.get("proxy_config_type") == "url" and o.get("proxy_string"):
            out[f"{name}-out"] = (name, o["proxy_string"])
    return out


def tag_names(config: dict) -> dict:
    """sing-box outbound tag -> server name, the way podkop names outbounds."""
    out = {tag: link_name(link) for tag, (_, link) in tag_links(config).items()}
    for s in config.get("sections", []):
        if s.get("options", {}).get("proxy_config_type") == "urltest":
            out[f"{s['name']}-urltest-out"] = "automatic"
    return out


def exit_targets(config: dict, proxies: dict | None) -> dict:
    """section -> what its traffic leaves through now: the server tag a proxy
    section uses (its selector's choice, or the urltest group's) or a VPN
    section's interface."""
    groups = {g.get("name"): g for g in (proxies or {}).get("groups") or []}
    out = {}
    for s in config.get("sections", []):
        o, name = s.get("options") or {}, s.get("name")
        if s.get("type") != "section":
            continue
        if o.get("connection_type") == "vpn" and o.get("interface"):
            out[name] = o["interface"]
        elif o.get("connection_type") == "proxy" and proxies is not None:
            now = groups.get(f"{name}-out", {}).get("now") or f"{name}-out"
            if now == f"{name}-urltest-out":
                now = groups.get(now, {}).get("now") or ""
            if now:
                out[name] = now
    return out


# --- podkop-probe's results -------------------------------------------------------

USABLE = {"GOOD", "OK", "FLAKY"}


def _option(options: list, name: str, default: float) -> float:
    try:
        return float(options[options.index(name) + 1])
    except (ValueError, IndexError):
        return default


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def probe_reason(row: dict, max_ping: float, min_speed: float) -> str:
    """Why podkop-probe did not pick a server; "" when it works and is fast
    enough but the lists were full."""
    v = row.get("verdict", "")
    if v == "SKIPPED":
        return "podkop cannot read this link"
    if v == "NO START":
        return "sing-box did not start with this server"
    if v == "NOT VIA NODE":
        return "traffic did not go through the server"
    if v == "OTHER REGION":
        return f"exit IP in {row.get('country') or 'an unknown country'}, outside the chosen countries"
    if v not in USABLE:
        return "every request failed: unreachable, blocked or wrong credentials"
    why = []
    median, mbit = _num(row.get("median")), _num(row.get("mbit"))
    if median is not None and median * 1000 >= max_ping:
        why.append(f"ping {round(median * 1000)} ms, {int(max_ping)} or more")
    if mbit is not None and mbit <= min_speed:
        why.append(f"too slow, {int(min_speed)} Mbit/s or less")
    return "; ".join(why)


def probe_limits(options: list) -> tuple[float, float]:
    """The probe's ping (ms) and speed (Mbit/s) limits from its options."""
    return _option(options, "--max-ping", 700), _option(options, "--min-speed", 40)


PROBE_CSV = ("n", "verdict", "ok", "total", "min", "median", "p90", "mbit", "exit_ip", "country", "youtube",
             "youtube_ms", "node")


def probe_csv_rows(text: str, links: str = "") -> list[dict]:
    """The rows of podkop-probe -o FILE (and FILE.links: "n<TAB>link"), as the
    agent's probe-status gives them."""
    link = dict(x.split("\t", 1) for x in links.splitlines() if "\t" in x)
    rows = []
    for f in list(csv.reader(io.StringIO(text)))[1:]:
        if len(f) < len(PROBE_CSV):
            continue
        r = dict(zip(PROBE_CSV, f[:len(PROBE_CSV) - 1] + [",".join(f[len(PROBE_CSV) - 1:])]))
        if links:
            r["link"] = link.get(r["n"])
        rows.append(r)
    return rows


def probe_rows(status: dict, options: list, picks: dict) -> list[dict]:
    """The rows of podkop-probe's CSV with the rules each server went to and,
    for the others, why the probe's limits rule it out. goes_to: [{"rule",
    "fallback"}]; picks: auto_pick results by rule."""
    # matched with the fingerprint fixed on both sides: with that setting on,
    # a pick is the row's link with fp=chrome
    where: dict[str, list] = {}
    for rule, pick in picks.items():
        for fb, key in ((False, "links"), (True, "fallback")):
            for link in pick[key]:
                where.setdefault(fix_fingerprint(link), []).append({"rule": rule, "fallback": fb})
    max_ping, min_speed = probe_limits(options)
    out = []
    for r in status.get("rows") or []:
        goes = where.get(fix_fingerprint(r.get("link") or ""), [])
        out.append(dict(r, goes_to=goes, reason="" if goes else probe_reason(r, max_ping, min_speed)))
    return out


PROTOCOLS = ("vless", "trojan", "ss", "hysteria2", "socks")


def link_protocol(link: str) -> str:
    p = link.split("://", 1)[0].lower() if "://" in link else ""
    return {"hy2": "hysteria2", "shadowsocks": "ss", "socks5": "socks", "socks4": "socks"}.get(p, p)


_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")


def link_host(link: str) -> str:
    """The server's host name or IP address in a link ("" when it can't be read)."""
    rest = link.split("://", 1)[-1].split("#", 1)[0]
    if "@" not in rest:
        return ""
    hostport = rest.rsplit("@", 1)[1].split("?", 1)[0].split("/", 1)[0]
    if hostport.startswith("["):
        return hostport[1:].split("]", 1)[0].lower()
    host = hostport.rsplit(":", 1)[0] if ":" in hostport else hostport
    return host.lower() if _HOST_RE.match(host) else ""


NET_CAP = 2     # servers of one rule from the same /24 network, unless nothing else is as good


def entry_net(ip: str | None) -> str | None:
    """The /24 network of a server's IPv4 address (None: unknown)."""
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return None
    return str(ipaddress.ip_network(f"{addr}/24", strict=False)) if addr.version == 4 else None


def _ip_in(ip: str, nets: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in nets:
        try:
            if addr in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


# what a rule needs from YouTube: nothing, that videos play, that YouTube
# TLS fingerprints without an X25519MLKEM768 key share: REALITY servers since
# Xray-core v26.9.8 reject them (sing-box warns "uTLS fingerprint qq carries no ...")
OLD_FINGERPRINTS = ("qq", "360", "ios", "android", "edge")
_FP = re.compile(r"([?&]fp=)(" + "|".join(OLD_FINGERPRINTS) + r")(?=[&#]|$)")


def old_fingerprint(link: str) -> bool:
    head = link.split("#", 1)[0]
    return "security=reality" in head and bool(_FP.search(head))


def fix_fingerprint(link: str) -> str:
    """A REALITY link with fp=chrome instead of a fingerprint new servers reject."""
    if not old_fingerprint(link):
        return link
    head, sep, name = link.partition("#")
    return _FP.sub(r"\1chrome", head) + sep + name


# podkop-probe's "europe" (EUROPE in router/probe.sh)
EUROPE = ("EU AD AL AT BA BE BG CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS "
          "SE SI SK SM UA VA XK").split()


def _youtube_why(row: dict, need: str) -> str:
    st, _, cc = str(row.get("youtube") or "-").partition(":")
    if st in ("", "-"):
        return "YouTube was not checked on this server"
    if st != "ok":
        return {"bot": "YouTube asks to confirm you're not a bot", "no": "YouTube videos don't play"}.get(st, "YouTube failed")
    if need != "plays" and cc.upper() != need:
        return f"YouTube sees {cc or 'another country'}, not {need}"
    return ""


def check_auto_rule(rule: dict) -> dict:
    """A rule that takes servers from the probe, as stored: lists cleaned up;
    raises ValueError. No countries = any country."""
    if not isinstance(rule, dict):
        raise ValueError("a rule's probe parameters are an object")
    for k in ("countries", "protocols", "exit_ips"):
        if not isinstance(rule.get(k) or [], list) or not all(isinstance(x, str) for x in rule.get(k) or []):
            raise ValueError(f"{k} is a list of text")
    countries = sorted({str(c).strip().upper() for c in rule.get("countries") or [] if str(c).strip()})
    if any(not re.fullmatch(r"[A-Z]{2}", c) for c in countries):
        raise ValueError("countries are two-letter codes like PL")
    protocols = [p for p in PROTOCOLS if p in (rule.get("protocols") or [])]
    if set(rule.get("protocols") or []) - set(protocols):
        raise ValueError(f"protocols are {', '.join(PROTOCOLS)}")
    exit_ips = [str(x).strip() for x in rule.get("exit_ips") or [] if str(x).strip()]
    for x in exit_ips:
        try:
            ipaddress.ip_network(x, strict=False)
        except ValueError:
            raise ValueError(f"{x} is not an IP address or subnet") from None
    try:
        top, fallback = int(rule.get("top", 5)), int(rule.get("fallback", 2))
        max_ping, min_speed = int(rule.get("max_ping", 700)), float(rule.get("min_speed", 40))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("server counts and limits must be numbers") from None
    if not 1 <= top <= 50 or not 0 <= fallback <= 50:
        raise ValueError("keep 1 to 50 servers and 0 to 50 fallback")
    if not 100 <= max_ping <= 5000 or not 0 <= min_speed <= 10000:
        raise ValueError("max ping is 100 to 5000 ms, min speed 0 to 10000 Mbit/s")
    # YouTube: not checked (""), plays anywhere ("plays"), or the country it must see ("RU": no ads)
    youtube = str(rule.get("youtube") or "").strip()
    youtube = youtube if youtube in ("", "plays") else youtube.upper()
    if youtube not in ("", "plays") and not re.fullmatch(r"[A-Z]{2}", youtube):
        raise ValueError("YouTube is '', 'plays' or a two-letter country code like RU")
    profile = rule.get("profile", "balanced")
    if profile not in ("balanced", "interactive", "streaming"):
        raise ValueError("profile is balanced, interactive or streaming")
    return {"profile": profile, "countries": countries, "protocols": protocols, "exit_ips": exit_ips,
            "name_contains": str(rule.get("name_contains") or "").strip(), "top": top, "fallback": fallback,
            "max_ping": max_ping, "min_speed": min_speed, "youtube": youtube}


def auto_pick(rows: list, rule: dict) -> dict:
    """The servers of the probe's rows for a rule: those that pass its filter
    and its limits, reliability first, then spread across subscription keys
    before taking another server from the same key; top for the URLTest, the
    next ones as fallback. left_out: servers in the rule's countries (and of
    its "keys", if given: a row's "key" is the id of the key it came from)
    that were not taken, with the reason."""
    picked, left = [], []
    for r in rows:
        link = r.get("link")
        if not link:
            continue
        in_country = not rule["countries"] or (r.get("country") or "").upper() in rule["countries"]
        if not in_country or (rule.get("keys") and r.get("key") not in rule["keys"]):
            continue
        name = r.get("node") or link_name(link)
        why = ""
        if rule["protocols"] and link_protocol(link) not in rule["protocols"]:
            why = f"{link_protocol(link) or 'unknown protocol'}, not one of the chosen protocols"
        elif rule["exit_ips"] and not _ip_in(r.get("exit_ip") or "", rule["exit_ips"]):
            why = f"exit IP {r.get('exit_ip') or 'unknown'} is not in the list"
        elif rule["name_contains"] and rule["name_contains"].lower() not in name.lower():
            why = f"name does not contain {rule['name_contains']}"
        else:
            why = probe_reason(r, rule["max_ping"], rule["min_speed"])
            if not why and rule["youtube"]:
                why = _youtube_why(r, rule["youtube"])
        item = {"name": name, "link": link, "protocol": link_protocol(link), "exit_ip": r.get("exit_ip"),
                "country": r.get("country"), "median": r.get("median"), "mbit": r.get("mbit"), "verdict": r.get("verdict"),
                "key": r.get("key"), "entry_ip": r.get("entry_ip")}
        item.update(p90=r.get("p90"), health=r.get("health") or {})
        if item["health"].get("quarantined"):
            why = "temporarily excluded after confirmed connection failures"
        if why:
            left.append(dict(item, reason=why))
        else:
            picked.append(item)
    picked.sort(key=lambda x: ({"GOOD": 0, "OK": 1, "FLAKY": 2}.get(x["verdict"], 3),
                               reliability.rank(x, rule.get("profile", "balanced"))))
    # Keys are the available provenance, not proof of independent providers.
    # Unknown provenance is one bucket, never a different provider per node.
    # Keep counts across the primary/fallback boundary to favour an unused key
    # for fallback. Never promote a flaky node over a healthy node for diversity.
    # Networks: at most NET_CAP servers from one /24 while a healthy (GOOD or
    # OK) server elsewhere is left, so one blocked range can't take the whole
    # rule down; servers whose network is unknown are not limited.
    rank_of = {"GOOD": 0, "OK": 1, "FLAKY": 2}
    healthy = {"GOOD": 0, "OK": 0, "FLAKY": 1}
    ordered, counts, nets, seen = [], {}, {}, set()
    while picked:
        tier = healthy.get(picked[0]["verdict"], 2)
        candidates = [i for i, x in enumerate(picked) if healthy.get(x["verdict"], 2) == tier]

        def order(i):
            x = picked[i]
            net = entry_net(x.get("entry_ip"))
            return (net is not None and nets.get(net, 0) >= NET_CAP, rank_of.get(x["verdict"], 3),
                    counts.get(x["key"], 0), i)
        i = min(candidates, key=order)
        item = picked.pop(i)
        identity = item["link"].split("#", 1)[0]
        if identity in seen:
            continue
        seen.add(identity)
        ordered.append(item)
        counts[item["key"]] = counts.get(item["key"], 0) + 1
        if (net := entry_net(item.get("entry_ip"))) is not None:
            nets[net] = nets.get(net, 0) + 1
    picked = ordered
    keep, fb = picked[:rule["top"]], picked[rule["top"]:rule["top"] + rule["fallback"]]
    left += [dict(x, reason=f"outside the {rule['top'] + rule['fallback']} picks by reliability, key and network diversity and latency")
             for x in picked[rule["top"] + rule["fallback"]:]]
    return {"matched": keep, "fallback_rows": fb, "left_out": left, "youtube": bool(rule["youtube"]),
            "links": [x["link"] for x in keep], "fallback": [x["link"] for x in fb]}


_KEY_OK = re.compile(r"^\s*key (\d+)\s+\S+\s+(\d+) servers")
_KEY_BAD = re.compile(r"key (\d+) \([^)]*\): (.*)$")


def probe_keys(log: list, keys: list) -> dict:
    """What podkop-probe's output says about each key (subscription) of the run
    that got `keys`: {url: {"servers": 42}, url2: {"error": "the key's device
    limit ..."}}. The output numbers keys by position in that run."""
    out: dict[str, dict] = {}
    for line in log or []:
        if m := _KEY_OK.match(line):
            n, info = int(m.group(1)), {"servers": int(m.group(2))}
        elif m := _KEY_BAD.search(line):
            n, info = int(m.group(1)), {"error": m.group(2).strip()}
        else:
            continue
        if 0 < n <= len(keys):
            out[keys[n - 1]] = info
    return out


# --- the router's log lines -----------------------------------------------------------

# Sat Oct 17 23:18:40 2026 user.notice podkop: [info] Stopping the podkop
# Sat Oct 17 23:18:48 2026 daemon.err sing-box[14682]: WARN[0000] outbound/vless[...]: ...
_LOG = re.compile(r"^\w{3} \w{3} +\d+ (\d\d:\d\d:\d\d) \d{4} \w+\.(\w+) ([^:\[\s]+)(?:\[\d+\])?: ?(.*)$")
_LEVEL_TAG = re.compile(r"^(?:\[(\w+)\]|(TRACE|DEBUG|INFO|WARN|ERROR|FATAL|PANIC)\[\d+\]) ?")
LEVELS = {"trace": "debug", "debug": "debug", "info": "info", "notice": "info", "warn": "warn",
          "warning": "warn", "err": "error", "error": "error", "fatal": "error", "panic": "error",
          "crit": "error", "alert": "error", "emerg": "error"}
LOG_SOURCES = ("podkop", "sing-box", "engine", "agent")
# failed reverse lookups (PTR in in-addr.arpa / ip6.arpa): devices ask them by themselves (Apple's
# Bonjour asks lb._dns-sd._udp.<its network>) and some zones never answer; nothing breaks, so they
# are "noise", which Logs hides unless asked
_NOISE = re.compile(r"exchanges? failed .*\.(?:in-addr|ip6)\.arpa\.? IN PTR\b")


class RequestLogContext:
    """Bounded, per-router connection context; no HTTP bodies or credentials.

    Info-level sing-box logs supply destinations before later errors that
    only carry a connection ID. Scope IDs by process and expire old entries.
    """

    def __init__(self, limit: int = 4096, ttl: float = 600):
        self.entries = OrderedDict()
        self.limit, self.ttl = limit, ttl

    def enrich(self, line: str) -> str:
        now = time.monotonic()
        while self.entries and next(iter(self.entries.values()))[0] < now - self.ttl:
            self.entries.popitem(last=False)
        proc = re.search(r"sing-box\[(\d+)\]:", line)
        conn = re.search(r"\[(\d+) [^\]]+\]", line)
        if not proc or not conn:
            return line
        key = (proc[1], conn[1])
        _, context = self.entries.pop(key, (now, {}))
        dest = re.search(r"(?:connection to|packet connection to) ([^\s]+)", line)
        outbound = re.search(r"outbound/[^\s\[]+\[([^\]]+)\]", line)
        if dest:
            context["destination"] = dest[1]
        if outbound:
            context["outbound"] = outbound[1]
        if context:
            self.entries[key] = (now, context)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        if parse_log(line)["level"] == "error" and context:
            line += " | request " + " ".join(f"{k}={v}" for k, v in context.items())
        return line


def parse_log(line: str) -> dict:
    """time (router's clock), source (podkop, sing-box, engine, agent), level
    (debug, info, warn, error, or noise: see _NOISE) and the message of one syslog line."""
    m = _LOG.match(line)
    if not m:
        return {"time": "", "source": "", "level": "info", "msg": line}
    time_, prio, tag, msg = m.groups()
    lm = _LEVEL_TAG.match(msg)
    if lm:
        level = LEVELS.get((lm.group(1) or lm.group(2)).lower(), "info")
        msg = msg[lm.end():]
    elif tag == "sing-box" or tag == "podkop" or (tag == "crond" and msg.startswith("USER ")):
        # stderr says nothing: sing-box writes everything there, podkop's own messages carry
        # a [level] and the rest is its tools' progress (wget), crond logs every job start as err
        level = "info"
    else:
        level = LEVELS.get(prio, "info")
    low = tag.lower()
    if "engine" in low or "failsafe" in low or (tag == "sing-box" and "failsafe" in msg.lower()):
        source = "engine"
    elif tag == "podkop-agent":
        source = "agent"
    elif tag == "sing-box":
        source = "sing-box"
    elif low.startswith("podkop"):
        source = "podkop"
    else:
        source = tag
    if source == "sing-box" and _NOISE.search(msg):
        level = "noise"
    return {"time": time_, "source": source, "level": level, "msg": msg}
