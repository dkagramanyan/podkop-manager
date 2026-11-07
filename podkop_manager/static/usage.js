// usage.js: part of podkop-manager's web interface (see app.js)

import { pageHead, seg } from "./shell.js";
import { S } from "./state.js";
import { ago, api, duration, h, hhmm, icon, linkHost, linkProto, longWhen, modal, plural, put, toast } from "./ui.js";

// --- keys and usage -----------------------------------------------------------------------------------

// sub.example.com/s/…k9Q: enough to tell keys apart, not enough to use one
export function keyLabel(url) {
  try {
    const u = new URL(url);
    const path = u.pathname + u.search;
    return path.length > 8 ? `${u.host}${path.slice(0, path.indexOf("/", 1) + 1 || 1)}…${path.slice(-3)}` : u.host + path;
  } catch { return url.slice(0, 12) + "…"; }
}

export const countryName = (cc) => {
  try { return new Intl.DisplayNames(["en"], { type: "region" }).of(cc); } catch { return cc; }
};

// podkop-probe's "europe" (EUROPE in router/probe.sh)
export const EUROPE = ("EU AD AL AT BA BE BG CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS "
  + "SE SI SK SM UA VA XK").split(" ");
export const hasEurope = (cs) => EUROPE.every((c) => cs.includes(c));
// "Europe, TR" or "DE, PL"
export const countriesText = (cs) => (hasEurope(cs) ? ["Europe", ...cs.filter((c) => !EUROPE.includes(c))] : cs).join(", ");

export const GB = 1024 ** 3;
export const gb = (b) => (b >= GB / 10 || !b ? `${(b / GB).toFixed(b >= 100 * GB ? 0 : 1)} GB` : `${Math.round(b / 1024 ** 2)} MB`);
export const usageUnit = (k) => (k.daily ? "today" : k.period === "month" ? "month" : "period");
export const shade = (i) => `var(--shade-${Math.min(i, 3) + 1})`;

export function usagePill(k) {
  if (k.pct === null) return h("span", { class: "pill" }, "no limit");
  return h("span", { class: `pill ${k.pct >= k.threshold ? "bad" : k.pct >= 60 ? "warn" : "ok"}` }, `${Math.round(k.pct)}% of ${usageUnit(k)}`);
}

// the running total of today with a dashed forecast at the last 3 hours' rate
export function usageChart(k) {
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

export async function viewUsage(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let data, histKey = null;
  const load = async () => { data = await api("GET", `/api/routers/${id}/usage`); };
  const save = async (k, body) => {
    try { await api("PUT", `/api/keys/${k.id}`, body); await load(); draw(); }
    catch (e) { toast(e.message, true); }
  };

  const kindText = (k) => (k.kind === "sub" ? `subscription · ${keyLabel(k.url)}` : `single link · ${linkProto(k.url)}://…@${linkHost(k.url)}`);
  const bar = (k) => (k.limit ? h("div", { class: `ubar${k.pct >= 100 ? " over" : ""}`, role: "progressbar", "aria-label": `${k.label} traffic`,
    "aria-valuemin": "0", "aria-valuemax": String(k.limit), "aria-valuenow": String(k.used) },
  h("div", { style: `width: ${Math.min(100, k.pct)}%` }),
  k.daily ? h("span", { class: "mark", style: `left: ${k.threshold}%`, title: `${k.threshold}%` }) : null) : null);
  const source = (k) => (k.limit_source === "provider" ? `from the provider${k.read ? `, read ${ago(k.read)}` : ""}` : "set by you, counted on the router");
  const when = (t) => new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  const resets = (k) => (k.daily ? `resets ${k.reset}` : k.period === "month" ? "per month" : k.expire ? `ends ${when(k.expire)}` : "no daily limit");
  // what the last probe found of the key's servers
  const probeText = (k) => {
    const p = k.probe || {};
    if (!p.tested) return h("span", { class: "muted" }, `${plural(k.servers || 0, "server")}, not tested yet`);
    const bad = p.flaky + p.bad;
    return h("span", { class: bad && !p.good ? "bad-text" : "" },
      `${p.good} of ${p.tested} good${p.flaky ? `, ${p.flaky} flaky` : ""}${p.bad ? `, ${p.bad} down` : ""}`);
  };
  const actionPick = (k) => h("div", { class: "row", style: "gap: 8px" },
    h("label", { for: `act-${k.id}`, class: "small muted" }, `At ${k.threshold}%`),
    h("select", { id: `act-${k.id}`, class: "input", style: "width: auto; height: 32px", onchange: (e) => save(k, { action: e.target.value }) },
      h("option", { value: "avoid", selected: k.action === "avoid" }, "Move rules to other keys"),
      h("option", { value: "warn", selected: k.action === "warn" }, "Only warn me")));
  const editBtn = (k) => h("button", { type: "button", class: "btn xs", onclick: () => editKey(k) }, "Edit");

  const byRule = (k) => {
    const tot = k.by_rule.reduce((a, r) => a + r.bytes, 0);
    if (!tot) return null;
    return h("div", { class: "col", style: "gap: 6px" }, h("span", { class: "small", style: "font-weight: 500" }, "Today by rule"),
      h("div", { class: "ustack" }, k.by_rule.map((r, i) => h("div", { title: `${r.rule} ${gb(r.bytes)}`,
        style: `width: ${(r.bytes / tot) * 100}%; background: ${shade(i)}` }))),
      h("div", { class: "legend" }, k.by_rule.map((r, i) => h("span", {}, h("i", { style: `background: ${shade(i)}` }), `${r.rule} ${gb(r.bytes)}`))));
  };

  // one row per key; a row opens to its chart, split by rule, limit and what happens at the mark
  const open = new Set();
  const details = (k) => {
    const facts = [
      ["Limit", k.limit ? `${gb(k.limit)} per ${k.daily ? "day" : usageUnit(k)}, ${source(k)}` : k.kind === "sub" && !k.read ? "waiting for the first reading" : "none"],
      ["Key expires", k.expire ? `${when(k.expire)}, in ${plural(Math.max(0, Math.round((k.expire - data.now) / 86400)), "day")}` : "–"],
      ["Last probe", k.probe && k.probe.finished ? longWhen(Date.parse(k.probe.finished) / 1000) : "not tested yet"]];
    return h("div", { class: "key-details" },
      h("div", { class: "stack", style: "flex: 1 1 300px; min-width: 0; gap: 12px" },
        k.daily && k.limit ? h("div", { class: "row small muted", style: "justify-content: space-between" },
          h("span", {}, `${gb(Math.max(0, k.limit - k.used))} left, resets in ${duration(Math.max(0, k.next_reset - data.now))}`),
          h("span", {}, `mark at ${k.threshold}%`)) : null,
        byRule(k),
        h("div", { class: "key-facts" }, facts.map(([x, y]) => h("div", {}, h("span", {}, x), h("span", {}, y)))),
        k.why ? h("span", { class: "small bad-text" }, k.why) : null,
        k.limit ? actionPick(k) : null),
      h("div", { style: "flex: 1 1 340px; min-width: 0" }, k.series.length > 1 ? usageChart(k)
        : h("p", { class: "muted small" }, k.limit ? "The chart fills in as traffic is counted." : "Set a limit (Edit) to see the day's chart and get a warning at the mark.")));
  };
  const keyRow = (k) => {
    const isOpen = open.has(k.id);
    const toggle = () => { if (isOpen) open.delete(k.id); else open.add(k.id); draw(); };
    return [h("div", { class: `key-row${isOpen ? " open" : ""}`, id: `key-${k.id}`, role: "row" },
      h("button", { type: "button", class: "btn icon xs", "aria-expanded": String(isOpen), "aria-label": `${isOpen ? "Hide" : "Show"} details of ${k.label}`, onclick: toggle },
        icon(isOpen ? "down" : "right", 14)),
      h("div", { class: "col key-name" }, h("a", { href: "#", class: "key-title", onclick: (e) => { e.preventDefault(); toggle(); } }, k.label),
        h("span", { class: "mono xs muted ellipsis" }, kindText(k))),
      h("div", {}, usagePill(k)),
      h("div", { class: "col key-use" },
        h("span", { class: "small" }, h("b", {}, gb(k.used)), h("span", { class: "muted" }, k.limit ? ` of ${gb(k.limit)} ${usageUnit(k)}` : ` ${usageUnit(k)}`)),
        bar(k)),
      h("span", { class: "small muted" }, resets(k)),
      h("span", { class: "small" }, probeText(k)),
      h("span", { class: "small mono ellipsis", title: k.rules.join(", ") }, k.rules.length ? k.rules.join(", ") : h("span", { class: "muted" }, "no rule")),
      editBtn(k)),
    isOpen ? details(k) : null];
  };
  const keysCard = () => h("section", { class: "card clip", "aria-labelledby": "u-keys" },
    h("div", { class: "card-head" }, h("div", { class: "col" }, h("h2", { id: "u-keys" }, "Keys"),
      h("span", { class: "small muted" }, "Subscriptions and single links; the probe tests the servers of both and the rules pick from them."))),
    data.keys.length ? h("div", { class: "key-list", role: "table", "aria-label": "Keys" },
      h("div", { class: "key-row head", role: "row" }, h("span"), h("span", {}, "Key"), h("span", {}, "Status"), h("span", {}, "Used"),
        h("span", {}, "Period"), h("span", {}, "Servers, last probe"), h("span", {}, "Used by rules"), h("span")),
      data.keys.map(keyRow)) : h("p", { class: "empty" }, "No keys yet: add a subscription or a single link."));

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

  const addKey = () => {
    const f = { link: h("input", { class: "input mono", id: "nk-link", placeholder: "https://… subscription, or vless://… link" }),
      label: h("input", { class: "input", id: "nk-label", placeholder: "e.g. fra-personal" }),
      limit: h("input", { class: "input mid", id: "nk-limit", inputmode: "decimal", placeholder: "10" }) };
    const out = h("div");
    const go = h("button", { type: "button", class: "btn primary" }, "Add");
    go.onclick = async () => {
      go.disabled = true;
      try {
        const link = f.link.value.trim();
        await api("POST", `/api/routers/${id}/keys`, { link, label: f.label.value.trim(), limit_gb: f.limit.value || 0 });
        m.close();
        if (/^https?:\/\//.test(link)) {
          toast("Subscription added: its usage shows up in a minute, its servers after the next probe run");
          api("POST", `/api/routers/${id}/usage/refresh`).then((d) => { data = d; draw(); }).catch(() => {});
        } else toast("Link added: the probe tests it at its next run, then rules can pick it");
        await load(); draw();
      } catch (err) { put(out, h("p", { class: "problem-box" }, err.message)); go.disabled = false; }
    };
    const m = modal({ title: "Add a key", narrow: true,
      sub: "A subscription URL (its servers come from the provider) or one server link of your own; the probe tests both.",
      body: h("div", { class: "stack" },
        h("div", { class: "field" }, h("label", { for: "nk-link" }, "Subscription URL or link"), f.link),
        h("div", { class: "auto-grid", style: "grid-template-columns: repeat(auto-fit, minmax(min(160px, 100%), 1fr))" },
          h("div", { class: "field" }, h("label", { for: "nk-label" }, "Name"), f.label),
          h("div", { class: "field" }, h("label", { for: "nk-limit" }, "GB per day"), h("div", { class: "unit" }, f.limit, h("span", {}, "GB")),
            h("span", { class: "help" }, "For a link; a subscription's limit comes from the provider"))),
        out),
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"), go)] });
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

  const addBtn = h("button", { type: "button", class: "btn primary", onclick: () => addKey() }, icon("plus", 15), "Add key");
  const draw = () => {
    const rt = S.routers[id];
    const old = rt && rt.status && rt.status.agent.version.localeCompare("0.2.0", undefined, { numeric: true }) < 0;
    const tot = data.servers.reduce((x, y) => x + y.bytes, 0);
    put(page,
      pageHead("Keys and usage", `${plural(data.keys.length, "key")} · ${gb(tot)} through podkop since 00:00`, refresh, addBtn),
      old ? h("div", { class: "banner" }, h("span", { class: "dot warn" }),
        h("span", { class: "grow" }, "podkop-agent on this router is too old to count traffic."), h("a", { class: "alink", href: "#/settings" }, "Reinstall agent")) : null,
      warnings(),
      keysCard(), history(), servers(),
      h("p", { class: "small muted" }, "Provider numbers are read every 10 minutes; the split by rule and server is counted on the router."));
  };

  await load();
  draw();
  S.view = { onUsage: (m) => { if (m.router_id === id && !document.querySelector(".modal-bg") && !page.querySelector("form :focus")) load().then(draw).catch(() => {}); } };
}

// busyClick(fn): a click handler that ignores clicks while its work runs (the button is disabled meanwhile)
export const busyClick = (fn) => async (e) => {
  const b = e && e.currentTarget;
  if (b) { if (b.disabled) return; b.disabled = true; b.setAttribute("aria-busy", "true"); }
  try { await fn(e); } finally { if (b) { b.disabled = false; b.removeAttribute("aria-busy"); } }
};
