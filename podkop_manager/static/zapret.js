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
  const st = (((rt || {}).status || {}).zapret || {})[s.name];      // agent 0.30
  if (st && ZAPRET_STATE[st]) {
    const [cls, label, title] = ZAPRET_STATE[st];
    return h("span", { class: `tag ${cls}`, title }, label);
  }
  const g = (((rt || {}).proxies || {}).groups || []).find((x) => x.name === `${s.name}-zapret-out`);
  if (!g) return null;
  return g.now === `${s.name}-zapret-direct` ? h("span", { class: "tag ok", title: "Its web traffic goes out direct, through zapret2" }, "zapret")
    : h("span", { class: "tag warn", title: "zapret does not open its sites now: the rule uses its servers" }, "zapret → servers");
}

// a zapret rule's state on the router (agent 0.30): [class, short label, what it means]
export const ZAPRET_STATE = {
  zapret: ["ok", "zapret", "Its web traffic goes out direct, through zapret2"],
  servers: ["warn", "zapret → servers", "zapret does not open its sites now: the rule uses its servers"],
  missing: ["bad", "zapret missing", "zapret is on, but sing-box runs without it: the router puts it back within a minute"],
  stopped: ["bad", "zapret stopped", "zapret2 (nfqws2) does not run: the router starts it again"],
  no_table: ["bad", "zapret firewall missing", "zapret's firewall table is missing: the router adds it again"],
  refused: ["", "zapret paused", "zapret waits while the rule has a kill switch or Protected on"],
  no_rule: ["", "zapret waits", "the rule is not running on the router yet"],
  not_installed: ["bad", "zapret not installed", "zapret2 is gone from the router: turn zapret first on again"],
};

const ms = (x) => (x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${x} ms`);

// the rule page's parts: the switch (at the top of the page), a one-line summary and
// the settings (in a panel that can be closed); w.zapret holds what GET .../zapret
// said (loaded once per page)
export function zapretParts(w, s, redraw) {
  const rt = S.routers[w.id];
  if (!zapretSupported(rt) || (s.options || {}).connection_type !== "proxy") return null;
  const z = w.zapret;
  const only = (text, cls) => ({ toggle: h("div", { class: "check", "aria-label": "Zapret first" },
    h("input", { type: "checkbox", role: "switch", disabled: true }),
    h("span", { class: "col" }, h("span", { style: "font-weight: 500" }, "Zapret first"), h("span", { class: cls }, text))), summary: text, body: null });
  if (!z) {
    if (!w.zapretLoading) {
      w.zapretLoading = api("GET", `/api/routers/${w.id}/zapret`)
        .then((r) => { w.zapret = r; }).catch((e) => { w.zapret = { error: e.message }; })
        .finally(() => { w.zapretLoading = null; redraw(); });
    }
    return only("…", "");
  }
  if (z.error) return only(z.error, "bad-text");
  const st = z.router;
  const on = st.rules[s.name];
  const known = z.rules.find((r) => r.name === s.name);       // the rule is on the router (not only in the draft)
  // the router's lists as the last status said (a kill switch turned off a moment ago counts)
  const hl = w.heal || {};
  const liveSkip = Array.isArray(hl.strict) || Array.isArray(hl.protected) ? [...(hl.strict || []), ...(hl.protected || [])] : st.refused || [];
  const refused = liveSkip.includes(s.name);
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

  // this page still shows this router's rules (a timer of a page left behind stops)
  const shown = () => S.work[w.id] === w && location.hash.startsWith(`#/r/${w.id}/rules`);
  // the page is drawn again only when something changed, and not while one types
  const reload = async () => {
    const before = JSON.stringify(w.zapret);
    try { w.zapret = await api("GET", `/api/routers/${w.id}/zapret`); } catch (e) { toast(e.message, true); }
    if (JSON.stringify(w.zapret) === before) return;
    const a = document.activeElement;
    if (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.type !== "checkbox") return;
    redraw();
  };
  const follow = async () => {
    for (let i = 0; i < 1200; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      if (!shown()) return;
      await reload();
      if (!w.zapret.run || w.zapret.run.state !== "running") return;
    }
  };
  // one rule on (cfg) or off (null); the router's other zapret rules stay as they are
  const setRule = async (cfg, msg) => {
    try {
      await api("PUT", `/api/routers/${w.id}/zapret`, cfg ? { set: { [s.name]: cfg } } : { off: [s.name] });
      toast(msg); ui.since = Date.now();
    } catch (e) { toast(e.message, true); }
    await reload();
  };

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
    else if (w.zapret.run && w.zapret.run.state === "failed") toast(`The search failed: ${w.zapret.run.error}`, true);
  });
  if (running && !w.zapretFollow) { w.zapretFollow = follow().finally(() => { w.zapretFollow = null; }); }

  // sing-box tests the new group within a minute; until then it may sit on the servers
  const using = (st.state || {})[s.name] || st.using[s.name];
  const checking = on && using !== "zapret" && ui.since && Date.now() - ui.since < 120e3;
  const now = !on ? null : using === "zapret" ? ["ok", "Now: direct, through zapret"]
    : checking ? ["", "Checking whether zapret opens its sites…"]
      : using === "servers" ? ["warn", "Now: on its servers (zapret's check fails)"]
        : ZAPRET_STATE[using] ? [ZAPRET_STATE[using][0] || "warn", `Now: ${ZAPRET_STATE[using][2]}`]
          : ["warn", "Now: unknown (sing-box's API does not answer)"];
  const state = now ? h("span", { class: `pill ${now[0]}` }, now[1]) : null;
  // while the page shows this rule: look again until zapret carries it (and now and then after)
  if (on && !running && !w.zapretTimer) {
    w.zapretTimer = setTimeout(() => {
      w.zapretTimer = null;
      if (shown() && document.querySelector("[aria-label='Zapret first']")) reload();
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
          onclick: busyClick(() => setRule({ args: r.args, url: urls()[0], name: r.name, service: ui.service },
            `${s.name}: zapret first, with ${r.name}`)) }, "Use this") : null))))))) : null;
  const outcome = run && run.outcome[s.name];

  const sel = h("select", { class: "input mid", "aria-label": "Service", disabled: running,
    onchange: (e) => { ui.service = e.target.value; redraw(); } },
  ZAPRET_SERVICES.map(([k, label]) => h("option", { value: k, selected: k === ui.service }, label)));
  const searching = running && run ? (job && job.trying ? `Searching: trying ${job.trying.name}…` : "Searching…") : null;
  const toggle = h("label", { class: "check", "aria-label": "Zapret first" },
    h("input", { type: "checkbox", role: "switch", checked: !!on || (running && !!run), disabled: (!on && (refused || !known)) || running,
      onchange: async (e) => {
        if (e.target.checked) { await search(e); return; }
        e.target.disabled = true;
        await setRule(null, `${s.name}: through its servers only`);
      } }),
    h("span", { class: "col" }, h("span", { style: "font-weight: 500" }, "Zapret first"),
      h("span", {}, searching || (refused ? (on ? "Paused while the kill switch or Protected is on" : "Not with a kill switch or Protected on")
        : !known ? "Apply the rule first: zapret works on rules the router has"
          : now ? now[1] : outcome && !outcome.on ? outcome.why : "Web traffic direct through zapret2; its servers when that fails"))));
  const service = on && (ZAPRET_SERVICES.find(([k]) => k === (on.service || "other")) || ["other", "Other"])[1];
  const summary = searching || (refused ? "not with a kill switch or Protected" : !known ? "apply the rule first"
    : on ? `on · ${service} · ${on.name || "own strategy"}` : "off");
  const box = h("div", { class: "stack", style: "gap: 10px", "aria-label": "Zapret settings" });
  put(box,
    h("p", { class: "small muted" }, on ? `Web traffic goes out without a server, through zapret2, while that opens its sites; otherwise through its servers. Strategy: ${on.name || on.args.join(" ")}`
      : "Web traffic goes out without a server, through zapret2, while that opens its sites; otherwise through its servers. Turning it on tries the strategy list on the router and keeps the fastest that opens the addresses below"),
    state || searching ? h("div", { class: "row" }, state, searching ? h("span", { class: "small" }, searching) : null) : null,
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
  return { toggle, summary, body: box };
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
