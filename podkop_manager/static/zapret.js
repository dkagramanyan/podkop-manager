// zapret.js: part of podkop-manager's web interface (see app.js)

import { S } from "./state.js";
import { api, h, put, toast } from "./ui.js";
import { busyClick } from "./usage.js";

// --- zapret first (agent 0.29): a rule's web traffic goes out direct through
// zapret2 while that opens its sites, and through its servers otherwise ---

export const ZAPRET_SERVICES = [["youtube", "YouTube"], ["meta", "Meta"], ["twitter", "X"], ["telegram", "Telegram"],
  ["discord", "Discord"], ["other", "Other"]];

export const zapretSupported = (rt) => ((((rt || {}).status || {}).agent || {}).capabilities || []).includes("zapret");

// the rules list: a rule on zapret, or one zapret's check moved to its servers
export function zapretTag(rt, s) {
  const g = (((rt || {}).proxies || {}).groups || []).find((x) => x.name === `${s.name}-zapret-out`);
  if (!g) return null;
  return g.now === `${s.name}-zapret-direct` ? h("span", { class: "tag ok", title: "Its web traffic goes out direct, through zapret2" }, "zapret")
    : h("span", { class: "tag warn", title: "zapret does not open its sites now: the rule uses its servers" }, "zapret → servers");
}

const ms = (x) => (x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${x} ms`);

// the rule page's part; w.zapret holds what GET .../zapret said (loaded once per page)
export function zapretField(w, s, redraw) {
  const rt = S.routers[w.id];
  if (!zapretSupported(rt) || (s.options || {}).connection_type !== "proxy") return null;
  const box = h("div", { class: "stack", style: "gap: 10px", "aria-label": "Zapret first" });
  const z = w.zapret;
  if (!z) {
    if (!w.zapretLoading) {
      w.zapretLoading = api("GET", `/api/routers/${w.id}/zapret`)
        .then((r) => { w.zapret = r; }).catch((e) => { w.zapret = { error: e.message }; })
        .finally(() => { w.zapretLoading = null; redraw(); });
    }
    return h("span", { class: "small muted" }, "Zapret first: …");
  }
  if (z.error) return h("span", { class: "small bad-text" }, `Zapret first: ${z.error}`);
  const st = z.router;
  const on = st.rules[s.name];
  const known = z.rules.find((r) => r.name === s.name);       // the rule is on the router (not only in the draft)
  const refused = (st.refused || []).includes(s.name);
  const run = z.run && z.run.rules.includes(s.name) ? z.run : null;
  const running = !!(z.run && z.run.state === "running");
  w.ui = w.ui || {};
  const ui = w.ui[`${s.name}:zapret`] = w.ui[`${s.name}:zapret`] || {};
  const guess = (known && known.service) || "other";
  ui.service = ui.service || (on && on.service) || guess;
  const svc = z.services[ui.service] || { urls: [], test: "", note: "" };
  if (ui.urls === undefined || ui.forService !== ui.service) {
    ui.urls = (on && (on.service || "other") === ui.service && ui.service === "other" ? [on.url] : svc.urls).join("\n");
    ui.forService = ui.service;
  }
  const urls = () => ui.urls.split(/\s+/).filter(Boolean);

  const reload = async () => { try { w.zapret = await api("GET", `/api/routers/${w.id}/zapret`); } catch (e) { toast(e.message, true); } redraw(); };
  const follow = async () => {
    for (let i = 0; i < 1200; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      await reload();
      if (!w.zapret.run || w.zapret.run.state !== "running") return;
    }
  };
  const setRules = async (rules, msg) => {
    try { await api("PUT", `/api/routers/${w.id}/zapret`, { rules }); toast(msg); ui.since = Date.now(); } catch (e) { toast(e.message, true); }
    await reload();
  };
  const current = () => Object.fromEntries(Object.entries(st.rules).map(([k, v]) =>
    [k, { args: v.args, url: v.url, name: v.name || "", service: v.service || "" }]));

  const search = busyClick(async () => {
    if (!urls().length) { toast("Give at least one address that zapret should open", true); return; }
    try {
      if (st.installed === null) {
        toast("Installing zapret2 on the router…");
        await api("POST", `/api/routers/${w.id}/zapret/install`);
      }
      const test = ui.service !== "other" && urls()[0] === svc.urls[0] && svc.test ? svc.test : urls()[0];
      await api("POST", `/api/routers/${w.id}/zapret/search`, { apply: true, rules: [{ name: s.name, urls: urls(), test, service: ui.service }] });
    } catch (e) { toast(e.message, true); await reload(); return; }
    toast(`Trying zapret strategies for ${s.name} on the router…`);
    await reload();
    await follow();
    ui.since = Date.now();
    redraw();
    const out = w.zapret.run && w.zapret.run.outcome[s.name];
    if (out) toast(out.on ? `${s.name}: zapret first, with ${out.strategy}` : `${s.name}: ${out.why}`, !out.on);
  });
  if (running && !w.zapretFollow) { w.zapretFollow = follow().finally(() => { w.zapretFollow = null; }); }

  // sing-box tests the new group within a minute; until then it may sit on the servers
  const checking = on && st.using[s.name] !== "zapret" && ui.since && Date.now() - ui.since < 120e3;
  const state = on ? (st.using[s.name] === "zapret" ? h("span", { class: "pill ok" }, "Now: direct, through zapret")
    : checking || !st.using[s.name] ? h("span", { class: "pill" }, "Checking whether zapret opens its sites…")
      : h("span", { class: "pill warn" }, "Now: on its servers (zapret's check fails)")) : null;
  // while the page shows this rule: look again until zapret carries it (and now and then after)
  if (on && !running && !w.zapretTimer) {
    w.zapretTimer = setTimeout(() => {
      w.zapretTimer = null;
      if (/\/rules(\/[A-Za-z0-9_]+)?$/.test(location.hash) && document.querySelector("[aria-label='Zapret first']")) reload();
    }, checking ? 5000 : 30000);
  }
  const job = run && run.jobs[s.name];
  const results = job && (job.results || []).length ? h("details", {},
    h("summary", { class: "small" }, `Strategies tried: ${job.results.filter((r) => r.ok).length} of ${job.results.length} opened every address`),
    (job.baseline || []).length ? h("p", { class: "xs muted" }, "Without zapret: ",
      job.baseline.map((b) => `${b.url} ${b.ok ? "opens" : "does not open"}`).join(", ")) : null,
    h("div", { class: "scroll-x" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Strategy"), h("th", {}, "Result"), h("th", {}, ""))),
      h("tbody", {}, job.results.map((r) => h("tr", {},
        h("td", { class: "small", title: r.args.join(" ") }, r.name),
        h("td", { class: "small" }, r.ok ? h("span", { class: "pill ok" }, `opens, ${ms(r.urls.reduce((a, u) => a + (u.ms || 0), 0))}`)
          : h("span", { class: "muted" }, r.error || (r.urls.find((u) => !u.ok) || {}).detail || "does not open")),
        h("td", {}, r.ok && !(on && on.name === r.name) ? h("button", { type: "button", class: "btn xs", disabled: running,
          onclick: busyClick(() => setRules({ ...current(), [s.name]: { args: r.args, url: urls()[0], name: r.name, service: ui.service } },
            `${s.name}: zapret first, with ${r.name}`)) }, "Use this") : null))))))) : null;
  const outcome = run && run.outcome[s.name];

  const sel = h("select", { class: "input mid", "aria-label": "Service", disabled: running,
    onchange: (e) => { ui.service = e.target.value; redraw(); } },
  ZAPRET_SERVICES.map(([k, label]) => h("option", { value: k, selected: k === ui.service }, label)));
  put(box,
    h("label", { class: "check" },
      h("input", { type: "checkbox", role: "switch", checked: !!on || (running && !!run), disabled: refused || !known || running,
        onchange: async (e) => {
          if (e.target.checked) { await search(e); return; }
          e.target.disabled = true;
          const rest = current(); delete rest[s.name];
          await setRules(rest, `${s.name}: through its servers only`);
        } }),
      h("span", { class: "col" }, h("span", { style: "font-weight: 500" }, "Zapret first"),
        h("span", {}, refused ? "Not with a kill switch or Protected on: this rule's traffic must stay on its servers"
          : !known ? "Apply the rule first: zapret works on rules the router has"
            : on ? `Web traffic goes out without a server, through zapret2, while that opens its sites; otherwise through its servers. Strategy: ${on.name || on.args.join(" ")}`
              : "Web traffic goes out without a server, through zapret2, while that opens its sites; otherwise through its servers. Turning it on tries the strategy list on the router and keeps the fastest that opens the addresses below"))),
    h("div", { class: "row" }, state, running && run ? h("span", { class: "small" },
      job && job.trying ? `Trying ${job.trying.name}…` : "Searching…") : null),
    outcome && !outcome.on ? h("p", { class: "small muted" }, outcome.why) : null,
    h("div", { class: "form-grid" },
      h("label", { class: "lbl" }, "Service"), h("div", { class: "ctl" }, sel,
        svc.note ? h("span", { class: "xs muted" }, svc.note) : null),
      h("label", { class: "lbl" }, "Check addresses"), h("div", { class: "ctl" },
        h("textarea", { class: "input mono xs", rows: 2, spellcheck: "false", disabled: running, "aria-label": "Check addresses",
          placeholder: "https://example.com/", oninput: (e) => { ui.urls = e.target.value; } }, ui.urls),
        h("span", { class: "xs muted" }, `Sites that must open through zapret; the first one is checked every ${z.interval || 30} s (Settings)`))),
    known && !refused ? h("div", { class: "row" },
      h("button", { type: "button", class: "btn", disabled: running, onclick: search }, on ? "Find the best strategy again" : "Find a strategy and turn on"),
      st.installed ? h("span", { class: "xs muted" }, `zapret2 ${st.installed} on this router · sites see the router's own address · QUIC refused there, so apps use TCP`)
        : h("span", { class: "xs muted" }, "zapret2 is installed on the router first (about 0.4 MB)")) : null,
    results);
  return box;
}

// Settings: how often zapret rules are checked, and the strategy list every search tries (all routers)
export function strategiesCard() {
  const card = h("section", { class: "card pad", "aria-labelledby": "s-zap", style: "gap: 12px" },
    h("h2", { id: "s-zap" }, "Zapret"), h("span", { class: "small muted" }, "…"));
  api("GET", "/api/zapret/strategies").then((z) => {
    const every = h("select", { class: "input mid", "aria-label": "Zapret check interval", onchange: busyClick(async (e) => {
      try {
        const r = await api("PUT", "/api/zapret/strategies", { interval: +e.target.value });
        toast(`Checked every ${r.interval} s${r.routers.length ? `; sent to ${r.routers.join(", ")}` : ""}${r.missed.length ? `; not reached: ${r.missed.join("; ")}` : ""}`, r.missed.length > 0);
      } catch (err) { toast(err.message, true); }
    }) }, z.intervals.map((s) => h("option", { value: s, selected: s === z.interval }, s < 60 ? `${s} s` : `${s / 60} min`)));
    const ta = h("textarea", { class: "input mono xs", rows: 8, spellcheck: "false", "aria-label": "Zapret strategies" }, z.text);
    const sub = h("span", { class: "small muted" }, z.custom ? `Your list, ${z.count} strategies.` : `The default list, ${z.count} strategies: zapret2's docs and blockcheck2, and Flowseal's lists ported to zapret2.`);
    card.replaceChildren(h("h2", { id: "s-zap" }, "Zapret"),
      h("div", { class: "row" }, h("span", { style: "font-size: 13px; font-weight: 500" }, "Check a zapret rule every"), every),
      h("span", { class: "small muted" }, "The router opens each zapret rule's first check address through zapret at this interval (sing-box's own check uses the same one). When it fails, the rule's web traffic goes through its servers until it opens again; the move is reported after two rounds of the agent (about 30 s)."),
      h("span", { style: "font-size: 13px; font-weight: 500" }, "Strategies"), sub,
      h("span", { class: "small muted" }, "Tried in this order when a rule turns on “Zapret first”. One nfqws2 strategy per line: name | options. Fake files are named plainly, like tls_clienthello_www_google_com.bin."),
      ta,
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn primary", onclick: busyClick(async () => {
          try { const r = await api("PUT", "/api/zapret/strategies", { text: ta.value }); toast(`Saved: ${r.count} strategies`); }
          catch (e) { toast(e.message, true); }
        }) }, "Save"),
        h("button", { type: "button", class: "btn", onclick: busyClick(async () => {
          await api("PUT", "/api/zapret/strategies", { text: "" });
          const d = await api("GET", "/api/zapret/strategies"); ta.value = d.text; toast("Back to the default list");
        }) }, "Back to defaults")));
  }).catch(() => card.remove());
  return card;
}
