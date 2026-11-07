// logs.js: part of podkop-manager's web interface (see app.js)

import { pageHead, seg } from "./shell.js";
import { S } from "./state.js";
import { api, h, hhmm, icon, put, toast } from "./ui.js";

// --- logs ------------------------------------------------------------------------------------------

export const LOG_SOURCES = [["podkop", "podkop"], ["sing-box", "sing-box"], ["engine", "engine"], ["agent", "agent"]];
export const LOG_LEVELS = { all: ["debug", "info", "warn", "error"], warn: ["warn", "error"], error: ["error"] };

export async function viewLogs(main, id) {
  // noise: failed reverse lookups devices ask by themselves (see _NOISE in podkop.py), hidden unless asked
  const f = { q: "", sources: new Set(), level: "all", noise: false, follow: true, buffer: null };
  const levels = () => [...LOG_LEVELS[f.level], ...(f.noise ? ["noise"] : [])];
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
    && levels().includes(r.level)
    && (!f.q || r.line.toLowerCase().includes(f.q.toLowerCase()));
  const toBottom = () => { box.scrollTop = box.scrollHeight; };

  const fill = (rows) => {
    put(lines, rows.length ? rows.map(lineEl) : h("p", { class: "empty" }, f.q || f.sources.size || f.level !== "all"
      ? "No line matches these filters." : "No lines stored yet. New lines arrive while the router is connected."));
    toBottom();
  };
  const load = async () => {
    const qs = new URLSearchParams({ limit: "1000", q: f.q, source: [...f.sources].join(","), level: levels().join(",") });
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
    h("label", { class: "check inline", title: "Failed reverse lookups (in-addr.arpa) that devices ask by themselves: nothing breaks" },
      h("input", { type: "checkbox", checked: f.noise, disabled: !!f.buffer, onchange: (e) => { f.noise = e.target.checked; load(); } }),
      "Show unimportant lines"),
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
