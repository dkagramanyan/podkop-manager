// ui.js: part of podkop-manager's web interface (see app.js)

// --- helpers --------------------------------------------------------------------

export function h(tag, props, ...kids) {
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
export function put(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false)
    .map((c) => (c instanceof Node ? c : document.createTextNode(String(c)))));
}

export const ICONS = {
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

export function icon(name, size = 16, opts = {}) {
  const t = document.createElement("template");
  const box = name === "shield" ? "0 0 20 20" : name === "grip" ? "0 0 12 18" : "0 0 16 16";
  const paint = name === "grip" ? 'fill="currentColor"' : `fill="none" stroke="${opts.stroke || "currentColor"}" stroke-width="${opts.width || 1.5}"`;
  const w = name === "grip" ? Math.round(size * 2 / 3) : size;
  t.innerHTML = `<svg width="${w}" height="${size}" viewBox="${box}" ${paint} aria-hidden="true">${ICONS[name]}</svg>`;
  const svg = t.content.firstChild;
  if (opts.class) svg.setAttribute("class", opts.class);
  return svg;
}

export function spinner() {
  const t = document.createElement("template");
  t.innerHTML = '<svg class="spin" width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="7.5" stroke="var(--accent-ring)" stroke-width="2.5"/><path d="M10 2.5a7.5 7.5 0 0 1 7.5 7.5" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round"/></svg>';
  return t.content.firstChild;
}

export async function api(method, path, body) {
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

export let toastTimer;
export function toast(msg, bad) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), bad ? 8000 : 3500);
}

// a dialog over the page; returns {close, el}; Escape and a click outside close it
export function modal({ title, sub, body, foot, narrow, onclose }) {
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

export const clone = (x) => JSON.parse(JSON.stringify(x));
export const asList = (v) => (v === undefined || v === null || v === "" ? [] : Array.isArray(v) ? v : [v]);
export const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
export const sameDay = (a, b) => a.toDateString() === b.toDateString();
export const hhmm = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

// 23:58 today, Thu this week, Oct 16 before
export function shortWhen(t) {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  if (sameDay(d, now)) return hhmm(d);
  if (now - d < 6 * 86400e3) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// Today 23:59, Yesterday 04:30, Oct 16, 18:12
export function longWhen(t) {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (sameDay(d, now)) return `Today ${hhmm(d)}`;
  if (sameDay(d, y)) return `Yesterday ${hhmm(d)}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${hhmm(d)}`;
}

export function ago(t) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return longWhen(t);
}

export function duration(s) {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

// agent times are UTC like 2026-10-17T21:03:05Z
export const agentTime = (s) => (s ? Date.parse(s) / 1000 : null);

export function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(`podkop-manager.${key}`);
    localStorage.setItem(`podkop-manager.${key}`, value);
  } catch { /* private mode, blocked storage */ }
  return null;
}

// --- links: name, protocol, host --------------------------------------------------

export function linkName(link) {
  const i = link.indexOf("#");
  if (i >= 0) {
    try { return decodeURIComponent(link.slice(i + 1).replace(/\+/g, " ")); } catch { return link.slice(i + 1); }
  }
  return linkHost(link);
}

export function linkProto(link) {
  const m = /^([a-z0-9]+):\/\//i.exec(link);
  const p = m ? m[1].toLowerCase() : "?";
  return p === "hy2" ? "hysteria2" : p === "shadowsocks" ? "ss" : p;
}

export function linkHost(link) {
  let rest = link.replace(/^[a-z0-9]+:\/\//i, "").split("#")[0];
  rest = rest.slice(rest.lastIndexOf("@") + 1);
  return rest.split(/[?/]/)[0];
}

// host:port · transport, security
export function linkDetails(link) {
  const q = new URLSearchParams((link.split("#")[0].split("?")[1]) || "");
  const extra = [q.get("type"), q.get("security") && q.get("security") !== "none" ? q.get("security") : null].filter(Boolean);
  return linkHost(link) + (extra.length ? ` · ${extra.join(", ")}` : "");
}

export const isLink = (v) => typeof v === "string" && /^[a-z0-9]+:\/\//i.test(v);

// TLS fingerprints REALITY servers since Xray-core v26.9.8 reject (podkop.OLD_FINGERPRINTS)
export const OLD_FP = /([?&]fp=)(qq|360|ios|android|edge)(?=[&#]|$)/;
export const oldFingerprint = (l) => isLink(l) && /security=reality/.test(l.split("#")[0]) && OLD_FP.test(l.split("#")[0]);
export const fixFingerprint = (l) => {
  if (!oldFingerprint(l)) return l;
  const i = l.indexOf("#");
  return i < 0 ? l.replace(OLD_FP, "$1chrome") : l.slice(0, i).replace(OLD_FP, "$1chrome") + l.slice(i);
};
