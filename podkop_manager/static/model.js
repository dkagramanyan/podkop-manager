// model.js: part of podkop-manager's web interface (see app.js)

import { S } from "./state.js";
import { asList, h, isLink, longWhen, plural } from "./ui.js";
import { countryName } from "./usage.js";

// --- podkop's settings: rules, diffs, labels -----------------------------------------------

export const INTERNAL = () => new Set((S.schema && S.schema.internal) || ["shutdown_correctly"]);
export const sectionsOf = (config) => ((config && config.sections) || []);
export const rulesOf = (config) => sectionsOf(config).filter((s) => s.type === "section");
export const bySection = (config) => new Map(sectionsOf(config).map((s) => [s.name, s]));
export const same = (a, b) => JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);

// what changes from a to b, entry by entry like the manager's podkop.diff
export function configDiff(a, b) {
  const out = [], A = bySection(a), B = bySection(b), internal = INTERNAL();
  for (const [n, s] of B) if (!A.has(n)) out.push({ section: n, change: "added", type: s.type, options: s.options || {} });
  for (const n of A.keys()) if (!B.has(n)) out.push({ section: n, change: "removed" });
  for (const [n, s] of B) {
    if (!A.has(n)) continue;
    const oo = A.get(n).options || {}, no = s.options || {};
    for (const k of [...Object.keys(oo), ...Object.keys(no).filter((k) => !(k in oo))]) {
      if (!internal.has(k) && !same(oo[k], no[k])) out.push({ section: n, option: k, change: "changed", old: oo[k], new: no[k] });
    }
  }
  const oa = [...A.keys()].filter((n) => B.has(n)), ob = [...B.keys()].filter((n) => A.has(n));
  if (!same(oa, ob)) out.push({ change: "order", old: oa, new: ob });
  return out;
}

export const FRIENDLY = {
  connection_type: "Sends to", proxy_config_type: "Servers", proxy_string: "Server", selector_proxy_links: "Servers",
  urltest_proxy_links: "Servers", urltest_fallback_links: "Fallback servers", outbound_json: "sing-box JSON",
  urltest_check_interval: "Check every", urltest_tolerance: "Switch only if faster by", urltest_testing_url: "Testing URL",
  urltest_download_check: "Download check", urltest_download_url: "Download check URL",
  community_lists: "Community lists", user_domain_list_type: "Your domains", user_domains: "Your domains",
  user_domains_text: "Your domains", user_subnet_list_type: "Your subnets", user_subnets: "Your subnets",
  user_subnets_text: "Your subnets", remote_domain_lists: "Domain lists from URLs", remote_subnet_lists: "Subnet lists from URLs",
  local_domain_lists: "Domain list files", local_subnet_lists: "Subnet list files", interface: "VPN interface",
  source_network_interfaces: "Which devices", routing_excluded_ips: "IPs never routed", dns_type: "DNS protocol",
  bootstrap_dns_server: "Bootstrap server", dns_rewrite_ttl: "Rewrite TTL", update_interval: "Update lists every",
};

export function optionDef(section, opt) {
  if (!S.schema) return null;
  return (section === "settings" ? S.schema.settings : S.schema.section).find((d) => d.name === opt) || null;
}

export const optionLabel = (section, opt) => FRIENDLY[opt] || (optionDef(section, opt) || {}).label || opt;

// what a rule matches, in a few words
export function ruleMatches(sec) {
  const o = sec.options || {}, parts = [];
  const lists = asList(o.community_lists);
  if (lists.length === 1) parts.push(`community ${lists[0]}`);
  else if (lists.length && lists.length <= 3) parts.push(lists.join(", "));
  else if (lists.length) parts.push(`${lists.length} community lists`);
  const count = (type, list, text) => (o[type] === "dynamic" ? asList(o[list]).length
    : o[type] === "text" ? String(o[text] || "").split(/[\s,]+/).filter(Boolean).length : 0);
  const d = count("user_domain_list_type", "user_domains", "user_domains_text");
  const n = count("user_subnet_list_type", "user_subnets", "user_subnets_text");
  if (d) parts.push(plural(d, "domain"));
  if (n) parts.push(plural(n, "subnet"));
  const remote = asList(o.remote_domain_lists).length + asList(o.remote_subnet_lists).length;
  const local = asList(o.local_domain_lists).length + asList(o.local_subnet_lists).length;
  if (remote) parts.push(plural(remote, "remote list"));
  if (local) parts.push(plural(local, "list file"));
  return parts.join(", ") || "nothing yet";
}

// where a rule sends traffic: [text, chip class]
export function ruleDest(sec) {
  const o = sec.options || {};
  switch (o.connection_type) {
    case "vpn": return [`VPN ${o.interface || ""}`.trim(), "vpn"];
    case "block": return ["Block", "block"];
    case "exclusion": return ["Direct", "direct"];
    case "proxy": {
      const pt = o.proxy_config_type || "url";
      if (pt === "urltest") return [`Proxy, fastest of ${asList(o.urltest_proxy_links).length}`, ""];
      if (pt === "selector") return [`Proxy, choice of ${asList(o.selector_proxy_links).length}`, ""];
      if (pt === "outbound") return ["Proxy, sing-box JSON", ""];
      return ["Proxy, one server", ""];
    }
    default: return [o.connection_type || "not set", "direct"];
  }
}

export const destChip = (sec) => { const [text, cls] = ruleDest(sec); return h("span", { class: `dest ${cls}` }, text); };

// the server a rule uses now: {text, mode}
export function ruleNow(rt, sec, devices) {
  const o = sec.options || {};
  if (o.connection_type === "block") return { text: "–" };
  if (o.connection_type === "exclusion") return { text: "not through podkop" };
  if (o.connection_type === "vpn") {
    const ok = !devices || devices.includes(o.interface);
    return { text: ok ? o.interface || "–" : `${o.interface} not found`, bad: !ok };
  }
  if (!rt.proxies) return { text: "–" };
  const groups = rt.proxies.groups || [];
  const g = groups.find((x) => x.name === `${sec.name}-out`);
  if (!g) return { text: "–" };
  if (g.now === `${sec.name}-strict-out`) return { text: "refusing connections: no server of its answers", bad: true, mode: "kill switch" };
  const ut = groups.find((x) => x.name === `${sec.name}-urltest-out`);
  const tag = g.now === `${sec.name}-urltest-out` && ut ? ut.now : g.now;
  const server = (rt.proxies.servers || []).find((x) => x.name === tag);
  const pinned = ((rt.status && rt.status.pins) || []).some((p) => p.section === sec.name);
  const name = (rt.names || {})[tag] || tag;
  return { text: server && server.delay ? `${name}, ${server.delay} ms` : name,
    mode: pinned ? "pinned" : o.proxy_config_type === "urltest" ? "auto" : "" };
}

// where a rule's traffic leaves to the internet: "DE, Germany · 203.0.113.5"
export function ruleExit(rt, sec) {
  const e = (rt.exits || {})[sec.name];
  if (!e) return null;
  if (e.error) return h("span", { class: "small muted", title: e.error }, "Exit IP unknown");
  const place = [e.city, e.org].filter(Boolean).join(", ");
  return h("span", { class: "small muted", title: place || null },
    `${e.country ? `${e.country}, ${countryName(e.country)} · ` : ""}${e.ip}`);
}

// the version the router runs now, by its fingerprint
export const runningVersion = (rt, versions) => (rt.status
  ? (versions || []).find((v) => v.fingerprint_after && v.fingerprint_after === rt.status.fingerprint) : null);

// "claude.ai in main, new rule banks_direct" from a diff
export function diffSummary(diff) {
  const parts = diff.map((d) => {
    if (d.change === "added") return `new rule ${d.section}`;
    if (d.change === "removed") return `rule ${d.section} removed`;
    if (d.change === "order") return "rule order";
    const added = asList(d.new).filter((x) => !asList(d.old).includes(x));
    if (Array.isArray(d.new) && added.length === 1 && !isLink(added[0])) return `${added[0]} in ${d.section}`;
    return `${d.section}: ${optionLabel(d.section, d.option).toLowerCase()}`;
  });
  const uniq = [...new Set(parts)];
  return uniq.slice(0, 3).join(", ") + (uniq.length > 3 ? ` and ${uniq.length - 3} more` : "");
}

// --- events ---------------------------------------------------------------------------------

export const EVENT_TEXT = {
  applied: (d) => (d.hot ? "Servers applied, sing-box reloaded them without a podkop restart" : d.restarted ? "Settings applied, podkop restarted" : "Settings saved, podkop is stopped"),
  reverted: (d) => `Change undone: ${d.reason}`,
  revert_failed: (d) => `Change failed and podkop does not run with the old settings either: ${d.revert_reason}`,
  apply_interrupted: (d) => d.message || "A change was cut off (reboot?): the old settings are back",
  imported: (d) => `Added without changing podkop: its settings are in Configs ("${d.config}")${d.keys ? `, ${plural(d.keys, "subscription")} in Keys` : ""}${(d.rules || []).length ? `, ${d.rules.join(" and ")} take links from probe` : ""}`,
  probe_cron_off: () => "podkop-probe no longer runs by itself on the router: the manager runs it",
  podkop_down: (d) => `podkop is down${d.failsafe ? ": " + d.failsafe : ""}`,
  podkop_up: () => "podkop is up again",
  recovered: (d) => `Watchdog: ${d.action}`,
  recovery_failed: (d) => `Watchdog could not bring podkop back: ${d.reason}`,
  recovery_gave_up: () => "Watchdog gave up: 3 tries in the last hour",
  change_not_applied: (d) => `Change #${d.version} (${d.trigger === "probe" ? "the probe's servers" : "scheduled"}) was not applied: ${d.state}${d.message ? `, ${d.message}` : ""}`,
  probe_failed: (d) => `The ${d.trigger || ""} probe failed: ${d.message || ""}`,
  router_offline: (d) => `The manager cannot reach the router: ${d.message || ""}`,
  router_online: () => "The router answers again",
  clash_api_silent: (d) => `sing-box runs but has not answered for ${d.seconds} s; podkop is restarted`,
  vpn: (d) => (d.enabled ? "VPN turned on" : "VPN turned off"),
  vpn_allow: (d) => { const n = Object.values(d.allow || {}).flat().length; return n ? `VPN limited to ${n} device address${n === 1 ? "" : "es"}` : "VPN open to every device"; },
  vpn_down: (d) => `VPN is down${d.last ? `: ${d.last}` : ""}${d.running ? "" : " (OpenVPN is not running)"}`,
  vpn_up: (d) => `VPN is up again${d.address ? `, ${d.address}` : ""}`,
  vpn_restart: (d) => `VPN: gateway ${d.gateway} stopped answering, reconnecting`,
  strict: (d) => ((d.sections || []).length ? `Kill switch on for ${d.sections.join(", ")}` : "Kill switch off for every rule"),
  strict_closed: (d) => `${d.section}: kill switch closed, connections are refused${d.via ? " (the rule it sends through has no server of its own)" : ""}`,
  strict_open: (d) => `${d.section}: kill switch open again`,
  strict_guard: (d) => (d.nft === false ? "Kill switch: the firewall refused the block list"
    : d.dns ? "Kill switch: its sites are blocked on the router while podkop is down" : "Kill switch: podkop is up, the router's block is lifted"),
  pinned: (d) => `${d.section}: pinned ${serverName(d.section, d.server)}`,
  pin_cleared: (d) => `${d.section}: back to automatic`,
  pin_restored: (d) => `${d.section}: pin ${serverName(d.section, d.server)} set again`,
  pin_dropped: (d) => `${d.section}: pin removed, ${d.reason}`,
  agent_started: (d) => `podkop-agent ${d.version} started`,
  agent_upgraded: (d) => `podkop-agent updated to ${d.version} from the manager`,
  agent_upgrade_failed: (d) => `Agent update failed: ${d.message}`,
  service: (d) => `podkop ${d.action}`,
  probe_started: () => "Probe started",
  probe_done: (d) => (d.exit === 0 ? "Probe finished" : `Probe failed (exit ${d.exit})`),
  probe_apply_failed: (d) => `Probe result not applied: ${d.message}`,
  probe_not_started: (d) => `Nightly probe not started: ${d.message}`,
  auto_rule_empty: (d) => `${d.section}: no server matched after the probe, the rule keeps its last servers`,
  keys_sync_failed: (d) => `A provider's new servers were not applied: ${d.message}`,
  usage_threshold: (d) => `${d.key} used ${Math.round(d.pct)}% of today's traffic${d.action === "avoid" ? ", rules move to other keys" : ""}`,
  usage_move_failed: (d) => `Rules not moved for the traffic limit: ${d.message}`,
  group_down: (d) => `${d.section}: none of its servers answers${(d.borrowed || []).length ? `, borrowed ${plural(d.borrowed.length, "working server")} of other rules` : ", nothing to borrow"}`,
  group_recovered: (d) => `${d.section}: its own servers answer again${(d.servers || []).length ? ", borrowed servers removed" : ""}`,
  heal_links: (d) => `${d.section}: got ${plural((d.servers || []).length, "server")} of earlier versions while it is down`,
  heal_links_failed: (d) => `${d.section}: servers of earlier versions not lent: ${d.message}`,
  heal_failed: (d) => `Self-heal could not reload sing-box: ${d.reason}`,
  heal_probe_failed: (d) => `Self-heal probe failed: ${d.message}`,
  heal_no_probe: (d) => `${d.section}: ${d.message}, so self-heal can't find new servers`,
  heal_off: (d) => ((d.sections || []).includes("*") ? "Self-heal switched off" : (d.sections || []).length ? `Self-heal off for ${d.sections.join(", ")}` : "Self-heal on for every rule"),
  stale_probe_not_used: (d) => d.message,
  scheduled: (d) => `Change #${d.version} scheduled for ${longWhen(d.due)}${d.note ? `: ${d.note}` : ""}`,
  schedule_cancelled: (d) => (d.to_draft ? `Scheduled change #${d.version} put back into the draft` : `Scheduled change #${d.version} cancelled`),
  schedule_failed: (d) => `Scheduled change #${d.version} not applied: ${d.message}`,
};
export const EVENT_DOT = {
  applied: "ok", podkop_up: "ok", reverted: "bad", revert_failed: "bad", apply_interrupted: "bad", podkop_down: "bad", recovery_failed: "bad",
  recovery_gave_up: "bad", change_not_applied: "bad", probe_failed: "bad", router_offline: "bad", router_online: "ok", clash_api_silent: "bad", vpn_down: "bad", vpn_up: "ok", vpn_restart: "warn", strict_closed: "bad", strict_open: "ok", probe_apply_failed: "bad", probe_not_started: "bad", keys_sync_failed: "bad", recovered: "warn", pin_dropped: "warn",
  auto_rule_empty: "warn", usage_threshold: "warn", usage_move_failed: "bad",
  group_down: "bad", group_recovered: "ok", heal_links: "warn", heal_links_failed: "bad", heal_failed: "bad",
  heal_probe_failed: "bad", heal_no_probe: "warn", stale_probe_not_used: "warn",
  agent_upgraded: "ok", agent_upgrade_failed: "bad", scheduled: "accent", schedule_failed: "bad",
  pinned: "accent", pin_cleared: "accent", pin_restored: "accent",
};

export function serverName(section, tag) {
  const rt = S.routers[S.rid];
  return (rt && rt.names && rt.names[tag]) || tag;
}

export const eventText = (type, d) => (EVENT_TEXT[type] ? EVENT_TEXT[type](d || {}) : `${type} ${JSON.stringify(d || {})}`);
