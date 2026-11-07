// shell.js: part of podkop-manager's web interface (see app.js)

import { viewConfigs } from "./configs.js";
import { viewGlobal } from "./global.js";
import { viewHistory } from "./history.js";
import { viewLogs } from "./logs.js";
import { viewOverview } from "./overview.js";
import { viewReliability } from "./reliability.js";
import { viewRules } from "./rules.js";
import { viewSettings } from "./settings.js";
import { S } from "./state.js";
import { ago, h, icon, plural, put, shortWhen, store } from "./ui.js";
import { viewUsage } from "./usage.js";

// --- routers ------------------------------------------------------------------------

export function routerState(rt) {
  if (!rt.online) return { cls: "bad", label: rt.checked ? "offline" : "connecting" };
  const p = rt.status.podkop;
  if (p.busy) return { cls: "warn", label: "applying" };
  if (p.paused) return { cls: "warn", label: "stopped" };
  if (!p.enabled) return { cls: "warn", label: "disabled" };
  if (!p.running) return { cls: "bad", label: "podkop down" };
  return { cls: "ok", label: "running" };
}

// a change undone on the router in the last day
export const recentAlert = (rt) => (rt.alert && Date.now() / 1000 - rt.alert.t < 86400 ? rt.alert : null);
export const alertText = (a) => (a.type === "group_down" ? `a rule's servers stopped answering ${ago(a.t)}`
  : a.type === "schedule_failed" ? `a scheduled change was not applied ${ago(a.t)}` : `a change was undone ${ago(a.t)}`);
export const draftChanges = (rt) => (rt.draft && rt.draft.changes) || 0;

// "running, 4 changes not applied" and its color, for the router list
export function routerLine(rt) {
  const st = routerState(rt);
  if (!rt.online) {
    const since = rt.down_since ? ` since ${shortWhen(rt.down_since)}` : "";
    return { cls: "bad", text: `offline${since}${rt.error ? ", " + rt.error : ""}` };
  }
  const a = recentAlert(rt);
  if (a) return { cls: "warn", text: `${st.label}, ${alertText(a)}` };
  const n = draftChanges(rt);
  return { cls: "", text: n ? `${st.label}, ${plural(n, "change")} not applied` : st.label };
}

export const routerList = () => Object.values(S.routers).sort((a, b) => a.id - b.id);

// --- routes ------------------------------------------------------------------------------

export const SECTIONS = {
  "": { title: "Overview", view: (m, id) => viewOverview(m, id) },
  usage: { title: "Keys and usage", view: (m, id) => viewUsage(m, id) },
  rules: { title: "Routing rules", view: (m, id, sub) => viewRules(m, id, sub) },
  global: { title: "Global settings", view: (m, id) => viewGlobal(m, id) },
  configs: { title: "Configs", view: (m, id) => viewConfigs(m, id) },
  reliability: { title: "Connection health", view: (m, id) => viewReliability(m, id) },
  logs: { title: "Logs", view: (m, id) => viewLogs(m, id) },
  history: { title: "History", view: (m, id) => viewHistory(m, id) },
};

export function route() {
  const m = /^#\/r\/(\d+)(?:\/([a-z]+)(?:\/([A-Za-z0-9_]+))?)?$/.exec(location.hash);
  if (m) return { rid: +m[1], section: m[2] || "", sub: m[3] || "" };
  if (location.hash === "#/settings") return { settings: true };
  return { home: true };
}

export const lastRouter = () => {
  const id = +store("router");
  return S.routers[id] ? id : (routerList()[0] || {}).id;
};

// A page is built off screen and swapped in when its data is there, so the
// old page stays until then (dimmed after a moment) instead of a blank one.
export let renders = 0;

export async function render() {
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

export let switchOpen = false;

export function drawSide() {
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
      link("reliability", "Connection health", "probe"),
      link("logs", "Logs", "logs"),
      link("history", "History", "history")) : null,
    h("div", { class: "nav nav-bottom" },
      h("a", { href: "#/settings", "aria-current": r.settings ? "page" : null }, icon("settings"), "Settings")));
}

export function switcher(rt) {
  const st = routerState(rt);
  const others = routerList().filter((x) => x.id !== rt.id);
  const alerts = others.map((x) => {
    if (!x.online) return { x, text: `${x.name} is offline` };
    const a = recentAlert(x);
    return a ? { x, text: `${x.name}: ${alertText(a)}` } : null;
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

export function pageHead(title, sub, ...actions) {
  return h("header", { class: "head" },
    h("div", { class: "title" }, h("h1", {}, title), sub ? h("p", {}, sub) : null),
    actions.length ? h("div", { class: "row" }, actions) : null);
}

export function getRouter(id) {
  const rt = S.routers[id];
  if (!rt) throw new Error("no such router");
  return rt;
}

// seg([["udp", "UDP"], ...], value, onchange): a segmented control
export function seg(options, value, onchange, { small, label, disabled } = {}) {
  return h("div", { class: `seg${small ? " sm" : ""}`, role: "group", "aria-label": label || null },
    options.map(([v, text]) => h("button", { type: "button", "aria-pressed": String(v === value), disabled,
      onclick: () => { if (v !== value) onchange(v); } }, text)));
}
