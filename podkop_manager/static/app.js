"use strict";
// podkop-manager web interface: hash routes (#/r/<router>/<section>), plain
// DOM, live updates over server-sent events.

const S = {
  routers: {},        // id -> snapshot from /api/routers and the stream
  schema: null,       // /api/schema
  manager: null,      // /api/manager
  view: null,         // the open page's live-update handlers
  live: false,        // the stream is connected
  work: {},           // id -> the routing draft being edited (see loadWork)
};

// --- helpers --------------------------------------------------------------------

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// replaceChildren that, like h(), flattens arrays and skips empty values
function put(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false)
    .map((c) => (c instanceof Node ? c : document.createTextNode(String(c)))));
}

const ICONS = {
  overview: '<rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/>',
  routing: '<path d="M2 4.5h3.5l4 7H14M9.5 4.5H14M12 2.5l2 2-2 2M12 9.5l2 2-2 2"/>',
  probe: '<path d="M2.5 11.5a5.5 5.5 0 0 1 11 0"/><path d="M8 11.5l2.6-3.6"/>',
  logs: '<path d="M3 4h10M3 8h10M3 12h6"/>',
  usage: '<path d="M3 13V8.5M6.5 13V4M10 13V9.5M13.5 13V6"/>',
  history: '<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.2l2.2 1.4"/>',
  settings: '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M3.6 12.4L5 11M11 5l1.4-1.4"/>',
  updown: '<path d="M5 6l3-3 3 3M5 10l3 3 3-3"/>',
  check: '<path d="M3.5 8.5l3 3 6-7"/>',
  plus: '<path d="M8 3v10M3 8h10"/>',
  refresh: '<path d="M13 8a5 5 0 1 1-1.5-3.5M13 2.5V5h-2.5"/>',
  trash: '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M5 4.5l.6 8.5h4.8l.6-8.5"/>',
  close: '<path d="M4 4l8 8M12 4l-8 8"/>',
  down: '<path d="M4 6l4 4 4-4"/>',
  up: '<path d="M4 10l4-4 4 4"/>',
  right: '<path d="M6 4l4 4-4 4"/>',
  arrow: '<path d="M3 8h10M9 4l4 4-4 4"/>',
  search: '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/>',
  shield: '<path d="M10 2.5l6 2.2v4.6c0 3.8-2.6 6.6-6 8.2-3.4-1.6-6-4.4-6-8.2V4.7z"/><path d="M7.2 10l2 2 3.6-4"/>',
  grip: '<circle cx="3" cy="4" r="1.3"/><circle cx="9" cy="4" r="1.3"/><circle cx="3" cy="9" r="1.3"/><circle cx="9" cy="9" r="1.3"/><circle cx="3" cy="14" r="1.3"/><circle cx="9" cy="14" r="1.3"/>',
};

function icon(name, size = 16, opts = {}) {
  const t = document.createElement("template");
  const box = name === "shield" ? "0 0 20 20" : name === "grip" ? "0 0 12 18" : "0 0 16 16";
  const paint = name === "grip" ? 'fill="currentColor"' : `fill="none" stroke="${opts.stroke || "currentColor"}" stroke-width="${opts.width || 1.5}"`;
  const w = name === "grip" ? Math.round(size * 2 / 3) : size;
  t.innerHTML = `<svg width="${w}" height="${size}" viewBox="${box}" ${paint} aria-hidden="true">${ICONS[name]}</svg>`;
  const svg = t.content.firstChild;
  if (opts.class) svg.setAttribute("class", opts.class);
  return svg;
}

function spinner() {
  const t = document.createElement("template");
  t.innerHTML = '<svg class="spin" width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="7.5" stroke="var(--accent-ring)" stroke-width="2.5"/><path d="M10 2.5a7.5 7.5 0 0 1 7.5 7.5" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round"/></svg>';
  return t.content.firstChild;
}

async function api(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Podkop-Manager": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 401) { location.href = "/login"; throw new Error("log in first"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error((data.error && data.error.message) || data.detail || r.statusText);
    e.info = data.error; e.status = r.status;
    throw e;
  }
  return data;
}

let toastTimer;
function toast(msg, bad) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), bad ? 8000 : 3500);
}

// a dialog over the page; returns {close, el}; Escape and a click outside close it
function modal({ title, sub, body, foot, narrow, onclose }) {
  const titleId = `dlg-${Date.now()}`;
  let bg;
  const close = () => { bg.remove(); document.removeEventListener("keydown", onkey); onclose && onclose(); };
  const onkey = (e) => { if (e.key === "Escape") close(); };
  const dialog = h("div", { class: `dialog${narrow ? " narrow" : ""}`, role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h("div", { class: "dialog-head" },
      h("div", { class: "col" }, h("h1", { id: titleId }, title), sub ? h("p", {}, sub) : null),
      h("button", { type: "button", class: "btn icon", "aria-label": "Close", onclick: () => close() }, icon("close"))),
    h("div", { class: "dialog-body" }, body),
    foot ? h("div", { class: "dialog-foot" }, foot) : null);
  bg = h("div", { class: "modal-bg", onmousedown: (e) => { if (e.target === bg) close(); } }, dialog);
  document.body.append(bg);
  document.addEventListener("keydown", onkey);
  const first = dialog.querySelector("input, textarea, select, .dialog-foot button");
  (first || dialog.querySelector("button")).focus();
  return { close, el: dialog };
}

const clone = (x) => JSON.parse(JSON.stringify(x));
const asList = (v) => (v === undefined || v === null || v === "" ? [] : Array.isArray(v) ? v : [v]);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
const sameDay = (a, b) => a.toDateString() === b.toDateString();
const hhmm = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

// 23:58 today, Thu this week, Oct 16 before
function shortWhen(t) {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  if (sameDay(d, now)) return hhmm(d);
  if (now - d < 6 * 86400e3) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// Today 23:59, Yesterday 04:30, Oct 16, 18:12
function longWhen(t) {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (sameDay(d, now)) return `Today ${hhmm(d)}`;
  if (sameDay(d, y)) return `Yesterday ${hhmm(d)}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${hhmm(d)}`;
}

function ago(t) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return longWhen(t);
}

function duration(s) {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

// agent times are UTC like 2026-10-17T21:03:05Z
const agentTime = (s) => (s ? Date.parse(s) / 1000 : null);

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(`podkop-manager.${key}`);
    localStorage.setItem(`podkop-manager.${key}`, value);
  } catch { /* private mode, blocked storage */ }
  return null;
}

// --- links: name, protocol, host --------------------------------------------------

function linkName(link) {
  const i = link.indexOf("#");
  if (i >= 0) {
    try { return decodeURIComponent(link.slice(i + 1).replace(/\+/g, " ")); } catch { return link.slice(i + 1); }
  }
  return linkHost(link);
}

function linkProto(link) {
  const m = /^([a-z0-9]+):\/\//i.exec(link);
  const p = m ? m[1].toLowerCase() : "?";
  return p === "hy2" ? "hysteria2" : p === "shadowsocks" ? "ss" : p;
}

function linkHost(link) {
  let rest = link.replace(/^[a-z0-9]+:\/\//i, "").split("#")[0];
  rest = rest.slice(rest.lastIndexOf("@") + 1);
  return rest.split(/[?/]/)[0];
}

// host:port · transport, security
function linkDetails(link) {
  const q = new URLSearchParams((link.split("#")[0].split("?")[1]) || "");
  const extra = [q.get("type"), q.get("security") && q.get("security") !== "none" ? q.get("security") : null].filter(Boolean);
  return linkHost(link) + (extra.length ? ` · ${extra.join(", ")}` : "");
}

const isLink = (v) => typeof v === "string" && /^[a-z0-9]+:\/\//i.test(v);

// TLS fingerprints REALITY servers since Xray-core v26.9.8 reject (podkop.OLD_FINGERPRINTS)
const OLD_FP = /([?&]fp=)(qq|360|ios|android|edge)(?=[&#]|$)/;
const oldFingerprint = (l) => isLink(l) && /security=reality/.test(l.split("#")[0]) && OLD_FP.test(l.split("#")[0]);
const fixFingerprint = (l) => {
  if (!oldFingerprint(l)) return l;
  const i = l.indexOf("#");
  return i < 0 ? l.replace(OLD_FP, "$1chrome") : l.slice(0, i).replace(OLD_FP, "$1chrome") + l.slice(i);
};

// --- routers ------------------------------------------------------------------------

function routerState(rt) {
  if (!rt.online) return { cls: "bad", label: rt.checked ? "offline" : "connecting" };
  const p = rt.status.podkop;
  if (p.busy) return { cls: "warn", label: "applying" };
  if (p.paused) return { cls: "warn", label: "stopped" };
  if (!p.enabled) return { cls: "warn", label: "disabled" };
  if (!p.running) return { cls: "bad", label: "podkop down" };
  return { cls: "ok", label: "running" };
}

// a change undone on the router in the last day
const recentAlert = (rt) => (rt.alert && Date.now() / 1000 - rt.alert.t < 86400 ? rt.alert : null);
const draftChanges = (rt) => (rt.draft && rt.draft.changes) || 0;

// "running, 4 changes not applied" and its color, for the router list
function routerLine(rt) {
  const st = routerState(rt);
  if (!rt.online) {
    const since = rt.down_since ? ` since ${shortWhen(rt.down_since)}` : "";
    return { cls: "bad", text: `offline${since}${rt.error ? ", " + rt.error : ""}` };
  }
  const a = recentAlert(rt);
  if (a) return { cls: "warn", text: `${st.label}, a change was undone ${ago(a.t)}` };
  const n = draftChanges(rt);
  return { cls: "", text: n ? `${st.label}, ${plural(n, "change")} not applied` : st.label };
}

const routerList = () => Object.values(S.routers).sort((a, b) => a.id - b.id);

// --- routes ------------------------------------------------------------------------------

const SECTIONS = {
  "": { title: "Overview", view: (m, id) => viewOverview(m, id) },
  usage: { title: "Keys and usage", view: (m, id) => viewUsage(m, id) },
  rules: { title: "Routing rules", view: (m, id, sub) => viewRules(m, id, sub) },
  global: { title: "Global settings", view: (m, id) => viewGlobal(m, id) },
  configs: { title: "Configs", view: (m, id) => viewConfigs(m, id) },
  logs: { title: "Logs", view: (m, id) => viewLogs(m, id) },
  history: { title: "History", view: (m, id) => viewHistory(m, id) },
};

function route() {
  const m = /^#\/r\/(\d+)(?:\/([a-z]+)(?:\/([A-Za-z0-9_]+))?)?$/.exec(location.hash);
  if (m) return { rid: +m[1], section: m[2] || "", sub: m[3] || "" };
  if (location.hash === "#/settings") return { settings: true };
  return { home: true };
}

const lastRouter = () => {
  const id = +store("router");
  return S.routers[id] ? id : (routerList()[0] || {}).id;
};

// A page is built off screen and swapped in when its data is there, so the
// old page stays until then (dimmed after a moment) instead of a blank one.
let renders = 0;

async function render() {
  const main = document.getElementById("main");
  if (S.view && S.view.leave) S.view.leave();
  S.view = S.shown = null;
  const r = route();
  if (r.home) {
    const id = lastRouter();
    location.replace(id ? `#/r/${id}` : "#/settings");
    return;
  }
  if (r.rid && !S.routers[r.rid]) { location.replace("#/"); return; }
  // the Servers and Servers probe pages are gone: their old links open the rules
  if (r.rid && ["servers", "probe"].includes(r.section)) { location.replace(`#/r/${r.rid}/rules`); return; }
  if (r.rid) store("router", String(r.rid));
  S.from = S.at; S.at = `${r.rid}/${r.section}`;      // the page before this one (see workFor)
  S.rid = r.rid || lastRouter();
  drawSide();
  const sec = SECTIONS[r.section];
  document.title = `${r.settings ? "Settings" : sec ? sec.title : "Not found"} · podkop-manager`;
  const token = ++renders;
  const slot = h("div");
  const wait = setTimeout(() => {
    if (token !== renders) return;       // a newer page took over
    main.classList.add("busy");
    if (!main.firstChild) put(main, h("div", { class: "page" }, h("p", { class: "loading" }, "Loading…")));
  }, 250);
  try {
    if (r.settings) await viewSettings(slot);
    else if (sec) await sec.view(slot, r.rid, r.sub);
    else put(slot, h("div", { class: "page" }, h("p", {}, "Not found")));
  } catch (e) {
    put(slot, h("div", { class: "page" }, h("div", { class: "card pad" }, h("p", { class: "bad-text" }, e.message))));
  }
  if (token !== renders) {
    // a newer page was asked for meanwhile: drop this one
    clearTimeout(wait);
    if (S.view !== S.shown) { if (S.view && S.view.leave) S.view.leave(); S.view = S.shown; }
    return;
  }
  clearTimeout(wait);
  main.classList.remove("busy");
  main.replaceChildren(...slot.childNodes);
  S.shown = S.view;
  window.scrollTo(0, 0);
}

// --- sidebar ---------------------------------------------------------------------------

let switchOpen = false;

function drawSide() {
  const side = document.getElementById("side");
  const r = route();
  const rt = S.routers[S.rid];
  const cur = (section) => (r.rid && r.section === section ? "page" : null);
  const link = (section, label, ico, extra) => h("a", { href: `#/r/${rt.id}${section ? "/" + section : ""}`, "aria-current": cur(section) },
    ico ? icon(ico) : null, label, extra);
  const n = rt ? draftChanges(rt) : 0;
  put(side,
    h("div", { class: "brand" }, h("b", {}, "podkop-manager"),
      h("span", { class: `live${S.live ? " on" : ""}`, title: S.live ? "live updates on" : "reconnecting" },
        h("span", { class: "dot sm" }), S.live ? "live" : "offline")),
    rt ? switcher(rt) : null,
    rt ? h("div", { class: "nav" },
      link("", "Overview", "overview"),
      link("usage", "Keys and usage", "usage", rt.usage && rt.usage.pct !== null && rt.usage.pct !== undefined
        ? h("span", { class: `count${rt.usage.alerts.length ? " warn" : ""}`, title: "Highest daily limit used" }, `${Math.round(rt.usage.pct)}%`) : null),
      h("a", { href: `#/r/${rt.id}/rules` }, icon("routing"), "Routing",
        n ? h("span", { class: "count", title: `${plural(n, "change")} not applied` }, n) : null),
      h("a", { href: `#/r/${rt.id}/rules`, class: "sub", "aria-current": cur("rules") }, "Rules"),
      h("a", { href: `#/r/${rt.id}/global`, class: "sub", "aria-current": cur("global") }, "Global settings"),
      h("a", { href: `#/r/${rt.id}/configs`, class: "sub", "aria-current": cur("configs") }, "Configs"),
      link("logs", "Logs", "logs"),
      link("history", "History", "history")) : null,
    h("div", { class: "nav nav-bottom" },
      h("a", { href: "#/settings", "aria-current": r.settings ? "page" : null }, icon("settings"), "Settings")));
}

function switcher(rt) {
  const st = routerState(rt);
  const others = routerList().filter((x) => x.id !== rt.id);
  const alerts = others.map((x) => {
    if (!x.online) return { x, text: `${x.name} is offline` };
    const a = recentAlert(x);
    return a ? { x, text: `${x.name}: a change was undone ${ago(a.t)}` } : null;
  }).filter(Boolean);
  const btn = h("button", { type: "button", class: "switcher", "aria-expanded": String(switchOpen), "aria-controls": "router-list",
    onclick: (e) => { e.stopPropagation(); switchOpen = !switchOpen; drawSide(); } },
    h("span", { class: `dot ${st.cls}` }),
    h("span", { class: "who" }, h("b", {}, rt.name), h("span", {}, `${rt.host}${rt.port !== 22 ? ":" + rt.port : ""} · ${st.label}`)),
    icon("updown", 14), h("span", { class: "sr" }, "Switch router"));
  const pop = switchOpen ? h("div", { class: "pop", id: "router-list", role: "listbox", "aria-label": "Routers" },
    routerList().map((x) => {
      const line = routerLine(x);
      return h("a", { href: `#/r/${x.id}${route().section ? "/" + route().section : ""}`, class: "router", role: "option",
        "aria-selected": String(x.id === rt.id), onclick: () => { switchOpen = false; } },
      h("span", { class: `dot ${routerState(x).cls}` }),
      h("span", { class: "who" }, h("b", {}, x.name), h("span", { class: line.cls }, line.text)),
      x.id === rt.id ? icon("check", 16, { stroke: "var(--accent)", width: 2 }) : null);
    }),
    h("hr"),
    h("a", { href: "#/settings", onclick: () => { switchOpen = false; } }, icon("plus", 15), "Add router"),
    h("a", { href: "#/settings", onclick: () => { switchOpen = false; } }, icon("settings", 15), "Manage routers")) : null;
  return h("div", { class: "switch" }, btn, pop,
    alerts.map((a) => h("a", { href: `#/r/${a.x.id}`, class: "alert-line" }, h("span", { class: "dot sm bad" }), a.text)));
}

document.addEventListener("click", (e) => {
  if (switchOpen && !e.target.closest(".switch")) { switchOpen = false; drawSide(); }
});
document.addEventListener("keydown", (e) => {
  if (switchOpen && e.key === "Escape") { switchOpen = false; drawSide(); document.querySelector(".switcher").focus(); }
});

// --- page parts ----------------------------------------------------------------------------

function pageHead(title, sub, ...actions) {
  return h("header", { class: "head" },
    h("div", { class: "title" }, h("h1", {}, title), sub ? h("p", {}, sub) : null),
    actions.length ? h("div", { class: "row" }, actions) : null);
}

function getRouter(id) {
  const rt = S.routers[id];
  if (!rt) throw new Error("no such router");
  return rt;
}

// seg([["udp", "UDP"], ...], value, onchange): a segmented control
function seg(options, value, onchange, { small, label, disabled } = {}) {
  return h("div", { class: `seg${small ? " sm" : ""}`, role: "group", "aria-label": label || null },
    options.map(([v, text]) => h("button", { type: "button", "aria-pressed": String(v === value), disabled,
      onclick: () => { if (v !== value) onchange(v); } }, text)));
}

// --- podkop's settings: rules, diffs, labels -----------------------------------------------

const INTERNAL = () => new Set((S.schema && S.schema.internal) || ["shutdown_correctly"]);
const sectionsOf = (config) => ((config && config.sections) || []);
const rulesOf = (config) => sectionsOf(config).filter((s) => s.type === "section");
const bySection = (config) => new Map(sectionsOf(config).map((s) => [s.name, s]));
const same = (a, b) => JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);

// what changes from a to b, entry by entry like the manager's podkop.diff
function configDiff(a, b) {
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

const FRIENDLY = {
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

function optionDef(section, opt) {
  if (!S.schema) return null;
  return (section === "settings" ? S.schema.settings : S.schema.section).find((d) => d.name === opt) || null;
}

const optionLabel = (section, opt) => FRIENDLY[opt] || (optionDef(section, opt) || {}).label || opt;

// what a rule matches, in a few words
function ruleMatches(sec) {
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
function ruleDest(sec) {
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

const destChip = (sec) => { const [text, cls] = ruleDest(sec); return h("span", { class: `dest ${cls}` }, text); };

// the server a rule uses now: {text, mode}
function ruleNow(rt, sec, devices) {
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
  const ut = groups.find((x) => x.name === `${sec.name}-urltest-out`);
  const tag = g.now === `${sec.name}-urltest-out` && ut ? ut.now : g.now;
  const server = (rt.proxies.servers || []).find((x) => x.name === tag);
  const pinned = ((rt.status && rt.status.pins) || []).some((p) => p.section === sec.name);
  const name = (rt.names || {})[tag] || tag;
  return { text: server && server.delay ? `${name}, ${server.delay} ms` : name,
    mode: pinned ? "pinned" : o.proxy_config_type === "urltest" ? "auto" : "" };
}

// where a rule's traffic leaves to the internet: "DE, Germany · 203.0.113.5"
function ruleExit(rt, sec) {
  const e = (rt.exits || {})[sec.name];
  if (!e) return null;
  if (e.error) return h("span", { class: "small muted", title: e.error }, "Exit IP unknown");
  const place = [e.city, e.org].filter(Boolean).join(", ");
  return h("span", { class: "small muted", title: place || null },
    `${e.country ? `${e.country}, ${countryName(e.country)} · ` : ""}${e.ip}`);
}

// the version the router runs now, by its fingerprint
const runningVersion = (rt, versions) => (rt.status
  ? (versions || []).find((v) => v.fingerprint_after && v.fingerprint_after === rt.status.fingerprint) : null);

// "claude.ai in main, new rule banks_direct" from a diff
function diffSummary(diff) {
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

const EVENT_TEXT = {
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
  pinned: (d) => `${d.section}: pinned ${serverName(d.section, d.server)}`,
  pin_cleared: (d) => `${d.section}: back to automatic`,
  pin_restored: (d) => `${d.section}: pin ${serverName(d.section, d.server)} set again`,
  pin_dropped: (d) => `${d.section}: pin removed, ${d.reason}`,
  agent_started: (d) => `podkop-agent ${d.version} started`,
  service: (d) => `podkop ${d.action}`,
  probe_started: () => "Probe started",
  probe_done: (d) => (d.exit === 0 ? "Probe finished" : `Probe failed (exit ${d.exit})`),
  probe_apply_failed: (d) => `Probe result not applied: ${d.message}`,
  probe_not_started: (d) => `Nightly probe not started: ${d.message}`,
  auto_rule_empty: (d) => `${d.section}: no server matched after the probe, the rule keeps its last servers`,
  usage_threshold: (d) => `${d.key} used ${Math.round(d.pct)}% of today's traffic${d.action === "avoid" ? ", rules move to other keys" : ""}`,
  usage_move_failed: (d) => `Rules not moved for the traffic limit: ${d.message}`,
};
const EVENT_DOT = {
  applied: "ok", podkop_up: "ok", reverted: "bad", revert_failed: "bad", apply_interrupted: "bad", podkop_down: "bad", recovery_failed: "bad",
  recovery_gave_up: "bad", probe_apply_failed: "bad", probe_not_started: "bad", recovered: "warn", pin_dropped: "warn",
  auto_rule_empty: "warn", usage_threshold: "warn", usage_move_failed: "bad",
  pinned: "accent", pin_cleared: "accent", pin_restored: "accent",
};

function serverName(section, tag) {
  const rt = S.routers[S.rid];
  return (rt && rt.names && rt.names[tag]) || tag;
}

const eventText = (type, d) => (EVENT_TEXT[type] ? EVENT_TEXT[type](d || {}) : `${type} ${JSON.stringify(d || {})}`);

// --- overview -------------------------------------------------------------------------------------

async function viewOverview(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let data = {};
  const load = async () => {
    const [config, events, probe, versions, draft] = await Promise.allSettled([
      api("GET", `/api/routers/${id}/config`), api("GET", `/api/routers/${id}/events?limit=12`),
      api("GET", `/api/routers/${id}/probe`), api("GET", `/api/routers/${id}/versions?limit=50`),
      api("GET", `/api/routers/${id}/draft`)]);
    const v = (r) => (r.status === "fulfilled" ? r.value : null);
    data = { config: v(config), events: v(events) || [], probe: v(probe), versions: v(versions) || [], draft: v(draft) };
    data.auto = Object.fromEntries(((data.probe && data.probe.auto_rules) || []).map((r) => [r.section, r]));
  };

  const service = (action, label, cls) => h("button", { type: "button", class: `btn ${cls || ""}`, onclick: async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { await api("POST", `/api/routers/${id}/service`, { action }); toast(`podkop: ${action} done`); }
    catch (err) { toast(err.message, true); }
    b.disabled = false;
  } }, label);

  const draw = () => {
    const rt = S.routers[id];
    if (!rt) return;
    const st = rt.status, pk = st && st.podkop;
    const cfg = data.config && data.config.config;
    const running = runningVersion(rt, data.versions);
    put(page,
      pageHead("Overview", null,
        h("button", { type: "button", class: "btn", onclick: async (e) => {
          const b = e.currentTarget; b.disabled = true;
          try { await api("POST", `/api/routers/${id}/refresh`); await load(); draw(); } catch (err) { toast(err.message, true); }
          b.disabled = false;
        } }, icon("refresh", 15), "Refresh"),
        pk && pk.running ? service("restart", "Restart podkop") : pk ? service("start", "Start podkop", "primary") : null,
        pk && pk.running ? service("stop", "Stop", "danger") : null),
      !rt.online ? h("div", { class: "banner bad" }, h("span", { class: "dot bad" }),
        h("span", { class: "grow" }, h("b", {}, "The router does not answer. "), rt.error || ""),
        h("span", { class: "muted small" }, rt.down_since ? `since ${shortWhen(rt.down_since)}` : "")) : null,
      waiting(rt, cfg, running),
      st ? statusStrip(st) : null,
      h("div", { class: "split" },
        h("div", { class: "stack", style: "flex: 2 1 600px; min-width: 0" },
          nowRouting(rt, cfg),
          nightly(rt)),
        eventsCard(rt)));
  };

  const applyProbe = async () => {
    try {
      const p = await api("POST", `/api/routers/${id}/probe/preview`);
      openApply(id, { base: p.fingerprint, config: p.config, note: "Probe result", trigger: "probe",
        done: async () => { await load(); draw(); } });
    } catch (e) { toast(e.message, true); }
  };

  // things that wait for the user: an unapplied draft, a probe result not in podkop yet
  const waiting = (rt, cfg, running) => {
    const notes = [];
    if (data.draft && cfg && data.draft.changes) {
      const d = configDiff(cfg, data.draft.config);
      notes.push(h("div", { class: "note" }, h("span", { class: "dot accent" }),
        h("div", { class: "col" }, h("span", {}, `${plural(d.length, "routing change")} ${d.length === 1 ? "is" : "are"} not applied`),
          h("span", {}, `${diffSummary(d)}.${running ? ` The router still runs #${running.id}.` : ""}`)),
        h("a", { class: "alink", href: `#/r/${id}/rules` }, "Open changes")));
    }
    const ps = data.probe && data.probe.status;
    if (ps && ps.state === "done" && probeIsNew(data.probe)) {
      const rows = ps.rows || [];
      const usable = rows.filter((r) => ["GOOD", "OK", "FLAKY"].includes(r.verdict)).length;
      const rules = data.probe.auto_rules.filter((r) => r.differs).map((r) => `${r.section} ${r.picked} and ${r.picked_fallback} fallback`);
      notes.push(h("div", { class: "note" }, h("span", { class: "dot accent" }),
        h("div", { class: "col" }, h("span", {}, `Probe from ${shortWhen(agentTime(ps.finished))} has new servers`),
          h("span", {}, `${rules.join(", ")}. ${usable} of ${rows.length} servers usable.`)),
        h("button", { type: "button", class: "btn sm", onclick: applyProbe }, "Review and apply")));
    }
    const st = rt.status;
    if (st && st.engine === false) {
      notes.push(h("div", { class: "note" }, h("span", { class: "dot warn" }),
        h("div", { class: "col" }, h("span", {}, "podkop-engine is not installed"),
          h("span", {}, "The probe can't test servers on this router. Reading and applying settings work.")),
        h("a", { class: "alink", href: "https://github.com/FiyeroT/podkop-engine", target: "_blank", rel: "noopener" }, "podkop-engine")));
    }
    if (st && st.probe && st.probe.cron) {
      const [mm, hh] = st.probe.cron.split(/\s+/);
      const at = /^\d+$/.test(mm) && /^\d+$/.test(hh) ? ` at ${hh.padStart(2, "0")}:${mm.padStart(2, "0")}` : "";
      notes.push(h("div", { class: "note" }, h("span", { class: "dot warn" }),
        h("div", { class: "col" }, h("span", {}, `podkop-probe also runs by itself every night${at}`),
          h("span", {}, "Set up on the router before the manager, it changes the servers without it. The manager can run it instead, at the same time.")),
        h("button", { type: "button", class: "btn sm", onclick: async (e) => {
          e.currentTarget.disabled = true;
          try { await api("POST", `/api/routers/${id}/probe/take-over`); toast("The manager runs the nightly probe now"); await load(); draw(); }
          catch (err) { toast(err.message, true); e.currentTarget.disabled = false; }
        } }, "Let the manager run it")));
    }
    for (const a of (rt.usage && rt.usage.alerts) || []) {
      const eta = a.forecast && a.forecast.eta;
      notes.push(h("div", { class: "note" }, h("span", { class: "dot warn" }),
        h("div", { class: "col" }, h("span", {}, `${a.label} has used ${Math.round(a.pct)}% of today's traffic`),
          h("span", {}, `${gb(a.used)} of ${gb(a.limit)}.${eta ? ` At this rate it runs out around ${hhmm(new Date(eta * 1000))}.` : ""}${
            a.action === "avoid" ? ` Rules move to other keys at ${a.threshold}%.` : ""}`)),
        h("a", { class: "alink", href: `#/r/${id}/usage` }, "See usage")));
    }
    return notes.length ? h("section", { class: "card clip waiting", "aria-label": "Waiting for you" }, notes) : null;
  };

  const statusStrip = (st) => {
    const s = routerState(S.routers[id]);
    const pk = st.podkop;
    const days = Math.floor(st.uptime / 86400), hours = Math.floor((st.uptime % 86400) / 3600);
    return h("section", { class: "card status-strip", "aria-label": "Status" },
      h("div", {}, h("span", {}, "podkop"), h("span", {}, h("span", { class: `dot sm ${s.cls}` }), `${pk.version || "?"}, ${s.label}`)),
      h("div", {}, h("span", {}, "sing-box"), h("span", {}, st.singbox.version || "not found")),
      h("div", {}, h("span", {}, "podkop-agent"),
        h("span", {}, `${st.agent.version}, ${pk.paused || !pk.enabled ? "watchdog paused" : "watchdog on"}`)),
      h("div", {}, h("span", {}, "Router"),
        h("span", {}, [st.router.model, (st.router.openwrt || "").replace(/ r\d+-\w+$/, ""), `up ${days ? days + " d" : hours + " h"}`].filter(Boolean).join(", "))),
      st.failsafe && !pk.running ? h("div", { style: "grid-column: 1 / -1" }, h("span", {}, `Failsafe, ${st.failsafe.time}`),
        h("span", { class: "bad-text" }, st.failsafe.message)) : null);
  };

  const unpin = async (sec) => {
    try { await api("POST", `/api/routers/${id}/pin`, { section: sec, server: "auto" }); toast(`${sec}: back to automatic`); }
    catch (e) { toast(e.message, true); }
  };

  const nowRouting = (rt, cfg) => {
    const rules = rulesOf(cfg);
    return h("section", { class: "card clip", "aria-labelledby": "now-routing" },
      h("div", { class: "card-head", style: "align-items: baseline" },
        h("div", { class: "col" }, h("h2", { id: "now-routing" }, "Now routing"),
          h("span", { class: "small muted" }, "First match wins")),
        h("a", { class: "alink", href: `#/r/${id}/rules` }, "Edit rules")),
      !cfg ? h("p", { class: "empty" }, rt.online ? "Could not read podkop's settings." : "Shown when the router answers.")
        : h("div", { class: "scroll-x" }, h("table", { class: "wide" },
          h("tr", {}, h("th", { scope: "col", style: "width: 24px" }, "#"), h("th", { scope: "col" }, "Rule"),
            h("th", { scope: "col" }, "Matches"), h("th", { scope: "col" }, "Goes to"), h("th", { scope: "col" }, "Now")),
          rules.map((sec, i) => {
            const now = ruleNow(rt, sec, data.config.devices);
            return h("tr", {},
              h("td", { class: "muted num" }, i + 1),
              h("td", { class: "mono", style: "font-size: 13px; font-weight: 500" }, sec.name),
              h("td", { style: "color: var(--text-2)" }, ruleMatches(sec)),
              h("td", {}, data.auto[sec.name] && (sec.options || {}).connection_type === "proxy"
                ? h("span", { class: "dest" }, `Proxy, from probe${data.auto[sec.name].countries.length ? ", " + countriesText(data.auto[sec.name].countries) : ""}`) : destChip(sec)),
              h("td", {}, h("div", { class: "col" },
                h("span", { class: "row", style: "gap: 8px" }, h("span", { class: now.bad ? "bad-text" : "" }, now.text),
                  now.mode ? h("span", { class: "mode" }, now.mode) : null,
                  now.mode === "pinned" ? h("button", { type: "button", class: "link-btn xs", onclick: () => unpin(sec.name) }, "Unpin") : null),
                ruleExit(rt, sec))));
          }),
          h("tr", {}, h("td", {}), h("td", { class: "nowrap", style: "color: var(--text-2)" }, "Everything else"),
            h("td", { class: "muted" }, "no rule matched"), h("td", {}, h("span", { class: "dest direct" }, "Direct")),
            h("td", { class: "muted" }, "not through podkop")))));
  };

  const nightly = () => {
    const p = data.probe;
    const ps = p && p.settings;
    if (!ps) return null;
    const last = (p.runs || []).find((r) => r.trigger === "schedule");
    let lastText = "";
    if (last) {
      const day = longWhen(last.started).replace(/ \d\d:\d\d$/, "").replace(/, \d\d:\d\d$/, "");
      lastText = last.state !== "done" ? `Last nightly run ${day.toLowerCase()}: ${last.state}.`
        : last.version_id ? `Last nightly run ${day.toLowerCase()} applied as #${last.version_id}.`
          : `Last nightly run ${day.toLowerCase()} finished, not applied.`;
    }
    let next = "";
    if (ps.schedule) {
      const [hh, mm] = ps.schedule.split(":").map(Number);
      const t = new Date(); t.setHours(hh, mm, 0, 0);
      if (t <= new Date()) t.setDate(t.getDate() + 1);
      next = `Next run in ${duration((t - new Date()) / 1000)}.`;
    }
    return h("section", { class: "card note", "aria-labelledby": "nightly", style: "padding: 16px 18px" },
      h("div", { class: "col", style: "flex: 1 1 320px; gap: 3px" },
        h("h2", { id: "nightly", class: "h2" }, "Nightly probe"),
        h("span", { style: "font-size: 13.5px; color: var(--text-2); font-weight: 400" }, ps.schedule
          ? `Every night at ${ps.schedule}, ${ps.auto_apply ? "applied by itself if podkop starts with it" : "results wait for you to apply them"}.`
          : "Off"),
        lastText || next ? h("span", { class: "small muted" }, [lastText, next].filter(Boolean).join(" ")) : null),
      h("a", { class: "btn", href: `#/r/${id}/rules${(p.auto_rules || []).length ? `/${p.auto_rules[0].section}` : ""}` }, "Schedule"));
  };

  const eventsCard = () => h("aside", { class: "card events", "aria-label": "Events", style: "flex: 1 1 300px; min-width: 0" },
    h("div", { class: "row", style: "justify-content: space-between; margin-bottom: 8px" },
      h("h2", {}, "Events"), h("a", { class: "small", href: `#/r/${id}/history` }, "History")),
    data.events.length ? data.events.map((e) => h("div", { class: "event" },
      h("span", { class: "num" }, shortWhen(e.t)), h("span", { class: `dot ${EVENT_DOT[e.type] || "gray"}` }),
      h("span", {}, eventText(e.type, e.data))))
      : h("p", { class: "muted small" }, "Nothing yet."));

  await load();
  draw();
  let timer;
  const later = () => { clearTimeout(timer); timer = setTimeout(async () => { await load(); draw(); }, 800); };
  S.view = {
    leave: () => clearTimeout(timer),
    onRouter: (r) => {
      if (r.id !== id) return;
      if (r.status && (data.config ? r.status.fingerprint !== data.config.fingerprint : r.online)) later(); else draw();
    },
    onEvent: (m) => m.router_id === id && later(),
    onProbe: (m) => m.router_id === id && m.state !== "running" && later(),
  };
}

// the last probe run picked other servers than some rule has now
const probeIsNew = (probe) => (probe.auto_rules || []).some((r) => r.differs);

// --- logs ------------------------------------------------------------------------------------------

const LOG_SOURCES = [["podkop", "podkop"], ["sing-box", "sing-box"], ["engine", "engine"], ["agent", "agent"]];
const LOG_LEVELS = { all: [], warn: ["warn", "error"], error: ["error"] };

async function viewLogs(main, id) {
  const rt = getRouter(id);
  const f = { q: "", sources: new Set(), level: "all", follow: true, buffer: null };
  const box = h("div", { class: "logbox", tabindex: "0", "aria-label": "Log lines" });
  const lines = h("div", { class: "loglines" });
  box.append(lines);
  const foot = h("div", { class: "card-foot" });
  const toolbar = h("div", { class: "row", role: "toolbar", "aria-label": "Log filters", style: "gap: 10px 12px" });
  const page = h("div", { class: "page" },
    pageHead("Logs"),
    toolbar,
    h("section", { class: "card clip", "aria-label": "Log lines" }, box, foot));
  put(main, page);

  const lineEl = (r) => h("div", { class: `logline ${r.level === "error" || r.level === "warn" ? r.level : ""}` },
    h("span", {}, r.time || (r.t ? new Date(r.t * 1000).toLocaleTimeString([], { hourCycle: "h23" }) : "")),
    h("span", {}, r.source || "–"), h("span", {}, r.level), h("span", {}, r.msg));
  const matches = (r) => (!f.sources.size || f.sources.has(r.source))
    && (f.level === "all" || LOG_LEVELS[f.level].includes(r.level))
    && (!f.q || r.line.toLowerCase().includes(f.q.toLowerCase()));
  const toBottom = () => { box.scrollTop = box.scrollHeight; };

  const fill = (rows) => {
    put(lines, rows.length ? rows.map(lineEl) : h("p", { class: "empty" }, f.q || f.sources.size || f.level !== "all"
      ? "No line matches these filters." : "No lines stored yet. New lines arrive while the router is connected."));
    toBottom();
  };
  const load = async () => {
    const qs = new URLSearchParams({ limit: "1000", q: f.q, source: [...f.sources].join(","), level: LOG_LEVELS[f.level].join(",") });
    fill(await api("GET", `/api/routers/${id}/logs?${qs}`));
  };

  const drawFoot = () => put(foot,
    f.buffer ? h("span", {}, `The router's own log buffer, read at ${hhmm(new Date(f.buffer * 1000))}. Filters apply to stored lines.`)
      : h("span", {}, "Newest at the bottom"),
    f.buffer ? h("button", { type: "button", class: "link-btn", onclick: () => { f.buffer = null; drawAll(); load(); } }, "Back to stored lines")
      : h("span", { class: `live${f.follow && S.live ? " on" : ""}` }, h("span", { class: "dot sm" }),
        f.follow ? (S.live ? "following" : "reconnecting") : "paused"));

  let qTimer;
  const drawTools = () => put(toolbar,
    h("label", { class: "search" }, icon("search", 15, { width: 1.6 }), h("span", { class: "sr" }, "Search logs"),
      h("input", { placeholder: "Search, e.g. main-4-out or timeout", value: f.q, disabled: !!f.buffer, oninput: (e) => {
        f.q = e.target.value.trim(); clearTimeout(qTimer); qTimer = setTimeout(load, 300);
      } })),
    h("div", { class: "row", style: "gap: 6px" },
      h("button", { type: "button", class: "toggle", "aria-pressed": String(!f.sources.size), disabled: !!f.buffer,
        onclick: () => { f.sources.clear(); drawTools(); load(); } }, "All sources"),
      LOG_SOURCES.map(([v, label]) => h("button", { type: "button", class: "toggle", "aria-pressed": String(f.sources.has(v)),
        disabled: !!f.buffer, onclick: () => {
          if (f.sources.has(v)) f.sources.delete(v); else f.sources.add(v);
          if (f.sources.size === LOG_SOURCES.length) f.sources.clear();
          drawTools(); load();
        } }, label))),
    seg([["all", "All"], ["warn", "Warnings"], ["error", "Errors"]], f.level, (v) => { f.level = v; drawTools(); load(); },
      { label: "Level", disabled: !!f.buffer }),
    h("div", { class: "row", style: "margin-left: auto; gap: 12px" },
      h("label", { class: "check inline" }, h("input", { type: "checkbox", checked: f.follow, onchange: (e) => {
        f.follow = e.target.checked; drawFoot(); if (f.follow) toBottom();
      } }), "Follow live"),
      h("button", { type: "button", class: "btn sm", onclick: async (e) => {
        const b = e.currentTarget; b.disabled = true;
        try {
          const rows = await api("GET", `/api/routers/${id}/logs/recent?lines=500`);
          f.buffer = Date.now() / 1000; drawAll(); fill(rows);
        } catch (err) { toast(err.message, true); }
        b.disabled = false;
      } }, "Load router buffer")));
  const drawAll = () => { drawTools(); drawFoot(); };

  drawAll();
  await load();
  S.view = {
    onLog: (m) => {
      if (m.router_id !== id || f.buffer || !matches(m)) return;
      if (lines.firstChild && lines.firstChild.classList.contains("empty")) lines.firstChild.remove();
      lines.append(lineEl(m));
      if (lines.childNodes.length > 3000) lines.firstChild.remove();
      if (f.follow) toBottom();
    },
    onLive: drawFoot,
  };
}

// --- changes, as shown in History and the apply dialog ----------------------------------------------

const TEXT_LISTS = new Set(["user_domains_text", "user_subnets_text"]);
const shortValue = (v) => (isLink(v) ? linkName(v) : String(v).length > 60 ? String(v).slice(0, 57) + "…" : String(v));

// a few chips, then "and N more"
function chipList(items, cls) {
  const box = h("span", { class: "vals" });
  const draw = (all) => put(box, (all ? items : items.slice(0, 6)).map((x) => h("span", { class: `${cls}${isLink(x) ? " plain" : ""}` },
    cls === "add" ? `+ ${shortValue(x)}` : shortValue(x))),
  !all && items.length > 6 ? h("button", { type: "button", class: "link-btn", onclick: () => draw(true) }, `and ${items.length - 6} more`) : null);
  draw(false);
  return box;
}

// option values as the editor names them
const VALUE_LABELS = {
  connection_type: { proxy: "Proxy", vpn: "VPN", block: "Block", exclusion: "Direct" },
  proxy_config_type: { url: "One link", selector: "Selector", urltest: "Fastest (URLTest)", outbound: "sing-box JSON" },
  user_domain_list_type: { disabled: "Off", dynamic: "List", text: "Text" },
  user_subnet_list_type: { disabled: "Off", dynamic: "List", text: "Text" },
};

// the old and new value of one option
function changeValue(opt, oldV, newV) {
  const names = VALUE_LABELS[opt];
  if (names) { oldV = oldV === undefined ? oldV : names[oldV] || oldV; newV = newV === undefined ? newV : names[newV] || newV; }
  if (Array.isArray(oldV) || Array.isArray(newV) || TEXT_LISTS.has(opt)) {
    const split = (v) => (TEXT_LISTS.has(opt) && !Array.isArray(v) ? String(v || "").split(/[\s,]+/).filter(Boolean) : asList(v));
    const o = split(oldV), n = split(newV);
    const add = n.filter((x) => !o.includes(x)), del = o.filter((x) => !n.includes(x));
    if (!add.length && !del.length) return h("span", { class: "muted" }, "same items, new order");
    return h("span", { class: "vals" }, add.length ? chipList(add, "add") : null, del.length ? chipList(del, "del") : null);
  }
  if (opt === "outbound_json") return h("span", {}, "changed");
  if (oldV === undefined || oldV === null) return h("span", { class: "vals" }, h("span", { class: "add" }, shortValue(newV)));
  if (newV === undefined || newV === null) return h("span", { class: "vals" }, h("span", { class: "del" }, shortValue(oldV)), h("span", { class: "muted xs" }, "back to default"));
  return h("span", { class: "vals" }, h("span", { class: "del" }, shortValue(oldV)), icon("arrow", 14, { stroke: "var(--muted)" }), h("span", { class: "add" }, shortValue(newV)));
}

// "Direct, 12 domains" for a rule added in a change
function newRuleSummary(options) {
  const sec = { name: "", options };
  const m = ruleMatches(sec);
  return `${ruleDest(sec)[0]}${m === "nothing yet" ? "" : ", " + m}`;
}

// [section, label, value] rows for a diff; config (optional) gives rule positions
function changeRows(diff, config) {
  const pos = (n) => rulesOf(config).findIndex((s) => s.name === n) + 1;
  return diff.map((d) => {
    if (d.change === "added") {
      return [d.section, d.type === "section" ? "New rule" : `New ${d.type}`,
        h("span", {}, d.type === "section" ? newRuleSummary(d.options) + (config ? `, rule ${pos(d.section)}` : "") : "")];
    }
    if (d.change === "removed") return [d.section, "Rule removed", h("span", {})];
    if (d.change === "order") return ["", "Rule order", h("span", { class: "mono xs" }, d.new.filter((n) => n !== "settings").join(" → "))];
    return [d.section, optionLabel(d.section, d.option), changeValue(d.option, d.old, d.new)];
  });
}

// --- history -------------------------------------------------------------------------------------------

const VERSION_STATE = {
  applied: ["applied", "ok"], saved: ["saved", "ok"], unchanged: ["no change", ""], reverted: ["undone", "bad"],
  rejected: ["rejected", "warn"], failed: ["failed", "bad"], pending: ["applying", "accent"], running: ["applying", "accent"],
  unknown: ["unknown", ""],
};

function versionWho(v) {
  if (v.trigger === "probe") return /nightly/i.test(v.note || "") ? "nightly probe" : "probe result, by you";
  if (v.trigger === "usage") return "traffic limit";
  return "by you";
}

function versionMeta(v, running) {
  const parts = [longWhen(v.t), versionWho(v)];
  if (v.diff.length) parts.push(plural(v.diff.length, "change"));
  if (running) parts.push("running now");
  if (v.state === "reverted") parts.push(`the router put the old settings back${v.seconds ? ` after ${v.seconds} s` : ""}`);
  if (v.state === "rejected") parts.push("stopped before podkop was touched");
  return parts.join(" · ");
}

const problemText = (p) => `${p.section}${p.option ? " / " + p.option : ""}${p.index !== undefined && p.index !== null ? " #" + (p.index + 1) : ""}: ${p.message}`;

function versionReason(v) {
  if (v.state === "reverted") return ["podkop did not start with these settings", v.reason || v.message, ""];
  if (v.state === "failed") return ["The change failed", v.message, ""];
  if (v.state === "rejected") {
    if (v.problems && v.problems.length) return ["Check failed", v.problems.map(problemText).join("\n"), "warn"];
    return [v.code === "config_changed" ? "podkop's settings changed on the router first" : "Not applied", v.message, "warn"];
  }
  return null;
}

async function viewHistory(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let list = [], more = true, filter = "all";
  const open = new Set();
  const load = async (older) => {
    const before = older && list.length ? `&before=${list[list.length - 1].id}` : "";
    const got = await api("GET", `/api/routers/${id}/versions?limit=30${before}`);
    list = older ? list.concat(got) : got;
    more = got.length === 30;
  };

  const draw = () => {
    const rt = S.routers[id];
    if (!rt) return;
    const runningId = (runningVersion(rt, list) || {}).id;
    const shown = list.filter((v) => filter === "all" || (filter === "ok" ? ["applied", "saved"].includes(v.state)
      : ["reverted", "rejected", "failed"].includes(v.state)));
    put(page,
      pageHead("History"),
      h("div", { class: "row" }, seg([["all", "All"], ["ok", "Applied"], ["bad", "Undone or rejected"]], filter,
        (v) => { filter = v; draw(); }, { label: "Show" })),
      shown.length ? h("ol", { class: "versions" }, shown.map((v) => {
        const [label, cls] = VERSION_STATE[v.state] || [v.state, ""];
        const reason = versionReason(v);
        const isOpen = open.has(v.id);
        const rows = isOpen ? changeRows(v.diff) : [];
        return h("li", { class: "card clip" },
          h("div", { class: "vhead" },
            h("span", { class: "vid" }, `#${v.id}`),
            h("span", { class: `pill ${cls}` }, label),
            h("div", { class: "col" }, h("span", {}, v.note || "Change from the web interface"), h("span", {}, versionMeta(v, v.id === runningId))),
            h("div", { class: "row", style: "gap: 6px" },
              v.diff.length ? h("button", { type: "button", class: "btn ghost sm", "aria-expanded": String(isOpen), onclick: () => {
                if (isOpen) open.delete(v.id); else open.add(v.id); draw();
              } }, isOpen ? "Hide changes" : "Show changes", icon(isOpen ? "up" : "down", 14, { width: 1.6 })) : null,
              ["applied", "saved"].includes(v.state) && v.id !== runningId
                ? h("button", { type: "button", class: "btn sm", onclick: () => restore(v.id) }, "Restore") : null)),
          reason ? h("div", { class: `reason ${reason[2]}` }, h("span", {}, reason[0]), h("span", { style: "white-space: pre-wrap" }, reason[1])) : null,
          isOpen ? h("div", { class: "changes-grid" }, rows.map(([sec, lbl, val]) => [
            h("span", { class: "mono xs" }, sec), h("span", { class: "muted" }, lbl), val])) : null);
      })) : h("div", { class: "card empty" }, list.length ? "Nothing here with this filter." : "Nothing applied from this manager yet."),
      more && list.length ? h("button", { type: "button", class: "btn", style: "align-self: center", onclick: busyClick(async () => {
        await load(true); draw();
      }) }, "Show older") : null);
  };

  const restore = async (vid) => {
    try {
      const v = await api("GET", `/api/versions/${vid}`);
      const d = await api("POST", `/api/routers/${id}/diff`, { config: v.config });
      if (!d.diff.length) return toast("The router already has these settings");
      openApply(id, { base: d.fingerprint, config: v.config, note: `Restore #${vid}`, trigger: "restore",
        done: async () => { await load(); draw(); } });
    } catch (e) { toast(e.message, true); }
  };

  await load();
  draw();
  S.view = {
    onRouter: (r) => r.id === id && draw(),
    onJob: (m) => { if (m.router_id === id && FINAL_STATES.includes(m.job.state)) load().then(draw); },
  };
}

const FINAL_STATES = ["applied", "saved", "unchanged", "rejected", "reverted", "failed"];

// --- keys and usage -----------------------------------------------------------------------------------

// sub.example.com/s/…k9Q: enough to tell keys apart, not enough to use one
function keyLabel(url) {
  try {
    const u = new URL(url);
    const path = u.pathname + u.search;
    return path.length > 8 ? `${u.host}${path.slice(0, path.indexOf("/", 1) + 1 || 1)}…${path.slice(-3)}` : u.host + path;
  } catch { return url.slice(0, 12) + "…"; }
}

const countryName = (cc) => {
  try { return new Intl.DisplayNames(["en"], { type: "region" }).of(cc); } catch { return cc; }
};

// podkop-probe's "europe" (EUROPE in router/probe.sh)
const EUROPE = ("EU AD AL AT BA BE BG CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS "
  + "SE SI SK SM UA VA XK").split(" ");
const hasEurope = (cs) => EUROPE.every((c) => cs.includes(c));
// "Europe, TR" or "DE, PL"
const countriesText = (cs) => (hasEurope(cs) ? ["Europe", ...cs.filter((c) => !EUROPE.includes(c))] : cs).join(", ");

const GB = 1024 ** 3;
const gb = (b) => (b >= GB / 10 || !b ? `${(b / GB).toFixed(b >= 100 * GB ? 0 : 1)} GB` : `${Math.round(b / 1024 ** 2)} MB`);
const usageUnit = (k) => (k.daily ? "today" : k.period === "month" ? "month" : "period");
const shade = (i) => `var(--shade-${Math.min(i, 3) + 1})`;

function usagePill(k) {
  if (k.pct === null) return h("span", { class: "pill" }, "no limit");
  return h("span", { class: `pill ${k.pct >= k.threshold ? "bad" : k.pct >= 60 ? "warn" : "ok"}` }, `${Math.round(k.pct)}% of ${usageUnit(k)}`);
}

// the running total of today with a dashed forecast at the last 3 hours' rate
function usageChart(k) {
  const x0 = 32, x1 = 472, y0 = 120, y1 = 18.75;
  const top = Math.max(k.limit, k.used) || GB;
  const X = (t) => (x0 + ((t - k.start) / 86400) * (x1 - x0)).toFixed(1);
  const Y = (v) => (y0 - (v / top) * (y0 - y1)).toFixed(1);
  const [nt, nv] = k.series[k.series.length - 1];
  const line = k.series.map(([t, v], i) => `${i ? "L" : "M"}${X(t)} ${Y(v)}`).join(" ");
  const f = k.forecast;
  let ahead = "";
  if (f && f.rate > 0 && k.used < k.limit) {
    const end = Math.min(f.eta || Infinity, k.next_reset);
    ahead = `<path d="M${X(nt)} ${Y(nv)} L${X(end)} ${Y(Math.min(k.limit, nv + f.rate * (end - nt)))}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-dasharray="4 4"/>`;
    if (f.eta && f.eta < k.next_reset) {
      ahead += `<circle cx="${X(f.eta)}" cy="${Y(k.limit)}" r="3.5" fill="var(--surface)" stroke="var(--accent)" stroke-width="2"/>
        <text x="${X(f.eta)}" y="34" text-anchor="middle" font-size="11" fill="var(--text)">≈${hhmm(new Date(f.eta * 1000))}</text>`;
    }
  }
  const grid = [0, 1, 2, 3].map((q) => {
    const v = (top * q) / 3, y = Y(v);
    return `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" stroke="var(${q ? "--line-soft" : "--control"})"/>
      <text x="26" y="${+y + 3.5}" text-anchor="end" font-size="10.5" fill="var(--muted)">${Math.round(v / GB)}</text>`;
  }).join("");
  const h0 = +k.reset.split(":")[0];
  const hours = [0, 6, 12, 18, 24].map((d, i) => `<text x="${x0 + (i * (x1 - x0)) / 4}" y="136" text-anchor="middle" font-size="10.5"
    fill="var(--muted)">${String((h0 + d) % 24 || (d ? 24 : 0)).padStart(2, "0")}</text>`).join("");
  const t = document.createElement("template");
  t.innerHTML = `<svg viewBox="0 0 480 140" role="img" aria-label="${k.used >= 0 ? `Used today: ${gb(k.used)}` : ""}" style="width: 100%; height: auto">
    ${grid}${hours}
    <line x1="${x0}" y1="${Y(k.limit)}" x2="${x1}" y2="${Y(k.limit)}" stroke="var(--text-2)" stroke-width="1.5" stroke-dasharray="2 3"/>
    <text x="${x1}" y="12" text-anchor="end" font-size="11" fill="var(--text-2)">limit ${gb(k.limit)}</text>
    <path d="${line} L${X(nt)} ${y0} L${x0} ${y0} Z" fill="var(--accent-soft)"/>
    <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
    ${ahead}
    <circle cx="${X(nt)}" cy="${Y(nv)}" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>
  </svg>`;
  return h("figure", { class: "uchart" }, h("figcaption", {}, h("span", {}, "Used today"), f ? h("span", {}, "dashed: at the last 3 hours' rate") : null),
    t.content.firstChild);
}

async function viewUsage(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let data, histKey = null;
  const load = async () => { data = await api("GET", `/api/routers/${id}/usage`); };
  const save = async (k, body) => {
    try { await api("PUT", `/api/keys/${k.id}`, body); await load(); draw(); }
    catch (e) { toast(e.message, true); }
  };

  const keyLine = (k) => h("span", { class: "mono xs muted ellipsis" }, k.kind === "sub"
    ? `${keyLabel(k.url)} · subscription${k.servers ? `, ${plural(k.servers, "server")}` : ""}`
    : `${linkProto(k.url)}://…@${linkHost(k.url)} · single link`);
  const bar = (k) => (k.limit ? h("div", { class: `ubar${k.pct >= 100 ? " over" : ""}`, role: "progressbar", "aria-label": `${k.label} traffic`,
    "aria-valuemin": "0", "aria-valuemax": String(k.limit), "aria-valuenow": String(k.used) },
  h("div", { style: `width: ${Math.min(100, k.pct)}%` }),
  k.daily ? h("span", { class: "mark", style: `left: ${k.threshold}%`, title: `${k.threshold}%` }) : null) : null);
  const source = (k) => (k.limit_source === "provider" ? `from the provider${k.read ? `, read ${ago(k.read)}` : ""}` : "set by you, counted on the router");
  const actionPick = (k) => h("div", { class: "row", style: "gap: 8px" },
    h("label", { for: `act-${k.id}`, class: "small muted" }, `At ${k.threshold}%`),
    h("select", { id: `act-${k.id}`, class: "input", style: "width: auto; height: 32px", onchange: (e) => save(k, { action: e.target.value }) },
      h("option", { value: "avoid", selected: k.action === "avoid" }, "Move rules to other keys"),
      h("option", { value: "warn", selected: k.action === "warn" }, "Only warn me")));
  const editBtn = (k) => h("button", { type: "button", class: "btn sm", onclick: () => editKey(k) }, "Edit");

  const byRule = (k) => {
    const tot = k.by_rule.reduce((a, r) => a + r.bytes, 0);
    if (!tot) return null;
    return h("div", { class: "col", style: "gap: 6px" }, h("span", { class: "small", style: "font-weight: 500" }, "Today by rule"),
      h("div", { class: "ustack" }, k.by_rule.map((r, i) => h("div", { title: `${r.rule} ${gb(r.bytes)}`,
        style: `width: ${(r.bytes / tot) * 100}%; background: ${shade(i)}` }))),
      h("div", { class: "legend" }, k.by_rule.map((r, i) => h("span", {}, h("i", { style: `background: ${shade(i)}` }), `${r.rule} ${gb(r.bytes)}`))));
  };

  const bigCard = (k) => {
    const left = Math.max(0, k.limit - k.used), mins = Math.max(0, Math.round((k.next_reset - data.now) / 60));
    return h("section", { class: "card", id: `key-${k.id}`, "aria-labelledby": `kh-${k.id}` },
      h("div", { class: "key-big" },
        h("div", { class: "stack", style: "flex: 1 1 300px; min-width: 0; gap: 14px" },
          h("div", { class: "col", style: "gap: 4px" },
            h("div", { class: "row", style: "gap: 10px" }, h("h2", { id: `kh-${k.id}` }, k.label), usagePill(k)), keyLine(k)),
          h("div", { class: "col", style: "gap: 8px" },
            h("div", { class: "row", style: "align-items: baseline; gap: 8px" }, h("span", { class: "big-num" }, gb(k.used)),
              h("span", { class: "muted" }, `of ${gb(k.limit)} today`)),
            bar(k),
            h("div", { class: "row small muted", style: "justify-content: space-between" },
              h("span", {}, `${gb(left)} left, resets in ${duration(mins * 60)}`), h("span", {}, `mark at ${k.threshold}%`))),
          byRule(k), k.why ? h("span", { class: "small bad-text" }, k.why) : null),
        h("div", { style: "flex: 1 1 340px; min-width: 0" }, k.series.length > 1 ? usageChart(k) : h("p", { class: "muted small" }, "The chart fills in as traffic is counted."))),
      h("div", { class: "card-foot" },
        h("span", { class: "small" }, h("b", {}, `${gb(k.limit)} per day `), h("span", { class: "muted" }, source(k))),
        h("div", { class: "row", style: "gap: 12px" }, actionPick(k), editBtn(k))));
  };

  const smallCard = (k) => {
    const top = k.by_rule[0];
    const facts = k.kind === "link"
      ? [["Used by", k.by_rule.map((r) => r.rule).join(", ") || "–"], ["Resets", k.daily ? k.reset : k.period], [`At ${k.threshold}%`, k.action === "avoid" ? "move rules" : "only warn"]]
      : [["Today", `${gb(k.today)}${top ? `, ${top.rule}` : ""}`],
        [k.daily ? "Resets" : "No daily limit", k.daily ? k.reset : k.expire ? `period ends ${new Date(k.expire * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}` : "–"],
        ["Key expires", k.expire ? `${new Date(k.expire * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}, in ${Math.max(0, Math.round((k.expire - data.now) / 86400))} days` : "–"]];
    return h("section", { class: "card pad", id: `key-${k.id}`, "aria-labelledby": `kh-${k.id}` },
      h("div", { class: "col", style: "gap: 4px" },
        h("div", { class: "row", style: "gap: 10px" }, h("h2", { id: `kh-${k.id}` }, k.label), usagePill(k)), keyLine(k)),
      h("div", { class: "row", style: "align-items: baseline; gap: 8px" }, h("span", { class: "big-num sm" }, gb(k.used)),
        h("span", { class: "muted" }, k.limit ? `of ${gb(k.limit)} ${k.daily ? "today" : "this " + usageUnit(k)}` : k.daily ? "today" : "used")),
      bar(k),
      h("div", { class: "key-facts" }, facts.map(([a, b]) => h("div", {}, h("span", {}, a), h("span", {}, b)))),
      k.why ? h("span", { class: "small bad-text" }, k.why) : null,
      h("div", { class: "row", style: "justify-content: space-between" },
        h("span", { class: "small muted" }, k.read || k.kind === "link" ? source(k) : "Waiting for the first reading"), editBtn(k)));
  };

  const warnings = () => data.keys.filter((k) => k.daily && k.limit && (k.used >= k.limit * k.threshold / 100 || (k.forecast && k.forecast.hits_today)))
    .map((k) => h("section", { class: "banner warn", "aria-label": "Warning" },
      h("span", { class: "dot warn" }),
      h("div", { class: "col grow", style: "flex: 1 1 320px" },
        h("span", { style: "font-weight: 600" }, k.used >= k.limit ? `${k.label} used all of today's traffic`
          : k.forecast && k.forecast.eta ? `${k.label} will run out of today's traffic around ${hhmm(new Date(k.forecast.eta * 1000))}`
            : `${k.label} used ${Math.round(k.pct)}% of today's traffic`),
        h("span", { class: "small muted" }, `${gb(k.used)} of ${gb(k.limit)}. At ${k.threshold}%: ${k.action === "avoid" ? `rules move to other keys until ${k.reset}` : "only a warning"}.`)),
      h("a", { class: "alink", href: `#key-${k.id}`, onclick: (e) => { e.preventDefault(); document.getElementById(`key-${k.id}`).scrollIntoView({ behavior: "smooth" }); } },
        "Change what happens")));

  const history = () => {
    const ks = data.keys.filter((k) => k.limit || k.days.some((d) => d.bytes));
    if (!ks.length) return null;
    const k = ks.find((x) => x.id === histKey) || ks[0];
    const top = Math.max(k.daily ? k.limit : 0, ...k.days.map((d) => d.bytes)) || GB;
    const hits = k.days.filter((d) => d.hit).length;
    return h("section", { class: "card pad", "aria-labelledby": "u-hist" },
      h("div", { class: "row", style: "justify-content: space-between" },
        h("div", { class: "col" }, h("h2", { id: "u-hist" }, "Last 14 days"),
          hits ? h("span", { class: "small muted" }, `${k.label} hit its limit on ${plural(hits, "day")}`) : null),
        ks.length > 1 ? seg(ks.map((x) => [x.id, x.label]), k.id, (v) => { histKey = v; draw(); }, { small: true, label: "Key" }) : null),
      h("div", { class: "days" },
        k.daily && k.limit ? h("div", { class: "limit", style: `bottom: ${(k.limit / top) * 100}%` }) : null,
        k.days.map((d) => h("div", { class: `day${d.hit ? " hit" : ""}${d.today ? " today" : ""}`,
          title: `${d.today ? "Today so far" : new Date(d.t * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}: ${gb(d.bytes)}${d.hit ? ", limit reached" : ""}` },
        d.hit ? h("span", {}, "limit") : null, h("b", { style: `height: ${(d.bytes / top) * 100}%` })))),
      h("div", { class: "day-labels" }, k.days.map((d) => h("span", { class: d.today ? "today" : "" },
        d.today ? "today" : new Date(d.t * 1000 + 43200e3).getDate()))),
      h("div", { class: "legend muted" },
        k.daily && k.limit ? h("span", {}, h("i", { class: "dash" }), `daily limit ${gb(k.limit)}`) : null,
        hits ? h("span", {}, h("i", { style: "background: var(--bad)" }), "limit reached") : null,
        h("span", {}, "faded bar: today so far")));
  };

  const servers = () => {
    if (!data.servers.length) return null;
    const tot = data.servers.reduce((a, s) => a + s.bytes, 0);
    return h("section", { class: "card clip", "aria-labelledby": "u-srv" },
      h("div", { class: "card-head" }, h("h2", { id: "u-srv" }, "Today by server"),
        h("span", { class: "small muted" }, `${gb(tot)} through podkop since 00:00`)),
      h("div", { class: "scroll-x" }, h("table", {},
        h("tr", {}, h("th", { scope: "col" }, "Server"), h("th", { scope: "col" }, "Key"), h("th", { scope: "col" }, "Rule"),
          h("th", { scope: "col", style: "width: 32%" }, "Share"), h("th", { scope: "col", class: "r" }, "Traffic")),
        data.servers.slice(0, 20).map((s) => h("tr", {},
          h("td", {}, s.name), h("td", { class: "muted" }, s.key || "–"), h("td", { class: "mono-cell" }, s.rule),
          h("td", {}, h("div", { class: "ubar thin" }, h("div", { style: `width: ${(s.bytes / tot) * 100}%` }))),
          h("td", { class: "r num" }, gb(s.bytes)))))));
  };

  const addLink = () => {
    const f = { link: h("input", { class: "input mono", id: "nk-link", placeholder: "https://… subscription, or vless://… link" }),
      label: h("input", { class: "input", id: "nk-label", placeholder: "e.g. fra-personal" }),
      limit: h("input", { class: "input mid", id: "nk-limit", inputmode: "decimal", placeholder: "10" }) };
    let sending = false;
    return h("form", { class: "card pad", "aria-labelledby": "u-add", onsubmit: async (e) => {
      e.preventDefault();
      if (sending) return;
      sending = true;
      try {
        const link = f.link.value.trim();
        await api("POST", `/api/routers/${id}/keys`, { link, label: f.label.value.trim(), limit_gb: f.limit.value || 0 });
        if (/^https?:\/\//.test(link)) {
          toast("Subscription added: its usage shows up in a minute, its servers after the next probe run");
          api("POST", `/api/routers/${id}/usage/refresh`).then((d) => { data = d; draw(); }).catch(() => {});
        }
        await load(); draw();
      } catch (err) { toast(err.message, true); } finally { sending = false; }
    } },
    h("h2", { id: "u-add" }, "Add a key"),
    h("div", { class: "auto-grid", style: "grid-template-columns: repeat(auto-fit, minmax(min(200px, 100%), 1fr))" },
      h("div", { class: "field", style: "grid-column: span 2" }, h("label", { for: "nk-link" }, "Subscription URL or link"), f.link),
      h("div", { class: "field" }, h("label", { for: "nk-label" }, "Name"), f.label),
      h("div", { class: "field" }, h("label", { for: "nk-limit" }, "GB per day"), h("div", { class: "unit" }, f.limit, h("span", {}, "GB")),
        h("span", { class: "help" }, "For a link; a subscription's limit comes from the provider"))),
    h("div", {}, h("button", { type: "submit", class: "btn" }, "Add")));
  };

  const editKey = (k) => {
    const st = k.settings;
    const v = { ...st };
    const out = h("div");
    const body = () => [
      h("div", { class: "field" }, h("label", { for: "ek-label" }, "Name"),
        h("input", { class: "input", id: "ek-label", value: v.label, placeholder: k.label, oninput: (e) => { v.label = e.target.value; } })),
      k.kind === "sub" ? h("div", { class: "field" }, h("span", { class: "label" }, "Limit"),
        seg([["provider", "From the provider"], ["manual", "My own"]], v.limit_source, (x) => { v.limit_source = x; redraw(); }, { label: "Limit" })) : null,
      v.limit_source === "manual" || k.kind === "link" ? h("div", { class: "field" }, h("label", { for: "ek-gb" }, "Limit"),
        h("div", { class: "unit" }, h("input", { class: "input mid", id: "ek-gb", inputmode: "decimal", value: String(v.limit_gb || ""),
          oninput: (e) => { v.limit_gb = e.target.value; } }), h("span", {}, "GB"))) : null,
      h("div", { class: "field" }, h("span", { class: "label" }, "Period"),
        seg([...(k.kind === "sub" ? [["", "Auto"]] : []), ["day", "Day"], ["month", "Month"], ...(k.kind === "sub" ? [["none", "Whole key"]] : [])],
          v.period, (x) => { v.period = x; redraw(); }, { label: "Period" })),
      h("div", { class: "row", style: "gap: 16px" },
        h("div", { class: "field" }, h("label", { for: "ek-reset" }, "Resets at"),
          h("input", { class: "input mid num", id: "ek-reset", type: "time", value: v.reset_time, oninput: (e) => { v.reset_time = e.target.value; } })),
        h("div", { class: "field" }, h("label", { for: "ek-thr" }, "Act at"),
          h("div", { class: "unit" }, h("input", { class: "input mid", id: "ek-thr", inputmode: "numeric", value: String(v.threshold),
            oninput: (e) => { v.threshold = e.target.value; } }), h("span", {}, "%")))),
      out];
    const box = h("div", { class: "stack" });
    const redraw = () => put(box, body());
    redraw();
    const m = modal({ title: k.label, narrow: true, body: box,
      foot: [h("button", { type: "button", class: "btn danger", onclick: async () => {
        if (!confirm(k.kind === "sub" ? `Remove ${k.label}? The probe stops testing its servers.` : `Remove ${k.label}?`)) return;
        try { await api("DELETE", `/api/keys/${k.id}`); m.close(); await load(); draw(); } catch (e) { toast(e.message, true); }
      } }, "Remove"),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: async () => {
          try {
            await api("PUT", `/api/keys/${k.id}`, { label: v.label, limit_source: v.limit_source, limit_gb: v.limit_gb || 0,
              period: v.period, reset_time: v.reset_time, threshold: v.threshold });
            m.close(); await load(); draw();
          } catch (e) { put(out, h("p", { class: "problem-box" }, e.message)); }
        } }, "Save"))] });
  };

  const refresh = h("button", { type: "button", class: "btn", onclick: async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { data = await api("POST", `/api/routers/${id}/usage/refresh`); draw(); } catch (err) { toast(err.message, true); }
    b.disabled = false;
  } }, icon("refresh", 15), "Ask providers now");

  const draw = () => {
    const rt = S.routers[id];
    const old = rt && rt.status && rt.status.agent.version.localeCompare("0.2.0", undefined, { numeric: true }) < 0;
    const big = data.keys.filter((k) => k.daily && k.limit && k.series.length);
    const small = data.keys.filter((k) => !big.includes(k));
    put(page,
      pageHead("Keys and usage", null, refresh),
      old ? h("div", { class: "banner" }, h("span", { class: "dot warn" }),
        h("span", { class: "grow" }, "podkop-agent on this router is too old to count traffic."), h("a", { class: "alink", href: "#/settings" }, "Reinstall agent")) : null,
      warnings(),
      data.keys.length ? null : h("div", { class: "card empty" }, "No keys yet: add a subscription or a link below."),
      big.map(bigCard),
      small.length ? h("div", { class: "cards-2" }, small.map(smallCard)) : null,
      history(), servers(), addLink(),
      h("p", { class: "small muted" }, "Provider numbers are read every 10 minutes; the split by rule and server is counted on the router."));
  };

  await load();
  draw();
  S.view = { onUsage: (m) => { if (m.router_id === id && !document.querySelector(".modal-bg") && !page.querySelector("form :focus")) load().then(draw).catch(() => {}); } };
}

// busyClick(fn): a click handler that ignores clicks while its work runs (the button is disabled meanwhile)
const busyClick = (fn) => async (e) => {
  const b = e && e.currentTarget;
  if (b) { if (b.disabled) return; b.disabled = true; b.setAttribute("aria-busy", "true"); }
  try { await fn(e); } finally { if (b) { b.disabled = false; b.removeAttribute("aria-busy"); } }
};

// --- the routing draft ---------------------------------------------------------------------------------
// One per router, shared by Rules and Global settings, kept on the manager
// (/draft) so it survives reloads. w.config is edited in place.

async function loadWork(id) {
  const prev = S.work[id];
  if (prev && prev.saving) saveWork(prev);
  if (prev && prev.inflight) await prev.inflight;      // its saves first, or they would look like someone else's
  const [cur, draft, versions, rules] = await Promise.all([api("GET", `/api/routers/${id}/config`),
    api("GET", `/api/routers/${id}/draft`), api("GET", `/api/routers/${id}/versions?limit=50`).catch(() => []),
    api("GET", `/api/routers/${id}/auto-rules`).catch(() => [])]);
  const old = S.work[id];
  const w = {
    id, orig: cur.config, fp: cur.fingerprint,
    ctx: { lists: cur.community_lists || [], devices: cur.devices || [], interfaces: cur.interfaces || [] },
    config: draft ? draft.config : clone(cur.config), base: draft ? draft.base : cur.fingerprint,
    rev: draft ? draft.updated : null,     // the draft this page started from, see saveWork
    versions, auto: Object.fromEntries(rules.map((r) => [r.section, r])),
    problems: old && old.problems && draft && same(old.config, draft.config) ? old.problems : null,
    saving: null, inflight: null, loaded: Date.now(), redraw: () => {},
  };
  S.work[id] = w;
  return w;
}

// workFor: the draft as loaded a moment ago when moving between rules (or to
// Global settings) of one router; loaded again after any other page (Configs
// may have changed it), after 2 minutes, or when the router's settings changed
async function workFor(id) {
  const w = S.work[id], rt = S.routers[id];
  if (w && !w.stale && [`${id}/rules`, `${id}/global`].includes(S.from) && Date.now() - w.loaded < 120e3
      && rt && rt.status && rt.status.fingerprint === w.fp) return w;
  return loadWork(id);
}

const workChanges = (w) => configDiff(w.orig, w.config);

// one save at a time: each PUT goes after the one before, with the rev that one returned
function saveWork(w) {
  clearTimeout(w.saving);
  w.saving = null;
  w.inflight = (w.inflight || Promise.resolve()).then(() => putWork(w));
  return w.inflight;
}

async function putWork(w) {
  if (w.stale) return;
  try {
    if (workChanges(w).length) {
      const d = await api("PUT", `/api/routers/${w.id}/draft`, { config: w.config, base: w.base, rev: w.rev });
      w.rev = d ? d.updated : null;
    } else {
      await api("DELETE", `/api/routers/${w.id}/draft${w.rev !== null ? `?rev=${w.rev}` : ""}`);
      w.rev = null;
    }
  } catch (e) {
    if (e.status !== 409) { toast(`The draft was not saved: ${e.message}`, true); return; }
    // another tab, or "Load into draft", changed it: show that one instead of overwriting it
    toast("The draft was changed elsewhere; showing that one", true);
    w.stale = true;
    // not awaited: loadWork waits for this save to end
    if (S.work[w.id] === w) loadWork(w.id).then(render).catch((e2) => toast(e2.message, true));
  }
}

// after every edit: save the draft a moment later, update the bar
function touch(w) {
  if (S.work[w.id] !== w) return;      // left behind by a newer load of the draft: never save it
  w.problems = w.problems ? Object.assign(w.problems, { stale: true }) : null;
  clearTimeout(w.saving);
  w.saving = setTimeout(() => saveWork(w), 500);
  w.drawBar && w.drawBar();
}

async function discardWork(w) {
  clearTimeout(w.saving);
  w.saving = null;
  await api("DELETE", `/api/routers/${w.id}/draft?discard=1`);
  await loadWork(w.id);
  render();
}

async function checkWork(w) {
  w.checking = true; w.drawBar && w.drawBar();
  try {
    const r = await api("POST", `/api/routers/${w.id}/validate`, { config: w.config });
    w.problems = r.problems;
  } catch (e) { toast(e.message, true); }
  w.checking = false;
  w.redraw();
}

function reviewWork(w) {
  openApply(w.id, { base: w.base, config: w.config, note: "", trigger: "web", done: async (ok) => {
    if (ok) { await loadWork(w.id); render(); }
  } });
}

// the bar over Rules: changes, problems, Discard, Check, Review and apply
function draftBar(w) {
  const box = h("div");
  w.drawBar = () => {
    const n = workChanges(w).length;
    const rt = S.routers[w.id];
    // while the router applies, its fingerprint moves; that is not someone else's change
    const busy = !!(rt && rt.status && rt.status.podkop.busy);
    const stale = w.base !== w.fp && !busy;
    if (!n && !stale) { put(box); return; }
    const probs = w.problems || [];
    put(box, h("div", { class: `banner${stale ? " bad" : ""}`, role: "region", "aria-label": "Unapplied changes" },
      h("span", { class: `dot ${stale ? "bad" : "accent"}` }),
      h("div", { class: "row grow", style: "flex: 1 1 300px; align-items: baseline; gap: 4px 12px" },
        h("span", { style: "font-size: 14px; font-weight: 600" }, n ? `${plural(n, "change")} not applied` : "Draft"),
        stale ? h("span", { class: "small bad-text" }, "Settings changed on the router since. Discard and edit again.")
          : busy ? h("span", { class: "small muted" }, "The router is applying settings now…")
            : w.checking ? h("span", { class: "small muted" }, "Checking on the router…")
            : w.problems ? h("span", { class: `small ${probs.length ? "bad-text" : "ok-text"}` },
              probs.length ? `${plural(probs.length, "problem")} found by Check${w.problems.stale ? ", changed since" : ""}` : `Check found no problems${w.problems.stale ? ", changed since" : ""}`) : null),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn ghost", onclick: async () => {
          if (confirm(`Discard ${plural(n, "change")}? The router keeps its settings.`)) await discardWork(w);
        } }, "Discard"),
        !stale ? h("button", { type: "button", class: "btn", disabled: w.checking, onclick: () => checkWork(w) }, w.problems ? "Check again" : "Check") : null,
        !stale ? h("button", { type: "button", class: "btn primary", onclick: () => reviewWork(w) }, "Review and apply") : null)),
    probs.length ? h("div", { class: "problem-box", style: "margin-top: 8px" }, probs.map((p) => h("div", {},
      p.section && rulesOf(w.config).some((s) => s.name === p.section)
        ? h("a", { href: `#/r/${w.id}/rules/${p.section}`, style: "color: inherit; text-decoration: underline" }, problemText(p)) : problemText(p)))) : null);
  };
  w.drawBar && w.drawBar();
  return box;
}

// --- fields bound to one section of the draft --------------------------------------------------------

function binder(w, section, defs, { defaults = true } = {}) {
  const sec = () => sectionsOf(w.config).find((s) => s.name === section);
  const orig = bySection(w.orig).get(section);
  const o = () => (sec().options || (sec().options = {}));
  const def = (opt) => (defs.find((d) => d.name === opt) || {}).default;
  const b = {
    get: (opt) => o()[opt],
    val: (opt) => (o()[opt] !== undefined ? o()[opt] : def(opt)),
    set: (opt, v, redraw) => {
      if (v === "" || v === null || v === undefined || (Array.isArray(v) && !v.length)) delete o()[opt];
      else o()[opt] = v;
      touch(w);
      if (redraw) w.redraw();
    },
    old: (opt) => (orig ? (orig.options || {})[opt] : undefined),
    changed: (opt) => !!orig && !same(b.old(opt), o()[opt]),
    isNew: !orig,
    // dot: accent when changed in the draft, gray when it differs from podkop's default
    dot: (opt) => {
      if (b.changed(opt)) return h("span", { class: "dot sm accent", title: "changed in this draft" });
      const d = def(opt), v = o()[opt];
      return defaults && v !== undefined && d !== undefined && !same(asList(v), asList(d)) ? h("span", { class: "dot sm gray", title: "differs from podkop's default" }) : null;
    },
    was: (opt) => {
      if (!b.changed(opt) || Array.isArray(b.old(opt)) || Array.isArray(o()[opt])) return null;
      const old = b.old(opt);
      return h("span", { class: "was" }, old === undefined ? "was not set" : `was ${shortValue(old)}`);
    },
    problems: (opt) => (w.problems || []).filter((p) => p.section === section && p.option === opt && (p.index === undefined || p.index === null)),
    problemEls: (opt) => b.problems(opt).map((p) => h("span", { class: "field-problem" }, p.message)),
    indexProblem: (opt, i) => (w.problems || []).find((p) => p.section === section && p.option === opt && p.index === i),
  };
  return b;
}

// focus survives a redraw when controls carry data-k
function keepFocus(fn) {
  const a = document.activeElement;
  const k = a && a.getAttribute && a.getAttribute("data-k");
  const pos = a && "selectionStart" in a ? a.selectionStart : null;
  fn();
  if (!k) return;
  const el = document.querySelector(`[data-k="${CSS.escape(k)}"]`);
  if (el) { el.focus(); if (pos !== null && "setSelectionRange" in el) try { el.setSelectionRange(pos, pos); } catch { /* not a text field */ } }
}

// label with its marker dot
const lbl = (b, opt, text, forId) => h(forId ? "label" : "span", { class: forId ? null : "label", for: forId || null }, b.dot(opt), text);

function textField(b, opt, { mono, placeholder, width, type } = {}) {
  return h("input", { class: `input mid${mono ? " mono" : ""}`, style: width ? `max-width: ${width}` : null, type: type || "text",
    "data-k": `${opt}`, value: b.get(opt) ?? "", placeholder: placeholder ?? (b.val(opt) ?? ""),
    oninput: (e) => b.set(opt, e.target.value), onchange: () => keepFocus(() => {}) });
}

function comboField(b, opt, values, { mono = true } = {}) {
  const lid = `dl-${opt}`;
  return h("span", { style: "display: contents" },
    h("input", { class: `input mid${mono ? " mono" : ""}`, list: lid, "data-k": opt, value: b.get(opt) ?? "", placeholder: b.val(opt) ?? "",
      oninput: (e) => b.set(opt, e.target.value) }),
    h("datalist", { id: lid }, values.map((v) => h("option", { value: v }))));
}

function selectField(b, opt, values, { mono } = {}) {
  const v = b.val(opt);
  return h("select", { class: `input mid${mono ? " mono" : ""}`, "data-k": opt, onchange: (e) => b.set(opt, e.target.value, true) },
    v === undefined ? h("option", { value: "" }, "—") : null,
    [...new Set([...values, ...(v !== undefined ? [v] : [])])].map((x) => h("option", { value: x, selected: x === v }, x)));
}

function flagField(b, opt, text, sub, { disabled } = {}) {
  return h("label", { class: sub ? "check" : "check inline" },
    h("input", { type: "checkbox", "data-k": opt, checked: b.val(opt) === "1", disabled,
      onchange: (e) => b.set(opt, e.target.checked ? "1" : "0", true) }),
    sub ? h("span", { class: "col" }, h("span", { class: "row", style: "gap: 8px" }, text, b.dot(opt)), h("span", {}, sub))
      : h("span", { class: "row", style: "gap: 8px" }, text, b.dot(opt)));
}

function segField(b, opt, values, labels) {
  return seg(values.map((v, i) => [v, labels ? labels[i] : v]), b.val(opt), (v) => b.set(opt, v, true), { small: true, label: opt });
}

// chips for a list of words (domains, subnets, URLs); new ones are green
function tokenField(b, opt, { placeholder, splitter = /[\s,]+/ } = {}) {
  const items = asList(b.get(opt));
  const old = asList(b.old(opt));
  const add = (text) => {
    const more = text.split(splitter).map((x) => x.trim()).filter(Boolean).filter((x) => !items.includes(x));
    if (more.length) b.set(opt, [...items, ...more], true);
  };
  return h("div", { class: "tokens" },
    items.map((x, i) => h("span", { class: `token${b.isNew || !old.includes(x) ? " new" : ""}${b.indexProblem(opt, i) ? " bad" : ""}`,
      title: b.indexProblem(opt, i) ? b.indexProblem(opt, i).message : null }, x,
    h("button", { type: "button", "aria-label": `Remove ${x}`, onclick: () => b.set(opt, items.filter((y) => y !== x), true) },
      icon("close", 10, { width: 2 })))),
    h("label", { class: "sr", for: `add-${opt}` }, `Add to ${opt}`),
    h("input", { id: `add-${opt}`, "data-k": `add-${opt}`, placeholder: placeholder || "Add, Enter",
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === ",") { e.preventDefault(); const v = e.target.value; e.target.value = ""; keepFocus(() => add(v)); }
        else if (e.key === "Backspace" && !e.target.value && items.length) keepFocus(() => b.set(opt, items.slice(0, -1), true));
      },
      onpaste: (e) => {
        const text = (e.clipboardData || window.clipboardData).getData("text");
        if (/[\s,]/.test(text.trim())) { e.preventDefault(); keepFocus(() => add(text)); }
      },
      onblur: (e) => { if (e.target.value.trim()) { const v = e.target.value; e.target.value = ""; add(v); } } }));
}

// Off / List / Text for user domains and subnets
function userListField(b, kind) {
  const type = `user_${kind}_list_type`, list = `user_${kind}s`, text = `user_${kind}s_text`;
  const mode = b.val(type) || "disabled";
  const name = kind === "domain" ? "Your domains" : "Your subnets";
  const changed = b.changed(type) || b.changed(list) || b.changed(text);
  const label = h("span", { class: "label" }, changed ? h("span", { class: "dot sm accent" }) : null, name);
  if (mode === "disabled" && kind === "subnet") {
    return h("div", { class: "mini" }, label, h("span", { class: "row" }, h("span", { class: "small muted" }, "Off"),
      h("button", { type: "button", class: "link-btn", onclick: () => b.set(type, "dynamic", true) }, "Turn on")));
  }
  return h("div", { class: "field" },
    h("div", { class: "row", style: "justify-content: space-between" }, label,
      seg([["disabled", "Off"], ["dynamic", "List"], ["text", "Text"]], mode, (v) => b.set(type, v, true), { small: true, label: name })),
    mode === "dynamic" ? tokenField(b, list, { placeholder: kind === "domain" ? "Add domain, Enter" : "Add subnet, Enter" }) : null,
    mode === "text" ? h("textarea", { class: "input", rows: 5, "data-k": text, oninput: (e) => b.set(text, e.target.value) }, b.get(text) || "") : null,
    mode === "dynamic" ? h("span", { class: "help" }, "Green: new in this draft") : null,
    b.problemEls(list), b.problemEls(text));
}

// --- links ----------------------------------------------------------------------------------------------

const LINK_RE = /[a-z0-9]+:\/\/[^\s"'<>]+/gi;

// the list of server links of a rule: rows with name, protocol, host; drag, remove, add
function serversField(w, b, opt, { title, help, collapsible }) {
  const links = asList(b.get(opt));
  const old = asList(b.old(opt));
  const ui = w.ui[`${b.name}:${opt}`] || (w.ui[`${b.name}:${opt}`] = { open: !collapsible, all: false });
  const head = h("div", { class: "servers-head" },
    h("span", {}, b.changed(opt) ? h("span", { class: "dot sm accent" }) : null, `${title}, ${links.length}`));
  if (collapsible && !ui.open) {
    return h("div", { class: "mini" }, h("div", { class: "col" }, h("span", { class: "row", style: "gap: 8px; font-size: 13px; font-weight: 500" },
      b.changed(opt) ? h("span", { class: "dot sm accent" }) : null, `${title}, ${links.length}`), h("span", { class: "xs muted" }, help)),
    h("button", { type: "button", class: "btn sm", style: "width: 32px; padding: 0", "aria-label": `Show ${title.toLowerCase()}`,
      onclick: () => { ui.open = true; w.redraw(); } }, icon("down", 14, { width: 1.6 })));
  }
  const shown = ui.all ? links : links.slice(0, 6);
  let drag = null;
  const move = (from, to) => { const l = [...links]; l.splice(to, 0, l.splice(from, 1)[0]); b.set(opt, l, true); };
  const addArea = h("textarea", { class: "input", rows: 2, id: `paste-${opt}`, "data-k": `paste-${opt}`,
    placeholder: "Paste links, one per line: vless://, trojan://, ss://, hysteria2://, socks://" });
  const addLinks = () => {
    const found = (addArea.value.match(LINK_RE) || []).filter((l) => !links.includes(l));
    if (!found.length) return toast("No links found: they start with vless://, trojan://, ss://, hysteria2:// or socks://", true);
    addArea.value = "";
    b.set(opt, [...links, ...found], true);
  };
  const oldFp = links.filter(oldFingerprint);
  return h("div", { class: "servers" }, head,
    oldFp.length ? h("div", { class: "row", style: "flex-wrap: nowrap; gap: 10px; padding: 10px 14px; background: var(--warn-row); border-bottom: 1px solid var(--line-soft)" },
      h("span", { class: "xs grow", style: "color: var(--warn-deep); line-height: 1.5" },
        `${plural(oldFp.length, "link")} use${oldFp.length === 1 ? "s" : ""} a TLS fingerprint (fp=${(OLD_FP.exec(oldFp[0].split("#")[0]) || [])[2]}) that new REALITY servers reject.`),
      h("button", { type: "button", class: "btn xs", onclick: () => b.set(opt, links.map(fixFingerprint), true) }, "Use chrome")) : null,
    collapsible ? h("div", { class: "servers-more" }, h("span", { class: "xs muted" }, help), " ",
      h("button", { type: "button", class: "link-btn", onclick: () => { ui.open = false; w.redraw(); } }, "Hide")) : null,
    shown.map((l, i) => {
      const p = b.indexProblem(opt, i);
      const isNew = b.isNew || !old.includes(l);
      return h("div", { class: `srv${p ? " bad" : isNew ? " new" : ""}`, draggable: "true",
        ondragstart: (e) => { drag = i; e.dataTransfer.effectAllowed = "move"; e.currentTarget.classList.add("dragging"); },
        ondragend: (e) => e.currentTarget.classList.remove("dragging"),
        ondragover: (e) => e.preventDefault(),
        ondrop: (e) => { e.preventDefault(); if (drag !== null && drag !== i) move(drag, i); drag = null; } },
      h("span", { class: "grip", "aria-hidden": "true" }, icon("grip", 16)),
      h("div", { class: "col" },
        h("div", {}, h("span", { class: "nm" }, linkName(l)), h("span", { class: "proto" }, linkProto(l)),
          isNew ? h("span", { class: "tag ok" }, "new") : null),
        h("span", { class: "host ellipsis", title: linkDetails(l) }, linkDetails(l)),
        p ? h("span", { class: "err" }, p.message) : null),
      h("button", { type: "button", class: "btn icon", "aria-label": `Move ${linkName(l)} up`, title: "Move up", disabled: i === 0,
        onclick: () => move(i, i - 1) }, icon("up", 14, { width: 1.6 })),
      h("button", { type: "button", class: "btn icon", "aria-label": `Remove ${linkName(l)}`, onclick: () => b.set(opt, links.filter((_, j) => j !== i), true) },
        icon("trash", 14, { width: 1.6 })));
    }),
    links.length > 6 ? h("div", { class: "servers-more" }, h("button", { type: "button", class: "link-btn", onclick: () => { ui.all = !ui.all; w.redraw(); } },
      ui.all ? "Show fewer" : `Show ${links.length - 6} more`)) : null,
    h("div", { class: "servers-add" },
      h("div", { class: "row", style: "justify-content: space-between" },
        h("label", { for: `paste-${opt}`, class: "xs", style: "font-weight: 500; color: var(--text-2)" }, "Add servers"),
        h("button", { type: "button", class: "btn xs", onclick: addLinks }, "Add")),
      addArea, b.problemEls(opt)));
}

// --- routing rules -----------------------------------------------------------------------------------------

const CONNECTIONS = [["proxy", "Proxy"], ["vpn", "VPN"], ["block", "Block"], ["exclusion", "Direct"]];
const PROXY_TYPES = [["url", "One link"], ["selector", "Selector"], ["urltest", "Fastest (URLTest)"], ["outbound", "sing-box JSON"]];
const PROXY_HELP = {
  url: "One server.",
  selector: "Uses the first server until you switch it in the Clash dashboard.",
  urltest: "Uses the fastest server.",
  outbound: "Checked with sing-box before applying.",
};

async function viewRules(main, id, sub) {
  const w = await workFor(id);
  w.ui = w.ui || {};
  const page = h("div", { class: "page" });
  put(main, page);
  const bar = draftBar(w);
  const listBox = h("section", { "aria-label": "Rules", class: "stack", style: "flex: 1 1 400px; min-width: 0; gap: 10px" });
  const editBox = h("div", { style: "flex: 1.3 1 520px; min-width: 0" });
  put(page, pageHead("Routing rules"),
    bar, h("div", { class: "split" }, listBox, editBox));

  const selected = () => {
    const rules = rulesOf(w.config);
    return rules.find((s) => s.name === sub) || rules[0] || null;
  };
  w.redraw = () => keepFocus(() => { drawList(); drawEditor(); w.drawBar && w.drawBar(); });

  const reorder = (from, to) => {
    const rules = rulesOf(w.config);
    const moved = [...rules];
    moved.splice(to, 0, moved.splice(from, 1)[0]);
    let k = 0;
    w.config.sections = sectionsOf(w.config).map((s) => (s.type === "section" ? moved[k++] : s));
    touch(w); w.redraw();
  };

  const drawList = () => {
    const rules = rulesOf(w.config);
    const diff = workChanges(w);
    const origNames = new Set(rulesOf(w.orig).map((s) => s.name));
    const sel = selected();
    let drag = null;
    put(listBox,
      h("div", { class: "col", style: "padding: 0 2px 4px" }, h("h2", { class: "h2" }, "Rules"),
        h("p", { class: "small muted" }, "First match wins. Drag to reorder.")),
      rules.map((s, i) => {
        const n = diff.filter((d) => d.section === s.name && d.change === "changed").length;
        const auto = w.auto[s.name];
        const tag = !origNames.has(s.name) ? h("span", { class: "tag ok" }, "new") : n ? h("span", { class: "tag" }, plural(n, "change")) : null;
        return h("a", { href: `#/r/${id}/rules/${s.name}`, class: "rule", "aria-selected": String(sel === s), draggable: "true",
          title: "Drag, or Alt+Up / Alt+Down, to change the order",
          ondragstart: (e) => { drag = i; e.dataTransfer.effectAllowed = "move"; e.currentTarget.classList.add("dragging"); },
          ondragend: (e) => e.currentTarget.classList.remove("dragging"),
          ondragover: (e) => { e.preventDefault(); },
          ondrop: (e) => { e.preventDefault(); if (drag !== null && drag !== i) reorder(drag, i); drag = null; },
          onkeydown: (e) => {
            if (!e.altKey || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
            e.preventDefault();
            const to = i + (e.key === "ArrowUp" ? -1 : 1);
            if (to < 0 || to >= rules.length) return;
            reorder(i, to);
            listBox.querySelectorAll(".rule")[to].focus();
          } },
        h("span", { class: "grip" }, icon("grip", 18)),
        h("span", { class: "n" }, i + 1),
        h("div", { class: "col grow", style: "gap: 6px" },
          h("div", { class: "row" }, h("span", { class: "name" }, s.name), tag,
            auto ? h("span", { class: "tag", style: "background: var(--line-soft); color: var(--text-2)" }, "from probe") : null),
          h("div", { class: "what" }, h("span", {}, ruleMatches(s)), icon("arrow", 14, { stroke: "var(--muted)" }),
            auto ? h("span", { class: "dest" }, autoDest(auto, s)) : destChip(s))));
      }),
      h("div", { class: "rule-else" }, h("b", {}, "Everything else"), icon("arrow", 14, { stroke: "var(--muted)" }), h("span", {}, "Direct, not through podkop")),
      h("button", { type: "button", class: "btn lg", onclick: () => {
        let k = rules.length + 1;
        while (bySection(w.config).has(`rule_${k}`)) k++;
        w.config.sections.push({ name: `rule_${k}`, type: "section", anonymous: false,
          options: { connection_type: "proxy", proxy_config_type: "urltest", user_domain_list_type: "dynamic" } });
        touch(w);
        location.hash = `#/r/${id}/rules/rule_${k}`;
      } }, icon("plus", 15, { width: 1.6 }), "Add rule"));
  };

  const drawEditor = () => {
    const s = selected();
    if (!s) { put(editBox, h("div", { class: "card empty" }, "No rules yet. Add one to send some sites through a proxy, a VPN, block them or let them go direct.")); return; }
    put(editBox, ruleEditor(w, s, { onRename: (name) => { location.hash = `#/r/${id}/rules/${name}`; } }));
  };

  drawList(); drawEditor();
  let probeState;
  S.view = {
    leave: () => { w.drawBar = null; },
    onRouter: (r) => {
      if (r.id !== id || !r.status) return;
      if (r.status.fingerprint !== w.fp) w.fp = r.status.fingerprint;
      w.drawBar && w.drawBar();
    },
    // a probe started or finished: the rules that take its servers read them again
    onProbe: (m) => {
      if (m.router_id !== id || m.state === probeState) return;
      probeState = m.state;
      for (const k of Object.keys(w.ui)) if (k.endsWith(":auto")) w.ui[k].preview = null;
      w.redraw();
    },
  };
}

// "Proxy, PL servers, 4"
const autoDest = (rule, sec) => `Proxy, fastest ${asList((sec.options || {}).urltest_proxy_links).length} from probe${
  rule.countries.length ? ", " + countriesText(rule.countries) : ""}`;

function ruleEditor(w, s, { onRename }) {
  const defs = S.schema.section;
  const b = binder(w, s.name, defs, { defaults: false });
  b.name = s.name;
  const o = s.options || (s.options = {});
  const rules = rulesOf(w.config);
  const index = rules.indexOf(s);
  const earlier = rules.slice(0, index);
  const ct = o.connection_type || "proxy";
  const pt = o.proxy_config_type || "url";
  const auto = w.auto[s.name];

  const rename = async (name) => {
    name = name.trim();
    if (name === s.name) return;
    if (!/^[A-Za-z0-9_]+$/.test(name)) { toast("A rule name has letters, digits and _ only", true); w.redraw(); return; }
    if (bySection(w.config).has(name)) { toast(`There is already a rule ${name}`, true); w.redraw(); return; }
    const old = s.name;
    s.name = name;
    touch(w);
    // the probe parameters move first: the page that opens next reads them
    if (w.auto[old]) {
      try {
        const r = await api("PUT", `/api/routers/${w.id}/auto-rules/${name}`, w.auto[old]);
        await api("DELETE", `/api/routers/${w.id}/auto-rules/${old}`);
        delete w.auto[old]; w.auto[name] = r;
      } catch (e) { toast(e.message, true); }
    }
    onRename(name);
  };

  const duplicate = async () => {
    let name = `${s.name}_copy`, k = 2;
    while (bySection(w.config).has(name)) name = `${s.name}_copy${k++}`;
    const copy = clone(s); copy.name = name;
    w.config.sections.splice(w.config.sections.indexOf(s) + 1, 0, copy);
    touch(w);
    if (auto) {
      try { w.auto[name] = await api("PUT", `/api/routers/${w.id}/auto-rules/${name}`, auto); } catch (e) { toast(e.message, true); }
    }
    onRename(name);
  };

  const remove = () => {
    const m = modal({ title: `Delete rule ${s.name}?`, narrow: true,
      body: h("p", { class: "small" }, "It goes away from the draft; the router keeps it until you apply."),
      foot: [h("span"), h("div", { class: "row" },
        h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn danger", onclick: () => {
          m.close();
          w.config.sections = sectionsOf(w.config).filter((x) => x !== s);
          if (auto) api("DELETE", `/api/routers/${w.id}/auto-rules/${s.name}`).then(() => { delete w.auto[s.name]; }).catch(() => {});
          touch(w);
          location.hash = `#/r/${w.id}/rules`;
        } }, "Delete rule"))] });
  };

  // community lists: toggles; lists an earlier rule already catches are dashed
  const lists = asList(o.community_lists);
  const caughtBy = (l) => earlier.findIndex((x) => asList((x.options || {}).community_lists).includes(l));
  const allLists = [...new Set([...w.ctx.lists, ...lists])];
  const listUi = w.ui[`${s.name}:lists`] || (w.ui[`${s.name}:lists`] = { all: false });
  const visible = listUi.all ? allLists : allLists.filter((l, i) => i < 10 || lists.includes(l));
  const shadowed = lists.filter((l) => caughtBy(l) >= 0);
  const extraLists = ["remote_domain_lists", "remote_subnet_lists", "local_domain_lists", "local_subnet_lists"];
  const extraCount = extraLists.reduce((n, k) => n + asList(o[k]).length, 0);
  const extraUi = w.ui[`${s.name}:extra`] || (w.ui[`${s.name}:extra`] = { open: extraCount > 0 });

  const matches = h("div", { class: "part" },
    h("div", { class: "part-head" }, h("h3", {}, "Matches"),
      index > 0 ? h("span", {}, `Unless rule${index > 1 ? "s" : ""} 1${index > 1 ? "–" + index : ""} above catch${index > 1 ? "" : "es"} it first`) : null),
    h("div", { class: "field" },
      h("span", { class: "label" }, b.changed("community_lists") ? h("span", { class: "dot sm accent" }) : null, "Community lists"),
      h("div", { class: "row", style: "gap: 6px" },
        visible.map((l) => h("button", { type: "button", class: `toggle mono${caughtBy(l) >= 0 && !lists.includes(l) ? " dim" : ""}`,
          "aria-pressed": String(lists.includes(l)), title: caughtBy(l) >= 0 ? `rule ${caughtBy(l) + 1} already catches ${l}` : null,
          onclick: () => b.set("community_lists", lists.includes(l) ? lists.filter((x) => x !== l) : [...lists, l], true) }, l)),
        allLists.length > visible.length || listUi.all ? h("button", { type: "button", class: "toggle mono dim",
          onclick: () => { listUi.all = !listUi.all; w.redraw(); } }, listUi.all ? "fewer" : `${allLists.length - visible.length} more`) : null),
      shadowed.length ? h("span", { class: "help" }, `${shadowed.join(", ")} ${shadowed.length === 1 ? "is" : "are"} already caught by rule ${caughtBy(shadowed[0]) + 1}, so ${shadowed.length === 1 ? "it never reaches" : "they never reach"} this one.`) : null,
      b.problemEls("community_lists")),
    userListField(b, "domain"),
    h("div", { style: "display: grid; grid-template-columns: repeat(auto-fit, minmax(min(200px, 100%), 1fr)); gap: 10px" },
      userListField(b, "subnet"),
      !extraUi.open ? h("div", { class: "mini" }, h("span", {}, "Lists from URLs or files"),
        h("span", { class: "row" }, extraCount ? h("span", { class: "small muted" }, plural(extraCount, "list")) : null,
          h("button", { type: "button", class: "link-btn", onclick: () => { extraUi.open = true; w.redraw(); } }, extraCount ? "Edit" : "Add"))) : null),
    extraUi.open ? h("div", { class: "stack", style: "gap: 12px; padding: 12px; border: 1px solid var(--line); border-radius: 8px" },
      h("div", { class: "row", style: "justify-content: space-between" }, h("span", { style: "font-size: 13px; font-weight: 500" }, "Lists from URLs or files"),
        h("button", { type: "button", class: "link-btn", onclick: () => { extraUi.open = false; w.redraw(); } }, "Hide")),
      [["remote_domain_lists", "Domain lists from URLs", "https://… list URL, Enter"], ["remote_subnet_lists", "Subnet lists from URLs", "https://… list URL, Enter"],
        ["local_domain_lists", "Domain list files on the router", "/path/to/file, Enter"], ["local_subnet_lists", "Subnet list files on the router", "/path/to/file, Enter"]]
        .map(([opt, text, ph]) => h("div", { class: "field" }, lbl(b, opt, text), tokenField(b, opt, { placeholder: ph, splitter: /[\s,]+/ }), b.problemEls(opt)))) : null);

  const advanced = proxyAdvanced(w, b, s, ct, pt);
  const sendsHelp = ct === "exclusion" ? "Not through podkop" : null;

  const sends = h("div", { class: "part" },
    h("div", { class: "part-head" }, h("h3", {}, "Sends to"), sendsHelp ? h("span", {}, sendsHelp) : null),
    h("div", { class: "row", style: "gap: 14px 24px; align-items: flex-start" },
      h("div", { class: "field", role: "group", "aria-label": "Connection" }, h("span", { class: "label xs muted", style: "font-size: 12px" }, b.dot("connection_type"), "Connection"),
        seg(CONNECTIONS, ct, (v) => {
          if (v !== "proxy" && auto) toggleAuto(w, s, false);
          b.set("connection_type", v, true);
        }, { label: "Connection" })),
      null),
    b.problemEls("connection_type"),
    ct === "proxy" ? h("div", { class: "field", role: "group", "aria-label": "Servers" }, h("span", { class: "label xs muted", style: "font-size: 12px" }, b.dot("proxy_config_type"), "Servers"),
      seg(PROXY_TYPES, pt, (v) => {
        if (v !== "urltest" && auto) toggleAuto(w, s, false);
        b.set("proxy_config_type", v, true);
      }, { label: "Servers" })) : null,
    b.problemEls("proxy_config_type"),
    ct === "proxy" ? h("p", { class: "xs muted", style: "margin-top: -6px" }, PROXY_HELP[pt]) : null,
    ct === "proxy" && pt === "urltest" ? h("label", { class: "check" },
      h("input", { type: "checkbox", role: "switch", checked: !!auto, onchange: (e) => toggleAuto(w, s, e.target.checked) }),
      h("span", { class: "col" }, h("span", { style: "font-weight: 500" }, "Take links from probe"),
        auto ? null : h("span", {}, "The probe tests your keys and picks servers by region, ping and speed"))) : null,
    ct === "proxy" && pt === "urltest" && auto ? autoRuleEditor(w, s, auto) : null,
    ct === "proxy" && pt === "url" ? oneLinkField(b) : null,
    ct === "proxy" && pt === "selector" ? serversField(w, b, "selector_proxy_links", { title: "Servers" }) : null,
    ct === "proxy" && pt === "urltest" && !auto ? serversField(w, b, "urltest_proxy_links", { title: "Servers" }) : null,
    ct === "proxy" && pt === "urltest" && !auto ? serversField(w, b, "urltest_fallback_links", { title: "Fallback servers",
      help: "Used only when every server above fails", collapsible: true }) : null,
    ct === "proxy" && pt === "outbound" ? h("div", { class: "field" }, lbl(b, "outbound_json", "sing-box outbound (JSON)"),
      h("textarea", { class: "input", rows: 9, "data-k": "outbound_json", spellcheck: "false", oninput: (e) => b.set("outbound_json", e.target.value) }, o.outbound_json || ""),
      b.problemEls("outbound_json")) : null,
    ct === "vpn" ? h("div", { class: "form-grid" },
      lbl(b, "interface", "VPN interface", "vpn-if"),
      h("div", { class: "ctl" }, h("select", { id: "vpn-if", class: "input mid mono", "data-k": "interface", onchange: (e) => b.set("interface", e.target.value, true) },
        h("option", { value: "" }, "—"), [...new Set([...w.ctx.devices, ...(o.interface ? [o.interface] : [])])].map((d) => h("option", { value: d, selected: d === o.interface }, d))),
      b.was("interface"), b.problemEls("interface"))) : null,
    ct === "vpn" ? flagField(b, "domain_resolver_enabled", "Resolve this rule's domains through its own DNS server") : null,
    ct === "vpn" && b.val("domain_resolver_enabled") === "1" ? h("div", { class: "form-grid" },
      h("span", { class: "lbl" }, b.dot("domain_resolver_dns_type"), "DNS protocol"), h("div", { class: "ctl" }, segField(b, "domain_resolver_dns_type", ["doh", "dot", "udp"], ["DoH", "DoT", "UDP"])),
      lbl(b, "domain_resolver_dns_server", "DNS server"), h("div", { class: "ctl" }, comboField(b, "domain_resolver_dns_server", optionDef("x", "domain_resolver_dns_server").values), b.was("domain_resolver_dns_server"))) : null,
    advanced);

  return h("section", { class: "editor", "aria-label": `Edit rule ${s.name}` },
    h("div", { class: "top" },
      h("div", { class: "field", style: "flex: 1 1 220px" },
        h("label", { for: "rule-name", class: "xs muted", style: "font-size: 12px" }, "Rule name"),
        h("input", { id: "rule-name", class: "input mono", style: "height: 38px; font-size: 15px; font-weight: 500", value: s.name,
          onchange: (e) => rename(e.target.value), onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); } })),
      h("div", { class: "row", style: "gap: 6px" },
        h("button", { type: "button", class: "btn", onclick: duplicate }, "Duplicate"),
        h("button", { type: "button", class: "btn danger", onclick: remove }, "Delete rule"))),
    matches, sends);
}

function oneLinkField(b) {
  const l = b.get("proxy_string") || "";
  return h("div", { class: "field" }, lbl(b, "proxy_string", "Server link", "one-link"),
    h("input", { id: "one-link", class: "input mono", "data-k": "proxy_string", value: l, placeholder: "vless://…",
      oninput: (e) => b.set("proxy_string", e.target.value.trim()), onchange: () => b.set("proxy_string", b.get("proxy_string"), true) }),
    isLink(l) ? h("span", { class: "row xs" }, h("span", { style: "font-weight: 500" }, linkName(l)), h("span", { class: "proto" }, linkProto(l)),
      h("span", { class: "mono muted" }, linkDetails(l))) : null,
    oldFingerprint(l) ? h("span", { class: "row xs", style: "color: var(--warn-deep)" }, "Its TLS fingerprint is rejected by new REALITY servers.",
      h("button", { type: "button", class: "btn xs", onclick: () => b.set("proxy_string", fixFingerprint(l), true) }, "Use chrome")) : null,
    b.problemEls("proxy_string"), (b.indexProblem("proxy_string", 0) ? [h("span", { class: "field-problem" }, b.indexProblem("proxy_string", 0).message)] : []));
}

function proxyAdvanced(w, b, s, ct, pt) {
  if (!["proxy", "vpn"].includes(ct)) return null;
  const o = s.options;
  const known = new Set([...S.schema.section.map((d) => d.name), ...S.schema.internal]);
  const other = Object.keys(o).filter((k) => !known.has(k));
  const opts = ["urltest_check_interval", "urltest_tolerance", "urltest_testing_url", "urltest_download_check", "urltest_download_url",
    "enable_udp_over_tcp", "resolve_real_ip_for_routing", "mixed_proxy_enabled", "mixed_proxy_port", "fully_routed_ips"];
  const changed = opts.filter((k) => b.changed(k)).length;
  const nondef = opts.filter((k) => o[k] !== undefined).length;
  const ui = w.ui[`${s.name}:adv`] || (w.ui[`${s.name}:adv`] = { open: changed > 0 });
  const isUt = ct === "proxy" && pt === "urltest";
  const det = h("details", { class: "adv", open: ui.open, ontoggle: (e) => { ui.open = e.currentTarget.open; } },
    h("summary", {}, icon("right", 14, { width: 1.6, class: "chev" }), h("span", {}, isUt ? "Testing and proxy options" : "Proxy options"),
      h("span", {}, changed ? `${changed} changed${nondef > changed ? ", others set" : ", others default"}` : nondef ? `${nondef} set` : "all default")),
    h("div", { class: "adv-body" },
      isUt ? h("div", { class: "field" }, h("span", { class: "label xs" }, b.dot("urltest_check_interval"), "Check every"),
        segField(b, "urltest_check_interval", ["30s", "1m", "3m", "5m"]), b.was("urltest_check_interval")) : null,
      isUt ? h("div", { class: "field" }, lbl(b, "urltest_tolerance", "Switch only if faster by", "tol"),
        h("div", { class: "unit" }, h("input", { id: "tol", class: "input sm", style: "width: 80px", inputmode: "numeric", "data-k": "urltest_tolerance",
          value: o.urltest_tolerance ?? "", placeholder: "50", oninput: (e) => b.set("urltest_tolerance", e.target.value.trim()) }), h("span", {}, "ms")),
        b.was("urltest_tolerance"), b.problemEls("urltest_tolerance")) : null,
      isUt ? h("div", { class: "field" }, lbl(b, "urltest_testing_url", "Testing URL"),
        comboField(b, "urltest_testing_url", optionDef("x", "urltest_testing_url").values), b.was("urltest_testing_url")) : null,
      isUt ? h("div", { class: "field" }, lbl(b, "urltest_download_check", "Download check"),
        selectField(b, "urltest_download_check", ["default", "off", "custom"]), b.was("urltest_download_check")) : null,
      isUt && b.val("urltest_download_check") === "custom" ? h("div", { class: "field" }, lbl(b, "urltest_download_url", "Download check URL"),
        comboField(b, "urltest_download_url", optionDef("x", "urltest_download_url").values)) : null,
      ct === "proxy" ? flagField(b, "enable_udp_over_tcp", "UDP over TCP") : null,
      flagField(b, "resolve_real_ip_for_routing", "Resolve real IP for routing"),
      flagField(b, "mixed_proxy_enabled", "Mixed proxy port"),
      b.val("mixed_proxy_enabled") === "1" ? h("div", { class: "field" }, lbl(b, "mixed_proxy_port", "Port", "mport"),
        h("input", { id: "mport", class: "input sm", style: "width: 100px", inputmode: "numeric", "data-k": "mixed_proxy_port", value: o.mixed_proxy_port ?? "",
          oninput: (e) => b.set("mixed_proxy_port", e.target.value.trim()) }), b.problemEls("mixed_proxy_port")) : null,
      h("div", { class: "field", style: "grid-column: 1 / -1" }, lbl(b, "fully_routed_ips", "Fully routed IPs"),
        tokenField(b, "fully_routed_ips", { placeholder: "Device IP or subnet, Enter" }),
        h("span", { class: "help" }, "All traffic of these devices goes through this rule")),
      other.length ? h("div", { class: "field", style: "grid-column: 1 / -1" }, h("span", { class: "label" }, "Other options, kept as they are"),
        other.map((k) => h("span", { class: "mono xs muted" }, `${k} = ${asList(o[k]).join(", ")}`))) : null));
  return det;
}

// --- rules whose servers the probe picks from the subscriptions ----------------------------------------------------------

async function toggleAuto(w, s, on) {
  try {
    if (on) {
      w.auto[s.name] = await api("PUT", `/api/routers/${w.id}/auto-rules/${s.name}`, { countries: [], protocols: [], top: 5, fallback: 2,
        youtube: asList(s.options.community_lists).includes("youtube") ? "RU" : "" });
      const o = s.options;
      o.proxy_config_type = "urltest";
      touch(w);
    } else {
      await api("DELETE", `/api/routers/${w.id}/auto-rules/${s.name}`);
      delete w.auto[s.name];
    }
  } catch (e) { toast(e.message, true); }
  w.redraw();
}

// a rule's filter and limits for the probe's servers, what it matches now, and its servers in the draft
// The router's nightly probe: one run for every rule that takes links from
// it, so each such rule shows the same settings. Saved right away, not in the
// draft. key: a suffix for the element ids.
function nightlyProbeBlock(w, key, redraw) {
  const c = w.probeCfg || (w.probeCfg = { settings: null, loading: false, downloading: false });
  if (!c.settings) {
    if (!c.loading) {
      c.loading = true;
      Promise.all([api("GET", `/api/routers/${w.id}/probe`), S.manager || api("GET", "/api/manager")])
        .then(([p, m]) => { c.settings = p.settings; S.manager = m; c.loading = false; redraw(); })
        .catch((e) => { c.loading = false; toast(e.message, true); });
    }
    return null;
  }
  const probe = c.settings;
  let lp = (S.manager && S.manager.local_probe) || { ready: false, missing: "this manager is too old" };
  if (c.downloading) lp = Object.assign({}, lp, { missing: "podkop-engine is downloading…", engine: { state: "downloading" } });
  const save = async (patch) => {
    try { c.settings = await api("PUT", `/api/routers/${w.id}/probe`, patch); toast("Saved"); } catch (e) { toast(e.message, true); }
    redraw();
  };
  // podkop-engine for probes run on this server: the manager downloads it after it starts; this tries again
  const getEngine = busyClick(async () => {
    try {
      let e = await api("POST", "/api/engine/download");
      c.downloading = true; redraw();
      while (e.state === "downloading") {
        await new Promise((r) => setTimeout(r, 4000));
        S.manager = await api("GET", "/api/manager"); e = S.manager.local_probe.engine;
      }
      if (e.state === "failed") toast(`podkop-engine was not downloaded: ${e.error}`, true); else toast("podkop-engine downloaded");
    } catch (err) { toast(err.message, true); }
    c.downloading = false; redraw();
  });
  return h("div", { class: "stack", style: "gap: 12px; padding: 16px; border: 1px solid var(--line); border-radius: 10px" },
    h("div", { class: "part-head" }, h("span", { style: "font-size: 13.5px; font-weight: 600" }, "Nightly probe"),
      h("span", { class: "xs muted" }, "One run for all rules that take links from it. Saved right away")),
    h("div", { class: "form-grid" },
      h("label", { class: "lbl", for: `sched-${key}` }, "Every night at"),
      h("div", { class: "ctl" }, h("input", { id: `sched-${key}`, type: "time", class: "input mid num", style: "width: 110px", value: probe.schedule || "",
        onchange: (e) => save({ schedule: e.target.value }) }), h("span", { class: "help" }, "Empty: off"))),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!probe.auto_apply, onchange: (e) => save({ auto_apply: e.target.checked }) }),
      h("span", {}, "Apply the result by itself")),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!probe.fix_fingerprint, onchange: (e) => save({ fix_fingerprint: e.target.checked }) }),
      h("span", { class: "col" }, h("span", {}, "Use the chrome TLS fingerprint"),
        h("span", {}, "For REALITY links with fp=qq, 360, ios, android or edge, which new servers reject"))),
    h("div", { class: "form-grid" },
      h("label", { class: "lbl", for: `testfrom-${key}` }, "Test servers"),
      h("div", { class: "ctl" },
        h("select", { id: `testfrom-${key}`, class: "input mid", style: "width: auto", onchange: (e) => save({ test_from: e.target.value }) },
          h("option", { value: "router", selected: probe.test_from !== "manager" }, "On the router"),
          h("option", { value: "manager", selected: probe.test_from === "manager", disabled: !lp.ready && probe.test_from !== "manager" },
            "On this server, through the router")),
        h("span", { class: "help" }, probe.test_from === "manager"
          ? "The servers still see the router's address. Servers that use UDP (hysteria2, TUIC) are not tested."
          : lp.ready ? "On this server the test is faster and leaves the router's processor free" : `Not possible here yet: ${lp.missing}`),
        !lp.ready && /podkop-engine is (not downloaded|downloading)/.test(lp.missing || "")
          ? h("button", { type: "button", class: "btn sm", disabled: c.downloading, onclick: getEngine },
            lp.engine && lp.engine.state === "downloading" ? "Downloading podkop-engine…" : "Download podkop-engine, 13 MB") : null)),
    probe.test_from === "manager" ? h("div", { class: "col", style: "gap: 8px" },
      h("span", { class: "small" }, "The router must accept this server's forwarding key: Settings → Reinstall agent with \"Let this manager test servers through the router\" adds it, or add this line to the router's /etc/dropbear/authorized_keys. It lets this server open connections through the router and nothing else: no shell, no commands."),
      h("div", { class: "row", style: "align-items: flex-start; flex-wrap: nowrap; padding: 10px 12px; border-radius: 6px; background: var(--chip)" },
        h("code", { class: "grow", style: "font-size: 12px; line-height: 1.5; color: var(--text-2); word-break: break-all" }, lp.forward_line || lp.missing),
        lp.forward_line ? h("button", { type: "button", class: "btn xs", onclick: async () => {
          try { await navigator.clipboard.writeText(lp.forward_line); toast("Line copied"); } catch { toast("Select the line and copy it", true); }
        } }, "Copy") : null),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn sm", onclick: busyClick(async () => {
        try { toast((await api("POST", `/api/routers/${w.id}/probe/check-forward`)).message); } catch (e) { toast(e.message, true); }
      }) }, "Check the router"))) : null);
}

function autoRuleEditor(w, s, rule) {
  const box = h("div", { class: "stack", style: "gap: 16px" });
  const ui = w.ui[`${s.name}:auto`] || (w.ui[`${s.name}:auto`] = { preview: null, loading: false });
  let saveTimer;

  // the preview's servers go into the draft right away, so the rule can be applied now
  const fillDraft = (p) => {
    const links = p.matched.map((m) => m.link), fb = p.fallback.map((m) => m.link);
    if (!links.length || S.work[w.id] !== w) return;
    const o = s.options;
    if (same(asList(o.urltest_proxy_links), links) && same(asList(o.urltest_fallback_links), fb)) return;
    o.urltest_proxy_links = links;
    if (fb.length) o.urltest_fallback_links = fb; else delete o.urltest_fallback_links;
    touch(w);
    w.redraw();
  };
  const preview = async () => {
    ui.loading = true; keepFocus(draw);
    try { ui.preview = await api("GET", `/api/routers/${w.id}/auto-rules/${s.name}/preview`); fillDraft(ui.preview); }
    catch (e) { toast(e.message, true); }
    ui.loading = false;
    // fillDraft may have redrawn the editor: then this box is gone and the new one shows the result
    if (box.isConnected) keepFocus(draw); else if (S.work[w.id] === w) w.redraw();
  };
  const runProbe = async () => {
    try {
      await api("POST", `/api/routers/${w.id}/probe/start`);
      toast("Probe started, it takes 2 to 10 minutes");
      await preview();
    } catch (e) { toast(e.message, true); }
  };
  const update = (patch, now) => {
    Object.assign(rule, patch);
    clearTimeout(saveTimer);
    const go = async () => {
      try { w.auto[s.name] = Object.assign(rule, await api("PUT", `/api/routers/${w.id}/auto-rules/${s.name}`, rule)); await preview(); }
      catch (e) { toast(e.message, true); }
    };
    if (now) go(); else saveTimer = setTimeout(go, 600);
    keepFocus(draw);
  };

  const draw = () => {
    const p = ui.preview;
    const seen = (p && p.countries) || [];
    const matched = p ? p.matched : [], fb = p ? p.fallback : [];
    const run = (p && p.run) || {};
    const running = run.state === "running";
    const sched = w.probeCfg && w.probeCfg.settings ? w.probeCfg.settings.schedule : p && p.schedule;
    const eu = hasEurope(rule.countries);
    const chip = (text, label, without) => h("span", { class: "toggle", "aria-pressed": "true", style: "display: inline-flex; align-items: center; gap: 6px; padding: 0 4px 0 10px; height: 30px" },
      text, h("button", { type: "button", class: "btn icon", style: "width: 22px; height: 22px; color: inherit", "aria-label": `Remove ${label}`,
        onclick: () => update({ countries: rule.countries.filter((x) => !without.includes(x)) }, true) }, icon("close", 10, { width: 2 })));
    put(box,
      h("div", { class: "stack", style: "gap: 14px; padding: 16px; border: 1px solid var(--line); border-radius: 10px" },
        h("div", { class: "part-head" }, h("span", { style: "font-size: 13.5px; font-weight: 600" }, "Probe parameters"),
          h("a", { href: `#/r/${w.id}/usage`, class: "xs" }, p ? `Tests ${plural(p.keys, "subscription")} from Keys and usage` : "Keys and usage")),
        h("div", { class: "form-grid", style: "grid-template-columns: minmax(110px, 140px) minmax(0, 1fr); align-items: start" },
          p && p.subs.length ? [
            h("span", { class: "lbl", style: "font-weight: 500; padding-top: 6px" }, "Keys"),
            h("div", { class: "ctl", style: "gap: 6px" },
              h("div", { class: "row", style: "gap: 6px" }, p.subs.map((k) =>
                h("button", { type: "button", class: "toggle", "aria-pressed": String(rule.keys.includes(k.id)),
                  onclick: () => update({ keys: rule.keys.includes(k.id) ? rule.keys.filter((x) => x !== k.id) : [...rule.keys, k.id] }, true) }, k.label))),
              h("span", { class: "help" }, rule.keys.some((id) => p.subs.some((k) => k.id === id)) ? "Servers only from these keys" : "None chosen: servers from all keys"))] : null,
          h("span", { class: "lbl", style: "font-weight: 500; padding-top: 6px" }, "Exit country"),
          h("div", { class: "ctl", style: "gap: 6px" },
            h("div", { class: "row", style: "gap: 6px" },
              eu ? chip("Europe", "Europe", EUROPE) : null,
              rule.countries.filter((c) => !eu || !EUROPE.includes(c)).map((c) => chip(`${c}, ${countryName(c)}`, countryName(c), [c])),
              h("label", { class: "sr", for: "add-cc" }, "Add a country"),
              h("select", { id: "add-cc", class: "input sm", style: "width: auto; height: 30px; border-style: dashed; border-radius: 15px",
                onchange: (e) => {
                  let v = e.target.value;
                  if (v === "?") v = (prompt("Two-letter country code, e.g. PL") || "").trim().toUpperCase();
                  if (v === "europe") update({ countries: [...new Set([...rule.countries, ...EUROPE])].sort() }, true);
                  else if (/^[A-Z]{2}$/.test(v) && !rule.countries.includes(v)) update({ countries: [...rule.countries, v] }, true); else draw();
                } },
              h("option", { value: "" }, rule.countries.length ? "Add country" : "Any country, add one"),
              eu ? null : h("option", { value: "europe" }, "Europe, all countries"),
              seen.filter((c) => !rule.countries.includes(c) && !(eu && EUROPE.includes(c))).map((c) => h("option", { value: c }, `${c}, ${countryName(c)}`)),
              h("option", { value: "?" }, "Other…"))),
            h("span", { class: "help" }, "The country sites see")),
          h("span", { class: "lbl", style: "font-weight: 500; padding-top: 6px" }, "Protocol"),
          h("div", { class: "row", style: "gap: 6px" }, ["vless", "trojan", "ss", "hysteria2", "socks"].map((x) =>
            h("button", { type: "button", class: "toggle mono", "aria-pressed": String(rule.protocols.includes(x)),
              onclick: () => update({ protocols: rule.protocols.includes(x) ? rule.protocols.filter((y) => y !== x) : [...rule.protocols, x] }, true) }, x))),
          h("label", { for: "ipf", class: "lbl", style: "font-weight: 500; padding-top: 8px" }, "Exit IP"),
          h("div", { class: "ctl" }, h("input", { id: "ipf", class: "input mid mono", style: "max-width: 360px; font-size: 12.5px", "data-k": "auto-ip",
            placeholder: "Any. Or an IP or subnet, e.g. 203.0.113.0/24", value: ui.ip ?? rule.exit_ips.join(" "),
            oninput: (e) => { ui.ip = e.target.value; update({ exit_ips: e.target.value.split(/[\s,]+/).filter(Boolean) }); },
            onblur: () => { ui.ip = null; } }),
          h("span", { class: "help" }, "Several, separated by spaces")),
          h("label", { for: "namef", class: "lbl", style: "font-weight: 500; padding-top: 8px" }, "Name contains"),
          h("div", { class: "ctl" }, h("input", { id: "namef", class: "input mid", style: "max-width: 360px", "data-k": "auto-name", placeholder: "Any, e.g. Warsaw",
            value: rule.name_contains, oninput: (e) => update({ name_contains: e.target.value }) })),
          h("span", { class: "lbl", style: "font-weight: 500; padding-top: 8px" }, "Keep"),
          h("div", { class: "row small", style: "gap: 8px 10px" },
            h("label", { class: "sr", for: "keep" }, "Fastest servers to keep"),
            h("input", { id: "keep", class: "input mid", style: "width: 56px", inputmode: "numeric", "data-k": "auto-top", value: rule.top,
              oninput: (e) => { const n = parseInt(e.target.value, 10); if (n >= 1) update({ top: n }); } }),
            h("span", {}, "fastest and"),
            h("label", { class: "sr", for: "fb" }, "Fallback servers"),
            h("input", { id: "fb", class: "input mid", style: "width: 56px", inputmode: "numeric", "data-k": "auto-fb", value: rule.fallback,
              oninput: (e) => { const n = parseInt(e.target.value, 10); if (n >= 0) update({ fallback: n }); } }),
            h("span", {}, "fallback")),
          h("span", { class: "lbl", style: "font-weight: 500; padding-top: 8px" }, "Limits"),
          h("div", { class: "row small", style: "gap: 8px 10px" },
            h("label", { for: "maxp" }, "Ping under"),
            h("input", { id: "maxp", class: "input mid", style: "width: 64px", inputmode: "numeric", "data-k": "auto-ping", value: rule.max_ping,
              oninput: (e) => { const n = parseInt(e.target.value, 10); if (n >= 100) update({ max_ping: n }); } }),
            h("span", {}, "ms, speed over"),
            h("label", { class: "sr", for: "mins" }, "Speed over, Mbit/s"),
            h("input", { id: "mins", class: "input mid", style: "width: 56px", inputmode: "numeric", "data-k": "auto-speed", value: rule.min_speed,
              oninput: (e) => { const n = parseFloat(e.target.value); if (n >= 0) update({ min_speed: n }); } }),
            h("span", {}, "Mbit/s")),
          h("label", { for: "ytr", class: "lbl", style: "font-weight: 500; padding-top: 8px" }, "YouTube region"),
          h("div", { class: "ctl" },
            h("select", { id: "ytr", class: "input mid", style: "width: auto", onchange: (e) => {
              let v = e.target.value;
              if (v === "?") v = (prompt("Two-letter country code YouTube must detect, e.g. RU") || "").trim().toUpperCase();
              if (v === "" || v === "plays" || /^[A-Z]{2}$/.test(v)) update({ youtube: v }, true); else draw();
            } },
            [["", "Not checked"], ["plays", "Any, video plays"],
              ...[...new Set(["RU", ...seen, ...(/^[A-Z]{2}$/.test(rule.youtube) ? [rule.youtube] : [])])].map((c) => [c, `${c}, ${countryName(c)}${c === "RU" ? ", no ads" : ""}`]),
              ["?", "Other…"]].map(([v, t]) => h("option", { value: v, selected: v === rule.youtube }, t))),
            h("span", { class: "help" }, "The country YouTube detects through the server")))),
      h("div", { class: "servers" },
        h("div", { class: "part-head", style: "padding: 12px 14px" },
          h("div", { class: "col", style: "gap: 2px" },
            h("span", { style: "font-size: 13.5px; font-weight: 600" }, ui.loading && !p ? "Matches now" : `Matches now: ${plural(matched.length + fb.length, "server")}`),
            h("span", { class: "xs muted" }, running ? "The probe is running, 2 to 10 minutes" : ui.loading ? "Updating…" : p && p.finished
              ? `Probe at ${shortWhen(agentTime(p.finished))}.${sched ? ` Next at ${sched}.` : ""}` : p ? "No probe run yet" : "")),
          h("button", { type: "button", class: "btn sm", disabled: running || !p, onclick: runProbe },
            running ? spinner() : icon("probe", 14), running ? "Running" : "Run probe")),
        run.state === "failed" ? h("details", { style: "border-top: 1px solid var(--line-soft)" },
          h("summary", { class: "bad-text", style: "padding: 10px 14px; font-size: 13px; font-weight: 500" }, icon("right", 14, { width: 1.6, class: "chev" }),
            "The last probe run failed"),
          h("pre", { class: "out" }, (run.log || []).join("\n"))) : null,
        p && !p.has_links ? h("p", { class: "empty", style: "border-top: 1px solid var(--line-soft)" },
          "The last probe run has no server links (podkop-probe before 1.12 does not save them). Run the probe again to fill this rule.") : null,
        matched.length || fb.length ? h("div", { class: "scroll-x" }, h("table", {},
          h("tr", {}, h("th", { scope: "col" }, "Server"), h("th", { scope: "col" }, "Protocol"), h("th", { scope: "col" }, "Exit IP"),
            h("th", { scope: "col", class: "r" }, "Median"), h("th", { scope: "col", class: "r" }, "Mbit/s")),
          [...matched.map((m) => [m, false]), ...fb.map((m) => [m, true])].map(([m, isFb]) => h("tr", {},
            h("td", {}, m.name, isFb ? h("span", { class: "xs muted" }, " fallback") : null),
            h("td", { class: "mono-cell" }, m.protocol), h("td", { class: "mono-cell", style: "color: var(--text-2)" }, m.exit_ip || "–"),
            h("td", { class: "r num" }, Number.isFinite(parseFloat(m.median)) ? `${parseFloat(m.median).toFixed(2)} s` : "–"),
            h("td", { class: "r num" }, m.mbit && m.mbit !== "-" ? Math.round(+m.mbit) : "–"))))) : null,
        p && p.has_links && matched.length < rule.top ? h("div", { class: "row", style: "align-items: flex-start; flex-wrap: nowrap; gap: 10px; padding: 10px 14px; background: var(--warn-row); border-top: 1px solid var(--line-soft)" },
          h("span", { class: "warn-text", style: "flex: none" }, "!"),
          h("span", { class: "xs", style: "color: var(--warn-deep); line-height: 1.5" }, matched.length
            ? `Only ${matched.length} match, so podkop gets ${matched.length === 1 ? "this one" : `these ${matched.length}`} and no fallback. Add a country or another protocol for more. If none match after a run, the rule keeps its last list and you get a warning.`
            : rule.countries.length && seen.length && !rule.countries.some((c) => seen.includes(c))
              ? `No tested server exits in ${countriesText(rule.countries)}. Exits seen: ${seen.join(", ")}.`
              : "No server matches now. Widen the filter, or run the probe. If none match after a run, the rule keeps its last list and you get a warning.")) : null,
        p && p.left_out.length ? h("details", { style: "border-top: 1px solid var(--line-soft)" },
          h("summary", { style: "padding: 10px 14px; font-size: 13px; font-weight: 500" }, icon("right", 14, { width: 1.6, class: "chev" }),
            `${plural(p.left_out.length, "server")}${rule.countries.length === 1 ? ` in ${countryName(rule.countries[0])}` : ""} left out, and why`),
          h("div", { style: "padding: 0 14px 12px" }, p.left_out.map((x) => h("div", { class: "row xs", style: "padding: 4px 0; flex-wrap: nowrap" },
            h("span", { style: "font-weight: 500; flex: none" }, x.name), h("span", { class: "muted" }, x.reason))))) : null),
      nightlyProbeBlock(w, s.name, () => { if (box.isConnected) keepFocus(draw); }));
  };

  draw();
  if (!ui.preview) preview();
  return box;
}

// --- global settings ----------------------------------------------------------------------------------------

async function viewGlobal(main, id) {
  const w = await workFor(id);
  w.ui = w.ui || {};
  const page = h("div", { class: "page" });
  put(main, page);
  const defs = S.schema.settings;
  const headBtn = h("span", { style: "display: contents" });
  w.drawBar = () => {
    const n = workChanges(w).length;
    put(headBtn, n ? h("button", { type: "button", class: "btn primary", disabled: w.base !== w.fp, onclick: () => reviewWork(w) }, `Review and apply, ${n}`) : null);
  };
  w.redraw = () => keepFocus(draw);

  const draw = () => {
    if (!bySection(w.config).has("settings")) {
      put(page, h("div", { class: "card empty" }, "podkop has no settings section on this router."));
      return;
    }
    const b = binder(w, "settings", defs);
    const o = sectionsOf(w.config).find((s) => s.name === "settings").options;
    const proxyRules = rulesOf(w.config).filter((s) => ["proxy", "vpn"].includes((s.options || {}).connection_type)).map((s) => s.name);
    const card = (title, sub, ...body) => h("section", { class: "card sect", "aria-label": title },
      h("div", { class: "col" }, h("h2", {}, title), sub ? h("span", {}, sub) : null), body);
    const row = (opt, label, control, forId) => [h(forId ? "label" : "span", { class: "lbl", for: forId || null }, b.dot(opt), label),
      h("div", { class: "ctl" }, control, b.was(opt), b.problemEls(opt))];
    const devices = asList(b.val("source_network_interfaces"));
    put(page,
      pageHead("Global settings", "Dots mark values that differ from podkop's defaults", headBtn),
      w.base !== w.fp ? h("div", { class: "banner bad" }, h("span", { class: "dot bad" }),
        h("span", { class: "small bad-text grow" }, "podkop's settings changed on the router after this draft was started. Applying it would undo that, so the router refuses it."),
        h("button", { type: "button", class: "btn ghost", onclick: () => discardWork(w) }, "Discard draft")) : null,
      h("div", { class: "cards-2" },
        card("DNS", null,
          h("div", { class: "form-grid" },
            row("dns_type", "Protocol", segField(b, "dns_type", ["doh", "dot", "udp"], ["DoH", "DoT", "UDP"])),
            row("dns_server", "DNS server", comboField(b, "dns_server", optionDef("settings", "dns_server").values)),
            row("bootstrap_dns_server", "Bootstrap server", comboField(b, "bootstrap_dns_server", optionDef("settings", "bootstrap_dns_server").values)),
            row("dns_rewrite_ttl", "Rewrite TTL", h("div", { class: "unit" }, h("input", { class: "input mid", style: "width: 90px", inputmode: "numeric",
              "data-k": "dns_rewrite_ttl", value: o.dns_rewrite_ttl ?? "", placeholder: "60", oninput: (e) => b.set("dns_rewrite_ttl", e.target.value.trim()) }),
            h("span", {}, "seconds"))))),
        card("Which devices", null,
          h("div", { class: "row" }, b.changed("source_network_interfaces") ? h("span", { class: "dot sm accent" }) : null,
            [...new Set([...w.ctx.devices.filter((d) => /^br-|^wg|^tun|^lan|^eth/.test(d)), ...devices])].map((d) => h("label", { class: "box-check" },
              h("input", { type: "checkbox", checked: devices.includes(d), onchange: (e) => b.set("source_network_interfaces",
                e.target.checked ? [...devices, d] : devices.filter((x) => x !== d), true) }), d))),
          b.problemEls("source_network_interfaces"),
          h("div", { class: "stack sep", style: "gap: 10px" },
            flagField(b, "enable_output_network_interface", "Send through a set output interface", "Off: podkop uses the default route"),
            b.val("enable_output_network_interface") === "1" ? h("div", { class: "form-grid" }, row("output_network_interface", "Interface",
              h("select", { class: "input mid mono", onchange: (e) => b.set("output_network_interface", e.target.value, true) },
                h("option", { value: "" }, "—"), [...new Set([...w.ctx.devices, ...(o.output_network_interface ? [o.output_network_interface] : [])])]
                  .map((d) => h("option", { value: d, selected: d === o.output_network_interface }, d))))) : null,
            flagField(b, "enable_badwan_interface_monitoring", "Restart podkop when WAN goes down and up", "Bad WAN monitoring"),
            b.val("enable_badwan_interface_monitoring") === "1" ? h("div", { class: "form-grid" },
              row("badwan_monitored_interfaces", "Interfaces", h("div", { class: "row" }, w.ctx.interfaces.map((x) => {
                const cur = asList(o.badwan_monitored_interfaces);
                return h("label", { class: "box-check" }, h("input", { type: "checkbox", checked: cur.includes(x),
                  onchange: (e) => b.set("badwan_monitored_interfaces", e.target.checked ? [...cur, x] : cur.filter((y) => y !== x), true) }), x);
              }))),
              row("badwan_reload_delay", "Delay", h("div", { class: "unit" }, h("input", { class: "input mid", style: "width: 100px", inputmode: "numeric",
                "data-k": "badwan_reload_delay", value: o.badwan_reload_delay ?? "", placeholder: "2000",
                oninput: (e) => b.set("badwan_reload_delay", e.target.value.trim()) }), h("span", {}, "ms")))) : null)),
        card("Lists", null,
          h("div", { class: "form-grid" },
            row("update_interval", "Update every", segField(b, "update_interval", ["1h", "3h", "12h", "1d", "3d"])),
            h("span", { class: "lbl" }, b.dot("download_lists_via_proxy"), "Download"),
            h("div", { class: "ctl", style: "flex-direction: row; align-items: center; flex-wrap: wrap; gap: 8px 12px" },
              h("label", { class: "check inline" }, h("input", { type: "checkbox", checked: b.val("download_lists_via_proxy") === "1",
                onchange: (e) => b.set("download_lists_via_proxy", e.target.checked ? "1" : "0", true) }), "through rule"),
              h("label", { class: "sr", for: "via" }, "Rule to download lists through"),
              h("select", { id: "via", class: "input sm mono", style: "width: auto", disabled: b.val("download_lists_via_proxy") !== "1",
                onchange: (e) => b.set("download_lists_via_proxy_section", e.target.value, true) },
              h("option", { value: "" }, "—"), [...new Set([...proxyRules, ...(o.download_lists_via_proxy_section ? [o.download_lists_via_proxy_section] : [])])]
                .map((n) => h("option", { value: n, selected: n === o.download_lists_via_proxy_section }, n))),
              b.problemEls("download_lists_via_proxy_section")))),
        card("Traffic", null,
          h("div", { style: "display: grid; grid-template-columns: repeat(auto-fit, minmax(min(190px, 100%), 1fr)); gap: 12px 20px" },
            flagField(b, "disable_quic", "Disable QUIC"), flagField(b, "enable_utls_fallback", "uTLS fallback"),
            flagField(b, "exclude_ntp", "Exclude NTP"), flagField(b, "dont_touch_dhcp", "Don't touch DHCP")),
          h("div", { class: "field sep" }, lbl(b, "routing_excluded_ips", "IPs never routed"),
            tokenField(b, "routing_excluded_ips", { placeholder: "IP or subnet, Enter" }), b.problemEls("routing_excluded_ips"))),
        card("sing-box", null,
          h("div", { class: "form-grid" },
            row("config_path", "Config file", selectField(b, "config_path", optionDef("settings", "config_path").values, { mono: true })),
            row("cache_path", "Cache file", textField(b, "cache_path", { mono: true })),
            row("log_level", "Log level", selectField(b, "log_level", optionDef("settings", "log_level").values)))),
        card("YACD dashboard", null,
          flagField(b, "enable_yacd", "Turn on"),
          flagField(b, "enable_yacd_wan_access", "Reachable from WAN, with a secret", null, { disabled: b.val("enable_yacd") !== "1" }),
          b.val("enable_yacd_wan_access") === "1" ? h("div", { class: "form-grid" }, row("yacd_secret_key", "Secret", textField(b, "yacd_secret_key", { mono: true }))) : null)),
      settingsOther(o, defs));
    w.drawBar && w.drawBar();
  };

  draw();
  S.view = { leave: () => { w.drawBar = null; }, onRouter: (r) => { if (r.id === id && r.status && r.status.fingerprint !== w.fp) { w.fp = r.status.fingerprint; draw(); } } };
}

function settingsOther(o, defs) {
  const known = new Set([...defs.map((d) => d.name), ...S.schema.internal]);
  const other = Object.keys(o).filter((k) => !known.has(k));
  return h("p", { class: "small muted" }, "Other options are kept as they are",
    other.length ? `: ${other.join(", ")}.` : ".");
}

// --- saved configs: archive, export, import -------------------------------------------------------------------

async function viewConfigs(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let list = [];
  const load = async () => { list = await api("GET", "/api/configs"); };
  const run = async (fn, ok) => {
    try { await fn(); if (ok) toast(ok); await load(); draw(); } catch (e) { toast(e.message, true); }
  };

  const fileInput = h("input", { type: "file", hidden: true, onchange: async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) run(async () => api("POST", "/api/configs/import", { text: await file.text(), file: file.name }), `Imported ${file.name}`);
  } });

  const confirm = (title, text, label, cls, go) => {
    const m = modal({ title, narrow: true, body: h("p", { class: "small" }, text),
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: `btn ${cls}`, onclick: () => { m.close(); go(); } }, label))] });
  };

  const rename = (c) => {
    const input = h("input", { class: "input", id: "cfg-name", value: c.name });
    const save = () => { m.close(); run(() => api("PUT", `/api/configs/${c.id}`, { name: input.value })); };
    input.onkeydown = (e) => { if (e.key === "Enter") save(); };
    const m = modal({ title: "Rename config", narrow: true, body: h("div", { class: "field" }, h("label", { for: "cfg-name" }, "Name"), input),
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: save }, "Rename"))] });
    input.select();
  };

  const loadInto = (c) => {
    const rt = S.routers[id];
    confirm(`Load ${c.name}?`, `It becomes the draft of ${rt.name}${draftChanges(rt) ? ", replacing the draft you have" : ""}. Rules that take links from probe get the last probe's servers. Nothing changes on the router until you apply.`,
      "Load into draft", "primary", async () => {
        try { await api("POST", `/api/routers/${id}/configs/${c.id}/load`); location.hash = `#/r/${id}/rules`; }
        catch (e) { toast(e.message, true); }
      });
  };

  const draw = () => {
    const rt = S.routers[id];
    if (!rt) return;
    put(page,
      pageHead("Configs", "podkop's settings with each rule's probe parameters",
        h("button", { type: "button", class: "btn", onclick: () => fileInput.click() }, "Import file"),
        draftChanges(rt) ? h("button", { type: "button", class: "btn", onclick: busyClick(() => run(() => api("POST", "/api/configs", { router_id: id, from: "draft" }), "Draft saved")) }, "Save draft") : null,
        h("button", { type: "button", class: "btn primary", disabled: !rt.online,
          onclick: busyClick(() => run(() => api("POST", "/api/configs", { router_id: id }), "Saved")) }, `Save ${rt.name}'s config`)),
      fileInput,
      h("section", { class: "card clip", "aria-label": "Saved configs" },
        list.length ? h("div", { class: "scroll-x" }, h("table", { class: "wide" },
          h("tr", {}, h("th", { scope: "col" }, "Name"), h("th", { scope: "col" }, "From"), h("th", { scope: "col" }, "Saved"),
            h("th", { scope: "col" }, "Rules"), h("th", { scope: "col", class: "r" }, h("span", { class: "sr" }, "Actions"))),
          list.map((c) => h("tr", {},
            h("td", { style: "font-weight: 500" }, c.name),
            h("td", { style: "color: var(--text-2)" }, c.source, c.podkop ? h("span", { class: "xs muted" }, ` · podkop ${c.podkop}`) : null),
            h("td", { class: "num nowrap", style: "color: var(--text-2)" }, shortWhen(c.t)),
            h("td", { style: "color: var(--text-2)" }, c.rules.join(", ") || "–",
              c.from_probe.length ? h("span", { class: "xs muted" }, ` · ${c.from_probe.length} from probe`) : null),
            h("td", { class: "r nowrap" }, h("span", { class: "row", style: "gap: 6px; justify-content: flex-end; flex-wrap: nowrap" },
              h("button", { type: "button", class: "btn xs", onclick: () => loadInto(c) }, "Load into draft"),
              h("a", { class: "btn xs", href: `/api/configs/${c.id}/file`, download: "" }, "Download"),
              h("button", { type: "button", class: "btn xs", onclick: () => rename(c) }, "Rename"),
              h("button", { type: "button", class: "btn xs danger", onclick: () => confirm(`Delete ${c.name}?`, "The saved config goes away; routers keep what they run.",
                "Delete", "danger", () => run(() => api("DELETE", `/api/configs/${c.id}`), "Deleted")) }, "Delete")))))))
          : h("p", { class: "empty" }, "No saved configs. Save this router's config, or import a file: one downloaded here, or podkop's /etc/config/podkop.")));
  };

  await load();
  draw();
  S.view = { onRouter: (r) => { if (r.id === id) draw(); } };
}

// --- review and apply ------------------------------------------------------------------------------------

S.jobWatch = new Map();   // version id -> fn(job) for live job updates

// how many server links podkop's parser reads in a config
const linkCount = (config) => rulesOf(config).filter((s) => (s.options || {}).connection_type === "proxy").reduce((n, s) => {
  const o = s.options, pt = o.proxy_config_type || "url";
  return n + (pt === "url" ? (o.proxy_string ? 1 : 0) : pt === "selector" ? asList(o.selector_proxy_links).length
    : pt === "urltest" ? asList(o.urltest_proxy_links).length + asList(o.urltest_fallback_links).length : 0);
}, 0);

// the diff, one block per rule in the new order
function changesBlock(diff, config) {
  const rules = rulesOf(config).map((s) => s.name);
  const groups = new Map();
  for (const d of diff) {
    const k = d.change === "order" ? "\u0000order" : d.section;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(d);
  }
  const keys = [...groups.keys()].sort((a, b) => {
    const r = (k) => (k === "settings" ? -1 : k === "\u0000order" ? 1e6 : rules.includes(k) ? rules.indexOf(k) : 1e5);
    return r(a) - r(b);
  });
  return h("section", { class: "dchanges", "aria-label": "Changes" }, keys.map((k) => {
    const items = groups.get(k);
    if (k === "\u0000order") {
      return [h("div", { class: "sec" }, h("span", {}, "Rule order"), h("span", { class: "muted" }, "first match wins")),
        h("div", { class: "rows" }, h("span", {}, "New order"), h("span", { class: "mono xs" }, items[0].new.filter((n) => rules.includes(n)).join(" → ")))];
    }
    const added = items.find((d) => d.change === "added"), removed = items.find((d) => d.change === "removed");
    const pos = rules.indexOf(k) + 1;
    const head = h("div", { class: "sec" }, h("span", {}, k),
      added ? h("span", { class: "tag ok" }, "new rule") : removed ? h("span", { class: "tag bad" }, "removed") : null,
      h("span", { class: "muted" }, added ? `added as rule ${pos}` : removed ? "this rule goes away"
        : k === "settings" ? `global settings, ${plural(items.length, "setting")}` : `rule ${pos}, ${plural(items.length, "setting")}`));
    if (removed) return [head];
    if (added) {
      const sec = { name: k, options: added.options };
      const domains = asList(added.options.user_domains);
      return [head, h("div", { class: "rows" },
        h("span", {}, "Sends to"), h("span", {}, ruleDest(sec)[1] === "direct" && added.options.connection_type === "exclusion" ? "Direct, not through podkop" : ruleDest(sec)[0]),
        h("span", {}, "Matches"), domains.length ? chipList(domains, "add") : h("span", {}, ruleMatches(sec)))];
    }
    return [head, h("div", { class: "rows" }, changeRows(items, config).map(([, label, val]) => [h("span", {}, label), val]))];
  }));
}

const STEP_TEXT = {
  check: ["Checking the settings on the router", "Checked the settings on the router", "The router refused these settings"],
  write: ["Saving a backup and writing the new settings", "Saved a backup and wrote the new settings", "Could not write the new settings"],
  restart: ["Restarting podkop and checking that it runs", "Restarted podkop, it runs", "podkop did not come up"],
  reload: ["Only servers changed: sing-box takes them without a podkop restart", "sing-box took the new servers, podkop kept running", "sing-box could not take them in place, so podkop restarts"],
  revert: ["Putting the old settings back", "Put the old settings back", "podkop does not run with the old settings either"],
};

// steps [key, status (todo, now, done, fail), seconds] from the job's state and when each state began
function jobSteps(state, marks, now) {
  const secs = (a, b) => (marks[a] ? Math.round(((marks[b] || now) - marks[a]) / 1000) : null);
  const s = (key, status, from, to) => ({ key, status, t: status === "todo" ? null : secs(from, to) });
  switch (state) {
    case "pending": case "queued": case "checking":
      return [s("check", "now", "check"), s("write", "todo"), s("restart", "todo")];
    case "reloading": return [s("check", "done", "check", "reload"), s("write", "done"), s("reload", "now", "reload")];
    case "restarting": return [s("check", "done", "check", marks.reload ? "reload" : "restart"), s("write", "done"),
      marks.reload ? s("reload", "fail", "reload", "restart") : null, s("restart", "now", "restart")].filter(Boolean);
    case "reverting": return [s("check", "done", "check", "restart"), s("write", "done"), s("restart", "fail", "restart", "revert"), s("revert", "now", "revert")];
    case "applied": return marks.reload && !marks.restart
      ? [s("check", "done", "check", "reload"), s("write", "done"), s("reload", "done", "reload", "end")]
      : [s("check", "done", "check", "restart"), s("write", "done"), s("restart", "done", "restart", "end")];
    case "saved": return [s("check", "done", "check", "end"), s("write", "done"), { key: "saved", status: "done", t: null }];
    case "unchanged": return [s("check", "done", "check", "end"), { key: "unchanged", status: "done", t: null }];
    case "rejected": return [s("check", "fail", "check", "end")];
    case "reverted": return [s("check", "done", "check", "restart"), s("write", "done"), s("restart", "fail", "restart", "revert"), s("revert", "done", "revert", "end")];
    case "failed": return marks.revert ? [s("check", "done", "check", "restart"), s("write", "done"), s("restart", "fail", "restart", "revert"), s("revert", "fail", "revert", "end")]
      : [s("check", "done", "check", "end"), s("write", "fail")];
    default: return [s("check", "now", "check")];
  }
}

function stepRow(st) {
  const text = st.key === "saved" ? ["", "podkop is stopped, so it was not restarted", ""] : st.key === "unchanged" ? ["", "Nothing to change on the router", ""] : STEP_TEXT[st.key];
  const label = st.status === "done" ? text[1] : st.status === "fail" ? text[2] : text[0];
  const ic = st.status === "now" ? spinner() : h("span", { class: `step-icon ${st.status}` },
    st.status === "done" ? icon("check", 12, { stroke: "#FFFFFF", width: 2.4 }) : st.status === "fail" ? icon("close", 12, { stroke: "#FFFFFF", width: 2.4 }) : null);
  return h("div", { class: `step ${st.status}` }, h("span", { style: "display: inline-flex; width: 20px; justify-content: center" }, ic),
    h("span", {}, label), h("span", {}, st.t !== null && st.t !== undefined ? `${st.t} s` : ""));
}

let applyOpening = false;     // a second click while the router is asked: no second dialog

async function openApply(id, { base, config, note, trigger, done }) {
  const rt = S.routers[id];
  if (applyOpening) return;
  applyOpening = true;
  let d, versions;
  try {
    [d, versions] = await Promise.all([api("POST", `/api/routers/${id}/diff`, { config }),
      api("GET", `/api/routers/${id}/versions?limit=50`).catch(() => [])]);
  } catch (e) { toast(e.message, true); return; } finally { applyOpening = false; }
  if (!d.diff.length) { toast("Nothing to apply: the router already has these settings"); return; }
  const running = runningVersion(rt, versions);
  const stale = d.fingerprint !== base;
  const n = d.diff.length;

  const checks = h("section", { class: "checks wait", "aria-label": "Checks" }, h("span", {}, "Checking on the router…"));
  const noteInput = h("input", { id: "apply-note", class: "input", value: note || "", placeholder: diffSummary(d.diff) });
  const steps = h("section", { class: "steps", "aria-label": "Progress", "aria-live": "polite", hidden: true });
  const result = h("div", { "aria-live": "polite" });
  const info = h("span", {}, "Will be saved in History");
  const close = h("button", { type: "button", class: "btn" }, "Close, keep going");
  const go = h("button", { type: "button", class: "btn primary", disabled: true }, "Apply");
  let problems = null, finished = false;

  const m = modal({
    title: `Apply ${plural(n, "change")} to ${rt.name}`,
    sub: stale ? "podkop's settings changed on the router after this draft was started. Applying it would undo that, so the router will refuse it."
      : `Compared with what the router runs now${running ? ` (#${running.id})` : ""}. Nobody changed it on the router in the meantime.`,
    body: [changesBlock(d.diff, config), checks,
      h("div", { class: "field" }, h("label", { for: "apply-note" }, "Note for history"), noteInput),
      steps, result,
      h("div", { class: "assure" }, icon("shield", 18, { stroke: "var(--info)", width: 1.6 }),
        h("p", {}, `If podkop does not come up, the router puts ${running ? `#${running.id}` : "the current settings"} back by itself within about 30 s. This works even if you close this page or the manager goes offline.`))],
    foot: [info, h("div", { class: "row" }, close, go)],
  });
  close.onclick = () => m.close();

  const showChecks = () => {
    if (problems === null) return;
    if (problems.length) {
      put(checks, h("span", {}, "podkop would not start with these settings"),
        problems.map((p) => h("span", { class: "bad-text" }, problemText(p))));
      checks.className = "checks bad";
    } else {
      const links = linkCount(config);
      put(checks, h("span", {}, "Checked on the router, podkop accepts these settings"),
        links ? h("span", {}, icon("check", 14, { stroke: "var(--ok)", width: 2 }), `${plural(links, "link")} read by podkop's parser`) : null,
        h("span", {}, icon("check", 14, { stroke: "var(--ok)", width: 2 }), "Required fields set"),
        h("span", {}, icon("check", 14, { stroke: "var(--ok)", width: 2 }), "Lists and domains valid"));
      checks.className = "checks";
    }
    go.disabled = stale || problems.length > 0 || finished;
  };
  api("POST", `/api/routers/${id}/validate`, { config }).then((r) => { problems = r.problems; showChecks(); })
    .catch((e) => { put(checks, h("span", {}, `Could not check: ${e.message}`)); checks.className = "checks bad"; go.disabled = stale; });

  go.onclick = async () => {
    go.disabled = true; go.textContent = "Applying"; noteInput.disabled = true;
    steps.hidden = false;
    const marks = { check: Date.now() };
    let state = "pending", timer, vid;
    const draw = () => put(steps, jobSteps(state, marks, Date.now()).map(stepRow));
    const onJob = (job) => {
      if (finished || !job || !job.state || job.state === state) return;
      state = job.state;
      const t = Date.now();
      if (state === "reloading") marks.reload = marks.reload || t;
      if (state === "restarting") marks.restart = marks.restart || t;
      if (state === "reverting") { marks.restart = marks.restart || marks.check; marks.revert = t; }
      if (FINAL_STATES.includes(state)) end(job, t);
      draw();
    };
    const end = (job, t) => {
      finished = true; marks.end = t; clearInterval(timer); S.jobWatch.delete(vid);
      const ok = ["applied", "saved", "unchanged"].includes(job.state);
      if (job.problems && job.problems.length) { problems = job.problems; showChecks(); }
      put(result, h("div", { class: `result ${ok ? "ok" : "bad"}` }, ok ? (job.state === "applied" ? (job.hot ? "Applied without restarting podkop: only servers changed." : "Applied, podkop is running.") : job.message)
        : job.state === "reverted" ? `podkop did not start with these settings, so the router put the old ones back: ${job.reason || job.message}` : job.message));
      go.textContent = ok ? "Done" : "Applying failed";
      close.textContent = "Close";
      go.disabled = !ok;
      go.onclick = () => m.close();
      done && done(ok);
    };
    draw();
    timer = setInterval(draw, 1000);
    try {
      const r = await api("POST", `/api/routers/${id}/apply`, { base, config, note: noteInput.value.trim() || diffSummary(d.diff), trigger });
      vid = r.version;
      put(info, h("span", {}, `Saved as version #${vid} in `, h("a", { href: `#/r/${id}/history`, onclick: () => m.close() }, "History")));
      S.jobWatch.set(vid, onJob);
      const poll = async () => {
        if (finished) return;
        const v = await api("GET", `/api/versions/${vid}`).catch(() => null);
        if (v && !["pending", "running"].includes(v.state)) onJob(Object.assign({}, v.details || {}, { state: v.state, message: v.message }));
        else setTimeout(poll, 3000);
      };
      setTimeout(poll, 3000);
    } catch (e) {
      clearInterval(timer); finished = true;
      put(result, h("div", { class: "result bad" }, e.message));
      go.textContent = "Apply"; close.textContent = "Close";
    }
  };
}

// --- settings (the manager's) -------------------------------------------------------------------------------

const splitIps = (v) => v.split(/[\s,]+/).filter(Boolean).join(" ");

async function viewSettings(main) {
  const info = S.manager || (S.manager = await api("GET", "/api/manager"));
  // the command that installs podkop-agent and this manager's key on a router, from the options
  const commandPart = (prefix) => {
    const close = h("input", { type: "checkbox", checked: true });
    const allow = h("input", { class: "input mono", id: `${prefix}-clash`, placeholder: "e.g. 192.168.1.10, optional" });
    const forward = h("input", { type: "checkbox" });
    const code = h("code", { class: "grow", style: "font-size: 12px; line-height: 1.5; color: var(--text-2); word-break: break-all" }, "…");
    let cmd = "", timer = null;
    const update = async () => {
      try {
        cmd = (await api("POST", "/api/install-command", { close_clash: close.checked, allow_clash: splitIps(allow.value), forward: forward.checked })).command;
        put(code, cmd);
      } catch (e) { cmd = ""; put(code, e.message); }
    };
    close.onchange = forward.onchange = update;
    allow.oninput = () => { clearTimeout(timer); timer = setTimeout(update, 400); };
    update();
    return [
      h("label", { class: "check" }, close, h("span", { class: "col" }, h("span", {}, "Close podkop's Clash API (port 9090) to the LAN"),
        h("span", {}, "Anyone on the LAN can switch its servers otherwise. Off: the firewall is not changed."))),
      h("div", { class: "field", style: "max-width: 560px" }, h("label", { for: `${prefix}-clash` }, "Computers that may still reach the Clash API"), allow,
        h("span", { class: "help" }, "Only when it is closed")),
      info.local_probe ? h("label", { class: "check" }, forward, h("span", { class: "col" },
        h("span", {}, "Let this manager test servers through the router"),
        h("span", {}, "Adds a second key that may only open connections through the router: no shell, no commands"))) : null,
      h("span", { style: "font-size: 13px; font-weight: 500" }, "Run this on the router as root, over SSH"),
      h("div", { class: "row", style: "align-items: flex-start; flex-wrap: nowrap; padding: 10px 12px; border-radius: 6px; background: var(--chip)" },
        code, h("button", { type: "button", class: "btn xs", onclick: async () => {
          if (!cmd) return;
          try { await navigator.clipboard.writeText(cmd); toast("Command copied"); } catch { toast("Select the command and copy it", true); }
        } }, "Copy")),
    ];
  };
  const page = h("div", { class: "page" });
  put(main, page);

  const rename = (rt) => {
    const input = h("input", { class: "input", id: "rn", value: rt.name });
    const save = async () => {
      try { S.routers[rt.id] = await api("PATCH", `/api/routers/${rt.id}`, { name: input.value.trim() }); m.close(); draw(); drawSide(); }
      catch (e) { toast(e.message, true); }
    };
    input.onkeydown = (e) => { if (e.key === "Enter") save(); };
    const m = modal({ title: `Rename ${rt.name}`, narrow: true, body: h("div", { class: "field" }, h("label", { for: "rn" }, "Name"), input),
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: save }, "Rename"))] });
    input.select();
  };

  const reinstall = (rt) => {
    const m = modal({ title: `Reinstall podkop-agent on ${rt.name}`, narrow: true,
      sub: "Puts this manager's podkop-agent, podkop-probe and key on the router again. podkop's settings stay as they are.",
      body: [commandPart("ri")],
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: busyClick(async () => {
          try { await api("POST", `/api/routers/${rt.id}/refresh`); m.close(); toast(`${rt.name} checked`); } catch (e) { toast(e.message, true); }
        }) }, "Done, check the router"))] });
  };

  const remove = (rt) => {
    const also = h("input", { type: "checkbox", checked: true });
    const go = h("button", { type: "button", class: "btn danger" }, "Remove");
    go.onclick = async () => {
      go.disabled = true;
      try {
        const r = await api("DELETE", `/api/routers/${rt.id}?uninstall=${also.checked ? 1 : 0}`);
        delete S.routers[rt.id];
        m.close(); toast(r.note || `${rt.name} removed`, !!r.note);
        draw(); drawSide();
      } catch (e) { toast(e.message, true); go.disabled = false; }
    };
    const m = modal({ title: `Remove ${rt.name}?`, narrow: true,
      body: [h("label", { class: "check" }, also, h("span", { class: "col" }, h("span", {}, "Also remove podkop-agent from the router"),
        h("span", {}, "Its key, firewall rules and watchdog go too. podkop and its settings stay."))),
      h("p", { class: "small muted" }, "History, logs and probe settings of this router are deleted from the manager.")],
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"), go)] });
  };

  const f = {
    name: h("input", { class: "input", id: "a-name", placeholder: "e.g. country-house" }),
    pairing: h("input", { class: "input mono", id: "a-pair", placeholder: "podkop-router 192.168.1.1 22 ssh-ed25519 AAAA…", autocomplete: "off",
      oninput: () => {
        const p = f.pairing.value.trim().split(/\s+/);
        if (p[0] === "podkop-router" && p.length === 5) { f.host.placeholder = p[1]; f.port.placeholder = p[2]; }
      } }),
    host: h("input", { class: "input mono", id: "a-host", placeholder: "from the line" }),
    port: h("input", { class: "input", id: "a-port", placeholder: "from the line", inputmode: "numeric" }),
  };
  const addOut = h("div");
  const addBtn = h("button", { type: "submit", class: "btn primary lg" }, "Add");
  const addForm = h("form", { class: "card pad", "aria-labelledby": "s-add", onsubmit: async (e) => {
    e.preventDefault();
    if (!f.pairing.value.trim()) { f.pairing.focus(); return; }
    addBtn.disabled = true; put(addOut, h("p", { class: "small muted" }, "Checking the router…"));
    try {
      const r = await api("POST", "/api/routers", { name: f.name.value.trim(), pairing: f.pairing.value.trim(),
        host: f.host.value.trim(), port: +f.port.value || null });
      await loadRouters();
      location.hash = `#/r/${r.id}`;
    } catch (err) { put(addOut, h("p", { class: "problem-box" }, err.message)); addBtn.disabled = false; }
  } },
  h("div", { class: "col" }, h("h2", { id: "s-add" }, "Add router"),
    h("span", { class: "small muted" }, "podkop stays as it is: its settings are kept in Configs. The probe needs podkop-engine.")),
  commandPart("a"),
  h("div", { class: "field" }, h("label", { for: "a-pair" }, "The line the command printed at the end"), f.pairing),
  h("div", { class: "auto-grid", style: "grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr))" },
    h("div", { class: "field" }, h("label", { for: "a-name" }, "Name"), f.name),
    h("div", { class: "field" }, h("label", { for: "a-host" }, "Address"), f.host, h("span", { class: "help" }, "If this server reaches the router at another address")),
    h("div", { class: "field" }, h("label", { for: "a-port" }, "SSH port"), f.port)),
  addOut, h("div", {}, addBtn));

  const restore = () => {
    const file = h("input", { type: "file", id: "bak-file", class: "input", accept: ".gz,.tgz,application/gzip" });
    const out = h("div");
    const go = h("button", { type: "button", class: "btn danger" }, "Restore");
    go.onclick = async () => {
      const f = file.files[0];
      if (!f) { file.focus(); return; }
      go.disabled = true; put(out, h("p", { class: "small muted" }, "Restoring…"));
      try {
        const r = await fetch("/api/backup", { method: "POST", headers: { "X-Podkop-Manager": "1", "Content-Type": "application/gzip" }, body: f });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || r.statusText);
        location.hash = "#/"; location.reload();
      } catch (e) { put(out, h("p", { class: "problem-box" }, e.message)); go.disabled = false; }
    };
    const m = modal({ title: "Restore a backup?", narrow: true,
      body: [h("p", { class: "small" }, "Everything in this manager is replaced by the backup: routers, keys, rules, configs, history and the SSH key."),
        h("div", { class: "field" }, h("label", { for: "bak-file" }, "Backup file"), file), out],
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"), go)] });
  };

  const draw = () => {
    const list = routerList();
    put(page,
      pageHead("Settings"),
      h("section", { class: "card clip", "aria-labelledby": "s-routers" },
        h("div", { class: "card-head" }, h("div", { class: "col" }, h("h2", { id: "s-routers" }, "Routers"),
          list.length ? null : h("span", { class: "small muted" }, "None yet: add your first router below"))),
        list.length ? h("div", { class: "scroll-x" }, h("table", { class: "wide" },
          h("tr", {}, h("th", { scope: "col" }, "Name"), h("th", { scope: "col" }, "Address"), h("th", { scope: "col" }, "State"),
            h("th", { scope: "col" }, "podkop-agent"), h("th", { scope: "col", class: "r" }, h("span", { class: "sr" }, "Actions"))),
          list.map((rt) => {
            const st = routerState(rt);
            const label = !rt.online && rt.down_since ? `offline since ${shortWhen(rt.down_since)}` : st.label;
            return h("tr", {},
              h("td", { style: "font-weight: 500" }, h("a", { href: `#/r/${rt.id}`, style: "color: inherit" }, rt.name)),
              h("td", { class: "mono-cell", style: "color: var(--text-2)" }, `${rt.host}:${rt.port}`),
              h("td", {}, h("span", { class: `pill ${st.cls}` }, label)),
              h("td", { style: "color: var(--text-2)" }, rt.status ? rt.status.agent.version : "–"),
              h("td", { class: "r nowrap" }, h("span", { class: "row", style: "gap: 6px; justify-content: flex-end; flex-wrap: nowrap" },
                h("button", { type: "button", class: "btn xs", onclick: () => rename(rt) }, "Rename"),
                h("button", { type: "button", class: "btn xs", onclick: () => reinstall(rt) }, "Reinstall agent"),
                h("button", { type: "button", class: "btn xs danger", onclick: () => remove(rt) }, "Remove"))));
          }))) : null),
      addForm,
      h("div", { style: "display: grid; grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr)); gap: 16px; align-items: start" },
        h("section", { class: "card pad", "aria-labelledby": "s-mgr", style: "gap: 12px" },
          h("div", { class: "col" }, h("h2", { id: "s-mgr" }, "This manager"), h("span", { class: "small muted" }, `podkop-manager ${info.version}`)),
          h("span", { style: "font-size: 13px; font-weight: 500" }, "SSH key the routers accept"),
          h("div", { class: "row", style: "align-items: flex-start; flex-wrap: nowrap; padding: 10px 12px; border-radius: 6px; background: var(--chip)" },
            h("code", { class: "grow", style: "font-size: 12px; line-height: 1.5; color: var(--text-2); word-break: break-all" }, info.public_key),
            h("button", { type: "button", class: "btn xs", onclick: async () => {
              try { await navigator.clipboard.writeText(info.public_key); toast("Key copied"); } catch { toast("Select the key and copy it", true); }
            } }, "Copy"))),
        h("section", { class: "card pad", "aria-labelledby": "s-acc", style: "gap: 12px" },
          h("h2", { id: "s-acc" }, "Login"),
          h("span", { class: "small muted" }, "Login and password are set in .env (PODKOP_MANAGER_USER, PODKOP_MANAGER_PASSWORD)"),
          h("div", { class: "row" },
            h("form", { method: "post", action: "/logout", style: "display: contents" }, h("button", { type: "submit", class: "btn" }, "Log out")))),
        h("section", { class: "card pad", "aria-labelledby": "s-bak", style: "gap: 12px" },
          h("h2", { id: "s-bak" }, "Backup"),
          h("span", { class: "small muted" }, "Everything in one file: routers, keys, rules, configs, history and the SSH key. Keep it private."),
          h("div", { class: "row" },
            h("a", { class: "btn", href: "/api/backup", download: "" }, "Download backup"),
            h("button", { type: "button", class: "btn", onclick: restore }, "Restore…")))));
  };

  draw();
  S.view = { onRouter: () => { if (!page.querySelector(":focus")) draw(); } };
}

// --- start --------------------------------------------------------------------------------

async function loadRouters() {
  S.routers = {};
  for (const r of await api("GET", "/api/routers")) S.routers[r.id] = r;
}

function connect() {
  const es = new EventSource("/api/stream");
  const live = (on) => {
    if (S.live === on) return;
    S.live = on; drawSide();
    S.view && S.view.onLive && S.view.onLive();
  };
  es.onopen = () => live(true);
  es.onerror = () => {
    live(false);
    // a 401 or a proxy error closes it for good: check the login, then connect again
    if (es.readyState === EventSource.CLOSED) {
      setTimeout(() => api("GET", "/api/manager").then(connect).catch(() => setTimeout(connect, 10000)), 3000);
    }
  };
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    const v = S.view || {};
    if (m.type === "router") {
      S.routers[m.router.id] = m.router;
      if (!switchOpen) drawSide();
      v.onRouter && v.onRouter(m.router);
    } else if (m.type === "event") {
      v.onEvent && v.onEvent(m);
      if (["reverted", "revert_failed", "apply_interrupted", "podkop_down", "recovery_failed"].includes(m.event)) {
        toast(`${(S.routers[m.router_id] || {}).name}: ${eventText(m.event, m.data)}`, true);
      }
    } else if (m.type === "log") v.onLog && v.onLog(m);
    else if (m.type === "probe") v.onProbe && v.onProbe(m);
    else if (m.type === "usage") v.onUsage && v.onUsage(m);
    else if (m.type === "job") {
      const fn = S.jobWatch.get(m.version);
      if (fn) fn(m.job);
      v.onJob && v.onJob(m);
    }
  };
}

(async () => {
  [S.schema] = await Promise.all([api("GET", "/api/schema"), loadRouters()]);
  connect();
  window.addEventListener("hashchange", render);
  render();
})();
