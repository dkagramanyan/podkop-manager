// reliability.js: part of podkop-manager's web interface (see app.js)

import { updateAgent } from "./agent.js";
import { getRouter, pageHead, seg } from "./shell.js";
import { S } from "./state.js";
import { ago, api, h, icon, longWhen, put, spinner, toast } from "./ui.js";

export function healthPath(rows = []) {
  if (new Set(rows.map(r => r.target)).size < 2) return "unknown";
  if (rows.every(r => r.exit === 0 && r.code >= 200 && r.code < 400)) return "up";
  if (rows.every(r => [5, 6, 7, 18, 28, 35, 52, 55, 56, 60, 92, 97].includes(r.exit)
    && ![401, 403, 429].includes(r.code))) return "down";
  return "degraded";
}

export function healthVerdict(data, router = {}, now = Date.now() / 1000) {
  const last = data.checks[0], report = last?.report || {};
  const stale = !last || now - last.t > Math.max(180, Math.max(data.settings.interval, 300) * 3);
  const selected = Object.values(report.selected || {});
  const nodes = Object.values(report.nodes || {}).map(healthPath);
  const tips = [];
  let title = "Checks passing", tone = "ok";
  if (router.online === false) {
    title = "Router unreachable"; tone = "bad";
    tips.push("Check the router's power, WAN connection and SSH access from Manager.");
  } else if (data.agent.supported !== true) {
    title = "Diagnostics unavailable"; tone = "warn";
    tips.push(data.agent.message);
  } else if (data.latest.error) {
    title = "Last check could not finish"; tone = "warn";
    tips.push(`${data.latest.error} Run Check now after resolving the collector error.`);
  } else if (stale) {
    title = last ? "Results are out of date" : "No baseline yet"; tone = "warn";
    tips.push("Run Check now to get current DNS, direct internet and proxy results.");
  } else {
    if (report.wan?.up === false || healthPath(report.direct) === "down") {
      title = "Direct internet is failing"; tone = "bad";
      tips.push("Check WAN and the provider connection before changing proxy servers.");
    }
    if ([report.router_dns, report.primary_dns?.ok, report.provider_dns].includes(false)) {
      if (tone !== "bad") { title = "DNS needs attention"; tone = "warn"; }
      tips.push("Compare router, primary and provider DNS below; a cached router answer does not verify the upstream resolver.");
    }
    const payloadFailed = rows => healthPath(rows) !== "up" || rows.some(r => r.bytes !== 65536);
    if (report.payload?.["diagnostic-direct"] && payloadFailed(report.payload["diagnostic-direct"])) {
      if (tone === "ok") { title = "Direct downloads need attention"; tone = "warn"; }
      tips.push("The direct download test did not finish normally. Inspect its endpoint evidence before changing proxy servers.");
    }
    if (nodes.some(s => s === "down" || s === "degraded")
      || Object.entries(report.payload || {}).some(([tag, rows]) => tag !== "diagnostic-direct" && payloadFailed(rows))) {
      if (tone === "ok") { title = "Some proxy checks need attention"; tone = "warn"; }
      tips.push("Inspect the failed endpoints and download results below. Access rejections do not confirm a broken proxy.");
    }
    if (healthPath(report.direct) !== "up" || report.router_dns !== true || !nodes.length
      || nodes.includes("unknown") || selected.some(tag => !report.nodes?.[tag])) {
      if (tone === "ok") { title = "Checks are incomplete"; tone = "warn"; }
      tips.push("Checks rotate through up to four servers each round. Run another check to fill gaps in the current sample.");
    }
    if (!tips.length) tips.push("Router checks passed for the sampled paths. Use application monitors to verify LAN forwarding and existing sessions.");
  }
  return { title, tone, tips, stale, last, report };
}

export async function viewReliability(main, id) {
  const page = h("div", { class: "page" });
  const content = h("div", { class: "stack" });
  const activityBody = h("div", { class: "stack" });
  let data, webhook = "", timer, poll, left = false, request = 0, hours = 24;
  let dirty = false, checking = false, saving = false, settingsForm;
  let enabled, automatic, interval, cooldown, save, reset, settingsNote;
  let query = "", source = "all", kind = "all";
  let heal = null, healDraft = null, healSaving = false, ipv6Saving = false, dnsSaving = false;
  const opened = new Set();
  const endpoint = `/api/routers/${id}/reliability`;
  const table = (headers, rows) => h("div", { class: "scroll-x" }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map(x => h("th", { scope: "col" }, x)))),
    h("tbody", {}, rows.map(row => h("tr", {}, row.map(x => h("td", {}, x)))))));
  const labels = { transport: "Connection failure", dns: "DNS failure", access: "Access rejected", application: "Application failure",
    lists: "List download failed", switch: "Server switch", recovery: "Recovery reported", pending: "Retry pending", test: "Test notification" };
  const badge = (value, text) => h("span", { class: `pill ${value === true || value === "up" ? "ok"
    : value === false || value === "down" ? "bad" : value === "degraded" ? "warn" : ""}` },
    text || (value === true || value === "up" ? "Responding" : value === false || value === "down" ? "Failed"
      : value === "degraded" ? "Partial / rejected" : "Not checked"));
  const disclose = (key, title, body, extra = "") => h("details", { class: "health-details", "data-health-key": key, open: opened.has(key), ontoggle: e => {
    if (e.currentTarget.open) opened.add(key); else opened.delete(key);
  } }, h("summary", {}, icon("right", 14, { class: "chev" }), h("span", { class: "grow" }, title),
    extra ? h("span", { class: "small muted" }, extra) : null), h("div", { class: "health-detail-body stack" }, body));
  const load = async () => {
    const token = ++request;
    const [next, nextHeal] = await Promise.all([api("GET", `${endpoint}?hours=${hours}`),
      api("GET", `/api/routers/${id}/heal`).catch(() => null)]);
    if (left || token !== request) return;
    data = next; heal = nextHeal; draw();
  };
  const markDirty = () => { dirty = true; syncSettings(); };
  const syncSettings = () => {
    const supported = data.agent.supported === true;
    enabled.disabled = saving || (!supported && !enabled.checked);
    automatic.disabled = saving || !supported || !enabled.checked;
    interval.disabled = saving || !supported; cooldown.disabled = saving || !supported;
    save.disabled = saving || !dirty;
    reset.hidden = !dirty;
    reset.disabled = saving;
    settingsNote.textContent = dirty ? "Unsaved changes" : data.settings.enabled
      ? `Monitoring every ${data.settings.interval} seconds (${Math.min(data.settings.interval * 5, Math.max(data.settings.interval, 300))} while nothing is wrong) · automatic recovery ${data.settings.auto_recover ? "on" : "off"}`
      : "Monitoring is off. You can still run checks manually.";
  };
  const resetSettings = () => {
    enabled.checked = data.settings.enabled; automatic.checked = data.settings.auto_recover;
    interval.value = data.settings.interval; cooldown.value = data.settings.cooldown;
    dirty = false; syncSettings();
  };
  const buildSettings = () => {
    enabled = h("input", { type: "checkbox", onchange: markDirty });
    automatic = h("input", { type: "checkbox", onchange: markDirty });
    interval = h("input", { class: "input", id: "health-interval", type: "number", min: 30, max: 300, required: true, oninput: markDirty });
    cooldown = h("input", { class: "input", id: "health-cooldown", type: "number", min: 60, max: 3600, required: true, oninput: markDirty });
    save = h("button", { type: "submit", class: "btn primary" }, "Save settings");
    reset = h("button", { type: "button", class: "btn", onclick: resetSettings }, "Discard changes");
    settingsNote = h("p", { class: "muted small", role: "status" });
    settingsForm = h("form", { class: "stack", onsubmit: async e => {
      e.preventDefault(); saving = true; syncSettings();
      try {
        const opts = await api("PUT", endpoint, { ...data.settings, enabled: enabled.checked,
          auto_recover: enabled.checked && automatic.checked, interval: Number(interval.value), cooldown: Number(cooldown.value) });
        data.settings = opts; dirty = false; toast("Connection monitoring settings saved"); await load();
      } catch (err) { toast(err.message, true); }
      finally { saving = false; syncSettings(); }
    } }, h("label", { class: "check" }, enabled, "Monitor connections from the router"),
    h("label", { class: "check" }, automatic, "Temporarily exclude repeatedly failing proxies"),
    h("div", { class: "health-grid" },
      h("div", { class: "field" }, h("label", { for: "health-interval" }, "Pause between checks, seconds"), interval),
      h("div", { class: "field" }, h("label", { for: "health-cooldown" }, "Minimum exclusion time, seconds"), cooldown)),
    h("p", { class: "small muted" }, "Recovery requires working direct internet and DNS, repeated paired proxy failures and a recently tested replacement. Manual pins and pending drafts are respected. Checks continue during cooldown."),
    h("div", { class: "row" }, save, reset), settingsNote);
    resetSettings();
  };
  const filter = (label, values, change) => h("label", { class: "row small" }, label,
    h("select", { class: "input", onchange: e => change(e.target.value) }, values.map(([value, title]) => h("option", { value }, title))));
  const activityTools = h("div", { class: "row" },
    h("label", { class: "search grow" }, icon("search", 15), h("span", { class: "sr" }, "Search grouped observations"),
      h("input", { placeholder: "Search server, destination or latest message", oninput: e => { query = e.target.value.toLowerCase().trim(); drawActivity(); } })),
    filter("Source", [["all", "All sources"], ["router", "Router logs"], ["active check", "Active checks"], ["kuma", "Uptime Kuma"]], v => { source = v; drawActivity(); }),
    filter("Type", [["all", "All types"], ...Object.entries(labels)], v => { kind = v; drawActivity(); }));
  const activity = h("section", { class: "card pad stack", "aria-label": "Grouped observations" },
    h("div", { class: "col" }, h("h2", {}, "Grouped observations"),
      h("p", { class: "small muted" }, "Repeated messages are grouped by source, type and target. Counts are observations, not outages. Expand a group for its latest evidence.")), activityTools, activityBody);
  const drawActivity = () => {
    const summary = data.summary;
    const names = getRouter(id).names || {};
    const groups = summary.groups.filter(g => (source === "all" || g.source === source) && (kind === "all" || g.kind === kind)
      && (!query || [g.scope, names[g.scope], g.sample.message, g.sample.destination, labels[g.kind]].filter(Boolean).join(" ").toLowerCase().includes(query)));
    put(activityBody, groups.length ? groups.map(g => {
      const sample = g.sample, label = labels[g.kind] || g.kind;
      const title = h("div", { class: "health-group-title" },
        h("span", { class: `pill ${["transport", "dns", "access", "application"].includes(g.kind) ? "warn" : g.kind === "recovery" ? "ok" : ""}` }, label),
        h("b", {}, names[g.scope] || g.scope || "Unattributed"), h("span", { class: "muted small" }, g.source));
      return disclose(`group-${JSON.stringify([g.source, g.kind, g.scope])}`, title,
        [h("p", { class: "small muted" }, `First received ${longWhen(g.first)} · last received ${longWhen(g.last)}. Targets and messages may vary within this group.`),
          sample.destination ? h("p", {}, `Latest destination: ${sample.destination}`) : null,
          h("pre", { class: "health-evidence" }, sample.message || "No message supplied."),
          sample.reported_time ? h("p", { class: "small muted" }, `Source timestamp: ${sample.reported_time}`) : null,
          sample.rows ? endpointTable(sample.rows) : null,
          h("a", { href: `#/r/${id}/logs`, class: "link-btn" }, "Open full logs")], `${g.count.toLocaleString()} × · ${ago(g.last)}`);
    }) : h("p", { class: "empty" }, summary.total ? "No groups match these filters." : "No observations in this time window. Run a check to establish current health."),
    h("p", { class: "small muted" }, `Showing ${groups.length} of ${summary.group_count} groups · ${summary.total.toLocaleString()} observations in this window.`
      + (summary.group_count > summary.groups.length ? " Only the 100 most frequent groups are shown; totals include all groups." : "")));
  };
  const host = target => { try { return new URL(target).hostname; } catch { return target || "Unknown target"; } };
  const endpointTable = rows => table(["Endpoint", "Result", "Response", "Downloaded"], rows.map(r => [host(r.target),
    badge(r.exit === 0 && r.code >= 200 && r.code < 400 ? "up" : r.exit === 0 ? "degraded" : "down",
      r.exit === 0 ? `HTTP ${r.code}` : `Request failed (curl ${r.exit})`),
    Number.isFinite(r.seconds) ? `${Math.round(r.seconds * 1000)} ms` : "–", r.bytes == null ? "–" : `${(r.bytes / 1024).toFixed(1)} KiB / 64 KiB`]));
  // IPv6: podkop routes IPv4 only; a LAN with IPv6 lets devices that resolve
  // names themselves reach sites over IPv6, past podkop
  const ipv6Card = () => {
    const v6 = (getRouter(id).status || {}).ipv6;
    const body = v6 === undefined
      ? [h("p", { class: "small muted" }, "Needs podkop-agent 0.11.0 or newer on the router."),
        h("div", { class: "row" }, h("button", { class: "btn", onclick: () => updateAgent(id, load) }, "Update agent"))]
      : [h("label", { class: "check" }, h("input", { type: "checkbox", checked: v6.blocked, disabled: ipv6Saving, onchange: async e => {
        const on = e.target.checked;
        ipv6Saving = true; draw();
        try { await api("PUT", `/api/routers/${id}/ipv6`, { blocked: on }); toast(on ? "IPv6 from the LAN is blocked" : "IPv6 from the LAN is allowed again"); }
        catch (err) { toast(err.message, true); }
        finally { ipv6Saving = false; if (!left) draw(); }
      } }), "Block IPv6 from the LAN to the internet (a firewall rule, REJECT: devices fall back to IPv4 at once)"),
      v6.lan && !v6.blocked ? h("p", { class: "small bad-text" }, "This router gives the LAN IPv6 and has an IPv6 route to the provider: a device that resolves names itself (a browser with its own DNS, a phone's private DNS) reaches any site over IPv6 without podkop.")
        : h("p", { class: "small muted" }, v6.lan ? "The LAN gets IPv6 from this router; the rule keeps it off the internet." : "This router gives the LAN no IPv6 route to the internet right now; the rule is a safeguard for when it does.")];
    return h("section", { class: "card pad stack", "aria-label": "IPv6" },
      h("div", { class: "col" }, h("h2", {}, "IPv6"),
        h("p", { class: "small muted" }, "podkop routes IPv4 only. Rules by domain hold because the router's DNS answers no IPv6 addresses, but a device with its own DNS, or an address list, goes around them over IPv6.")),
      body);
  };
  // podkop's rules by domain work through the router's DNS; a device that
  // asks another DNS server gets the real addresses and goes around them
  const dnsCard = () => {
    const dns = (getRouter(id).status || {}).dns;
    const body = dns === undefined
      ? [h("p", { class: "small muted" }, "Needs podkop-agent 0.26.0 or newer on the router."),
        h("div", { class: "row" }, h("button", { class: "btn", onclick: () => updateAgent(id, load) }, "Update agent"))]
      : [h("label", { class: "check" }, h("input", { type: "checkbox", checked: dns.guard, disabled: dnsSaving, onchange: async e => {
        const on = e.target.checked;
        dnsSaving = true; draw();
        try { await api("PUT", `/api/routers/${id}/dns-guard`, { on }); toast(on ? "Devices now use the router's DNS" : "Devices may use their own DNS again"); }
        catch (err) { toast(err.message, true); }
        finally { dnsSaving = false; if (!left) draw(); }
      } }), "Make the LAN's devices use the router's DNS (firewall rules)"),
      h("p", { class: "small muted" }, "Plain DNS (port 53) to any server is answered by the router; DNS over TLS (port 853) and DNS over HTTPS to the well-known public resolvers (Cloudflare, Google, Quad9, AdGuard, OpenDNS, Yandex, NextDNS and a few more) are refused, so browsers fall back to the router's DNS. IPv4 from the lan zone; a resolver that is not in the list still gets through. A phone with a fixed Private DNS server name shows \"no internet\" until that setting is put back to automatic.")];
    return h("section", { class: "card pad stack", "aria-label": "Devices' DNS" },
      h("div", { class: "col" }, h("h2", {}, "Devices' own DNS"),
        h("p", { class: "small muted" }, "A rule by domain works because the router's DNS answers with an address that leads into podkop. A device that asks another DNS server (a browser's secure DNS, a fixed 8.8.8.8) gets the site's real address and reaches it directly, past the rule and its kill switch.")),
      body);
  };
  // Self-heal: a URLTest group whose own servers all fail borrows working
  // servers on the router and the probe runs; sections switched off are left alone.
  const healCard = () => {
    if (!heal) return null;
    if (!heal.supported) return h("section", { class: "card pad stack" }, h("h2", {}, "Self-heal"),
      h("p", { class: "small muted" }, "Needs podkop-agent 0.9.0 or newer on the router."),
      h("div", { class: "row" }, h("button", { class: "btn", onclick: () => updateAgent(id, load) }, "Update agent")));
    const off = healDraft || new Set(heal.off);
    const allOff = off.has("*");
    const toggle = (name, on) => {
      healDraft = new Set(off);
      if (name === "*") { if (on) healDraft.delete("*"); else healDraft.add("*"); }
      else if (on) healDraft.delete(name); else healDraft.add(name);
      draw();
    };
    const strict = name => (heal.strict || []).includes(name) ? h("span", { class: "mode", title: "Through its own servers or nowhere (set on the rule)" }, "kill switch") : null;
    const status = name => {
      const g = heal.groups[name];
      if (!g) return h("span", { class: "row", style: "gap: 8px" }, h("span", { class: "pill ok" }, "Working"), strict(name));
      const n = (g.added || []).length + (g.extra || []).length;
      if (!g.reported) return h("span", { class: "pill warn" }, `No server answers since ${longWhen(g.dead_since)}`);
      return h("div", { class: "col" }, h("span", { class: "row", style: "gap: 8px" }, h("span", { class: "pill bad" }, `Down since ${longWhen(g.dead_since)}`), strict(name)),
        h("span", { class: "small muted" }, g.block ? "Kill switch: connections are refused until one of its servers answers"
          : n ? `Using ${n} borrowed server${n > 1 ? "s" : ""} until its own work again`
          : "Nothing to borrow; the probe looks for new servers"));
    };
    const save = async () => {
      healSaving = true; draw();
      try {
        await api("PUT", `/api/routers/${id}/heal`, { off: [...healDraft] });
        healDraft = null; toast("Self-heal settings saved"); await load();
      } catch (err) { toast(err.message, true); }
      finally { healSaving = false; if (!left) draw(); }
    };
    return h("section", { class: "card pad stack", "aria-label": "Self-heal" },
      h("div", { class: "col" }, h("h2", {}, "Self-heal"),
        h("p", { class: "small muted" }, "The router tests the server each rule uses every 30 seconds. When none of a rule's own servers answers in two checks in a row (30–75 seconds) and the router's line works, the router lends it up to 3 working servers of other rules at once, in memory only. The manager adds servers from earlier versions if there are none to lend, and runs the probe; its servers are applied when it finishes. Borrowed servers go away when the rule's own servers answer again or its settings change. Rules switched off here are never changed and never lend their servers.")),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: !allOff, disabled: healSaving,
        onchange: e => toggle("*", e.target.checked) }), "Self-heal on this router"),
      heal.guard && (heal.strict || []).length ? h("p", { class: "small muted" }, `Kill switch guard: ${heal.guard.domains} domains of the kill-switch rules answer NXDOMAIN while podkop is down or restarting${
        heal.guard.dns ? " (active now: podkop is not running)" : ""}; ${heal.guard.subnets} subnets${heal.guard.resolved != null ? ` and ${heal.guard.resolved} addresses the rules' own domains resolve to (for devices with their own DNS, refreshed hourly)` : ""} are ${
        heal.guard.nft ? "blocked on the router at all times" : "not blocked (podkop is stopped on purpose)"}.${
        heal.guard.blocked != null ? ` Refused since the list was last loaded: ${heal.guard.blocked} packets.` : ""}${
        heal.guard.boot ? " A copy on the router goes in at boot, before podkop starts." : ""}`) : null,
      heal.guard && heal.guard.offload && (heal.strict || []).length ? h("p", { class: "small bad-text" },
        "Flow offloading is on in the router's firewall: offloaded connections go past the firewall's chains, so the kill switch cannot cut a connection that is already open. Switch it off (Network → Firewall) for a kill switch you can rely on.") : null,
      heal.sections.length ? table(["Rule", "Self-heal", "Now"], heal.sections.map(name => [h("b", {}, name),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: !allOff && !off.has(name), disabled: allOff || healSaving,
          onchange: e => toggle(name, e.target.checked) }), allOff || off.has(name) ? "Off" : "On"),
        allOff || off.has(name) ? h("span", { class: "muted small" }, "Left alone") : status(name)]))
        : h("p", { class: "empty" }, "No URLTest rules on this router."),
      healDraft ? h("div", { class: "row" },
        h("button", { class: "btn primary", disabled: healSaving, onclick: save }, healSaving ? "Saving…" : "Save"),
        h("button", { class: "btn", disabled: healSaving, onclick: () => { healDraft = null; draw(); } }, "Discard changes")) : null);
  };
  const runCheck = async () => {
    checking = true; draw();
    try { await api("POST", `${endpoint}/check`, {}); await load(); }
    catch (err) { toast(err.message, true); await load().catch(() => {}); }
    finally { checking = false; if (!left) draw(); }
  };
  const draw = () => {
    const focused = page.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused?.closest("details")?.dataset.healthKey;
    const selection = focused && ["INPUT", "TEXTAREA"].includes(focused.tagName)
      ? [focused.selectionStart, focused.selectionEnd] : null;
    const rt = getRouter(id), opts = data.settings, supported = data.agent.supported === true;
    const verdict = healthVerdict(data, rt), { last, report, stale } = verdict;
    const names = { ...(report.server_names || {}), ...(rt.names || {}) };
    const name = tag => names[tag] || tag;
    const servers = Object.values(data.servers).sort((a, b) => Number(b.quarantined) - Number(a.quarantined) || b.checked - a.checked);
    const selected = new Set(Object.values(report.selected || {}));
    const banned = new Set(report.banned || []);
    const card = (title, value, detail) => h("div", { class: "card pad health-metric" },
      h("span", { class: "small muted" }, title), value, h("span", { class: "small muted" }, detail));
    const freshBadge = value => stale || data.latest.error || rt.online === false || !supported ? badge(null, "Needs fresh check") : badge(value);
    if (!settingsForm) buildSettings();
    else if (!dirty) resetSettings();
    else syncSettings();
    put(content,
      pageHead("Connection health", "Router diagnostics, recurring failures and recovery evidence.",
        h("button", { type: "button", class: "btn primary", disabled: !supported || checking || rt.online === false, onclick: runCheck },
          checking ? spinner() : icon("refresh", 15), checking ? "Checking…" : "Check now"),
        h("a", { class: "btn", href: `#/r/${id}/logs` }, "Full logs")),
      h("section", { class: `card pad stack health-verdict ${verdict.tone}`, "aria-label": "Current health" },
        h("div", { class: "row" }, h("span", { class: `dot ${verdict.tone}` }), h("h2", { class: "grow" }, verdict.title),
          h("span", { class: "small muted" }, last ? `Last completed ${ago(last.t)} · ${longWhen(last.t)}` : "No completed checks")),
        h("ul", { class: "health-tips" }, verdict.tips.map(tip => h("li", {}, tip))),
        h("div", { class: "row small muted" },
          h("span", {}, opts.enabled ? `Monitoring every ${opts.interval} s` : "Monitoring off"), h("span", {}, "·"),
          h("span", {}, `Automatic recovery ${opts.auto_recover ? "on" : "off"}`), h("span", {}, "·"),
          h("span", {}, S.live ? "Live updates connected" : "Live updates reconnecting; refreshes every 30 s")),
        !supported ? h("div", { class: "row" },
          h("button", { class: "btn", onclick: () => updateAgent(id, load) }, "Update agent"),
          h("button", { class: "btn", onclick: async e => {
            const b = e.currentTarget; b.disabled = true;
            try { await api("POST", `/api/routers/${id}/refresh`); await load(); } catch (err) { toast(err.message, true); }
            finally { b.disabled = false; }
          } }, "Refresh router")) : null),
      h("div", { class: "health-grid" },
        card("Direct internet", freshBadge(healthPath(report.direct)), "Two independent HTTPS endpoints"),
        card("Router DNS", freshBadge(report.router_dns), "Resolution through Podkop"),
        card("Upstream DNS", h("div", { class: "row" }, freshBadge(report.primary_dns?.ok)),
          `Primary: ${report.primary_dns?.method || "not checked"} · provider: ${report.provider_dns === true ? "responding" : report.provider_dns === false ? "failed" : "not checked"}`),
        card("Proxy sample", h("strong", { class: "num" }, `${Object.values(report.nodes || {}).filter(rows => healthPath(rows) === "up").length} / ${Object.keys(report.nodes || {}).length} responding`),
          stale ? "Historical sample; run a fresh check" : "Up to four server configurations per round")),
      h("section", { class: "card pad stack" },
        h("div", { class: "row" }, h("div", { class: "col grow" }, h("h2", {}, "Activity"),
          h("p", { class: "small muted" }, "History window; current diagnostics above always use the last completed check.")),
          seg([["1", "1 hour"], ["6", "6 hours"], ["24", "24 hours"], ["168", "7 days"]], String(hours), v => {
            hours = Number(v); load().catch(err => toast(err.message, true));
          }, { label: "History window" })),
        h("div", { class: "health-totals" },
          h("span", {}, h("b", {}, data.summary.total.toLocaleString()), " observations"),
          h("span", {}, h("b", {}, data.summary.failures.toLocaleString()), " failure observations"),
          h("span", {}, h("b", {}, data.summary.group_count.toLocaleString()), " groups")),
        h("p", { class: "small muted" }, `Received ${longWhen(data.summary.since)} — ${longWhen(data.summary.until)}. Log volume alone does not establish current health.`)),
      activity,
      h("section", { class: "card pad stack" }, h("h2", {}, "Servers and recovery"),
        h("p", { class: "small muted" }, "Excluded means the latest completed check confirmed a runtime exclusion. Cooldown alone is a recommendation. Last selections are from that check; stale rows are labelled."),
        servers.length ? table(["Server", "Last result", "Response", "Confirmed failures", "Recovery", "Checked"], servers.map(s => {
          const old = Date.now() / 1000 - s.checked > Math.max(180, opts.interval * 3);
          return [h("div", { class: "col" }, h("b", {}, name(s.tag)),
            h("span", { class: "small muted" }, s.tag), selected.has(s.tag) ? h("span", { class: "pill accent" }, "Selected at last check") : null),
          h("div", { class: "col" }, badge(s.status), s.download_status ? h("span", { class: "small" }, `Download: ${s.download_status}`) : null),
          s.response_ms == null ? "–" : `${s.response_ms} ms`,
          h("div", { class: "col" }, `${s.failed_samples || 0} / ${s.samples || 0} samples`, h("span", { class: "small muted" }, "Recent samples, up to 24 h / 120 checks")),
          h("div", { class: "col" }, banned.has(s.tag) ? h("span", { class: "pill warn" }, "Excluded at last check")
            : s.quarantined ? h("span", { class: "pill warn" }, "Cooldown recommended") : h("span", {}, "No exclusion recommended"),
            s.quarantined ? h("span", { class: "small muted" }, `${Date.now() / 1000 < s.until ? `Until at least ${longWhen(s.until)}; then ` : "Awaiting "}${opts.recovery_successes} successful recovery checks`) : null),
          h("div", { class: "col" }, ago(s.checked), old ? h("span", { class: "pill warn" }, "Stale") : null)];
        })) : h("p", { class: "empty" }, "No server checks yet. Run Check now to test the selected servers and backups.")),
      h("section", { class: "card pad stack" }, h("h2", {}, "Diagnostic evidence"),
        h("p", { class: "small muted" }, last ? `Last completed ${longWhen(last.t)}. These tests use an isolated engine on the router; LAN forwarding and existing application sessions need separate checks.` : "No completed diagnostics yet."),
        disclose("router-checks", "WAN, DNS and router resources", table(["Check", "Result"], [
          ["WAN", h("div", { class: "row" }, badge(report.wan?.up), report.wan?.device || "")],
          ["Gateway ping", badge(report.gateway)], ["Router DNS", badge(report.router_dns)],
          ["Primary DNS", h("div", { class: "row" }, badge(report.primary_dns?.ok), report.primary_dns?.method || "")],
          ["Provider DNS", badge(report.provider_dns)],
          ["Available memory", report.available_kib == null ? "Not checked" : `${Math.round(report.available_kib / 1024)} MiB`],
          ["Load (1 / 5 / 15 min)", report.load || "Not checked"]])),
        ...(last ? [["direct", "Direct internet endpoints", report.direct || []],
          ...Object.entries(report.nodes || {}).map(([tag, rows]) => [tag, `Proxy: ${name(tag)}`, rows])].map(([key, title, rows]) =>
            disclose(`path-${key}`, title, [endpointTable(rows),
              report.payload?.[key === "direct" ? "diagnostic-direct" : key]
                ? h("div", { class: "stack" }, h("h3", {}, "64 KiB download checks"), endpointTable(report.payload[key === "direct" ? "diagnostic-direct" : key]))
                : h("p", { class: "small muted" }, "No download test in this round. Downloads normally run every five minutes."),
              h("p", { class: "small muted" }, "HTTP 401, 403 and 429 indicate access rejection. They do not confirm a transport failure.")], healthPath(rows))) : []),
        Object.keys(report.selected || {}).length ? disclose("selections", "Routing at last completed check",
          table(["Rule", "Selected server"], Object.entries(report.selected).map(([rule, tag]) => [rule, name(tag)]))) : null),
      h("section", { class: "card pad stack" }, disclose("incidents", "Correlated incidents",
        [h("p", { class: "small muted" }, "Network failures received within 30 seconds are correlated. Last observation is not a measured recovery time. Incidents overlapping this window can include earlier observations. Showing up to 100 incidents."),
          data.incidents.length ? table(["First / last received", "Type", "Observations", "Selected servers at first observation"], data.incidents.map(i => [
            `${longWhen(i.started)} — ${longWhen(i.last)}`, i.category, i.count,
            Object.entries(i.context.selected || {}).map(([rule, tag]) => `${rule}: ${i.context.server_names?.[tag] || tag}`).join(", ") || "Unknown"]))
            : h("p", { class: "empty" }, "No incidents in this window.")], `${data.incidents.length}${data.incidents.length === 100 ? "+" : ""}`),
        disclose("observations", "Individual observations",
          [h("p", { class: "small muted" }, "Newest 300 observations in this window; grouped counts above include the full window."),
            data.observations.length ? table(["Received", "Source", "Type / target", "Message"], data.observations.map(o => [longWhen(o.t), o.source,
              h("div", { class: "col" }, labels[o.data.kind] || o.data.kind, o.data.scope), h("span", { class: "health-message" }, o.data.message)]))
              : h("p", { class: "empty" }, "No observations in this window.")], String(data.observations.length))));
    drawActivity();
    put(page, content, healCard(), ipv6Card(), dnsCard(),
      h("section", { class: "card pad stack" }, disclose("settings", "Monitoring and recovery settings", settingsForm)),
      h("section", { class: "card pad stack" }, disclose("kuma", "Uptime Kuma integration", [
        h("p", {}, "Use a Kuma Webhook notification with its default JSON body. Events are correlated with router checks; notifications alone never trigger proxy changes."),
        h("p", { class: "small muted" }, data.kuma_connected ? "A webhook token is configured. Replacing it revokes the previous URL." : "No webhook token configured."),
        h("button", { class: "btn", onclick: async () => {
          try { const r = await api("POST", `/api/routers/${id}/kuma-token`, {});
            webhook = new URL(r.path, location.origin).href; data.kuma_connected = true; opened.add("kuma"); draw(); }
          catch (err) { toast(err.message, true); }
        } }, data.kuma_connected ? "Replace webhook token" : "Create webhook URL"),
        webhook ? h("input", { class: "input mono", value: webhook, readonly: true, "aria-label": "Kuma webhook URL", onclick: e => e.target.select() }) : null,
        h("p", { class: "small muted" }, "Use separate monitors for the router, direct internet, DNS and application access. Keep 403 responses as access failures and try two retries before notifying.")])));
    if (focused?.isConnected) {
      focused.focus({ preventScroll: true });
      if (selection && selection[0] !== null) focused.setSelectionRange(...selection);
    } else if (focusKey) {
      [...page.querySelectorAll("details")].find(el => el.dataset.healthKey === focusKey)?.querySelector("summary")?.focus({ preventScroll: true });
    }
  };
  put(main, page);
  await load();
  const reload = routerId => {
    if (routerId !== id || timer || left) return;
    timer = setTimeout(() => { timer = null; load().catch(() => {}); }, 1500);
  };
  poll = setInterval(() => reload(id), 30000);
  S.view = { leave: () => { left = true; ++request; clearTimeout(timer); clearInterval(poll); },
    // (not on every status of the router, twice a minute and more: the page is 60 reports and 300 rows)
    onReliability: m => reload(m.router_id), onLive: () => reload(id) };
}
