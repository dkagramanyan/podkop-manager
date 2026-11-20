// zapret.js: part of podkop-manager's web interface (see app.js)

import { pageHead } from "./shell.js";
import { api, h, put, toast } from "./ui.js";
import { busyClick } from "./usage.js";

// --- zapret on the router (agent 0.29): sites without a server first, the rule's servers as fallback ---

export async function viewZapret(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let z, timer = null;
  const picked = new Map();      // rule -> checked for the search
  const urls = new Map();        // rule -> test addresses typed on the page

  const load = async () => {
    try { z = await api("GET", `/api/routers/${id}/zapret`); }
    catch (e) {
      put(page, pageHead("Zapret"), h("div", { class: "card empty" }, /too old|unknown command/.test(e.message)
        ? "This router's agent has no zapret support yet. Update it first: Settings → Update agent (agent 0.29 or newer)."
        : e.message));
      return;
    }
    for (const r of z.rules) {
      if (!picked.has(r.name)) picked.set(r.name, r.default && !z.router.rules[r.name]);
      if (!urls.has(r.name)) urls.set(r.name, (z.router.rules[r.name] ? [z.router.rules[r.name].url] : r.urls).join("\n"));
    }
    draw();
    clearTimeout(timer);
    if (z.run && z.run.state === "running" && page.isConnected) timer = setTimeout(load, 3000);
  };

  const installed = () => z.router.installed !== null;

  const install = busyClick(async () => {
    toast("Downloading zapret2 and sending it to the router…");
    try { await api("POST", `/api/routers/${id}/zapret/install`); toast("zapret2 installed"); }
    catch (e) { toast(e.message, true); }
    await load();
  });

  const setRules = async (rules, msg) => {
    try { await api("PUT", `/api/routers/${id}/zapret`, { rules }); toast(msg); }
    catch (e) { toast(e.message, true); }
    await load();
  };
  const current = () => Object.fromEntries(Object.entries(z.router.rules).map(([k, v]) =>
    [k, { args: v.args, url: v.url, name: v.name || "" }]));

  const ruleUrls = (name) => urls.get(name).split(/\s+/).filter(Boolean);
  const search = (apply, names) => busyClick(async () => {
    const list = names.map((n) => {
      const r = z.rules.find((x) => x.name === n);
      return { name: n, urls: ruleUrls(n), test: r && r.test && ruleUrls(n)[0] === r.urls[0] ? r.test : ruleUrls(n)[0] };
    });
    if (!list.length) { toast("Tick at least one rule", true); return; }
    try { await api("POST", `/api/routers/${id}/zapret/search`, { rules: list, apply }); }
    catch (e) { toast(e.message, true); return; }
    toast(apply ? "Searching; rules where a strategy works get zapret on" : "Searching…");
    await load();
  });

  const card = (title, sub, ...body) => h("section", { class: "card sect", "aria-label": title },
    h("div", { class: "col" }, h("h2", {}, title), sub ? h("span", {}, sub) : null), body);

  const using = (name) => {
    const u = z.router.using[name];
    return u === "zapret" ? h("span", { class: "pill ok" }, "direct, through zapret")
      : u === "servers" ? h("span", { class: "pill warn" }, "on its servers (zapret's test fails)")
        : z.router.rules[name] ? h("span", { class: "pill" }, "waiting for sing-box") : h("span", { class: "pill" }, "servers only");
  };

  const ms = (x) => (x === null || x === undefined ? "" : x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${x} ms`);
  const jobView = (name) => {
    const j = z.run && z.run.jobs[name];
    if (!j) return null;
    const res = j.results || [];
    const pickNow = (r) => setRules({ ...current(), [name]: { args: r.args, url: ruleUrls(name)[0], name: r.name || "" } },
      `zapret on for ${name}: ${r.name}`);
    return h("details", { open: z.run.state === "running" },
      h("summary", { class: "small" }, `Search: ${res.filter((r) => r.ok).length} working of ${res.length} tried`,
        j.trying ? ` · trying ${j.trying.name}` : "", j.state === "failed" ? ` · ${j.message}` : ""),
      (j.baseline || []).length ? h("p", { class: "xs muted" }, "Without zapret: ",
        j.baseline.map((b) => `${b.url} ${b.ok ? "opens" : "fails"}`).join(", ")) : null,
      h("div", { class: "scroll-x" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Strategy"), h("th", {}, "Result"), h("th", {}, ""))),
        h("tbody", {}, res.map((r) => h("tr", {},
          h("td", { class: "small", title: r.args.join(" ") }, r.name),
          h("td", { class: "small" }, r.ok ? h("span", { class: "pill ok" }, `opens, ${ms(r.urls.reduce((a, u) => a + (u.ms || 0), 0))}`)
            : h("span", { class: "muted" }, r.error || (r.urls.find((u) => !u.ok) || {}).detail || "fails")),
          h("td", {}, r.ok ? h("button", { type: "button", class: "btn ghost", onclick: busyClick(() => pickNow(r)) }, "Use") : null)))))));
  };

  const draw = () => {
    const running = z.run && z.run.state === "running";
    const rows = z.rules.map((r) => {
      const on = z.router.rules[r.name];
      const out = z.run && z.run.outcome[r.name];
      const refused = (z.router.refused || []).includes(r.name);
      return h("div", { class: "card", style: "padding: 12px; display: grid; gap: 8px" },
        h("div", { class: "row" },
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: picked.get(r.name), disabled: refused || running,
            onchange: (e) => { picked.set(r.name, e.target.checked); } }),
          h("a", { class: "mono", href: `#/r/${id}/rules/${r.name}` }, r.name)),
          r.service ? h("span", { class: "pill" }, r.service) : null,
          using(r.name),
          on ? h("span", { class: "xs muted", title: on.args.join(" ") }, on.name || on.args.join(" ")) : null,
          on ? h("button", { type: "button", class: "btn ghost", disabled: running, onclick: busyClick(() => {
            const rest = current(); delete rest[r.name];
            return setRules(rest, `${r.name}: servers only`);
          }) }, "Servers only") : null),
        refused ? h("p", { class: "xs muted" }, "Kill switch or Protected is on for this rule: zapret is not used on it.") : null,
        r.note ? h("p", { class: "xs muted" }, r.note) : null,
        out ? h("p", { class: `small ${out.on ? "" : "muted"}` }, out.on ? `Turned on with ${out.strategy}` : out.why) : null,
        h("label", { class: "field" }, h("span", { class: "label xs" }, "Test addresses (the first one is also the live check)"),
          h("textarea", { class: "input mono xs", rows: 2, spellcheck: "false", disabled: running,
            oninput: (e) => { urls.set(r.name, e.target.value); } }, urls.get(r.name))),
        h("div", { class: "row" }, h("button", { type: "button", class: "btn ghost", disabled: running || refused || !installed(),
          onclick: search(false, [r.name]) }, "Find strategies")),
        jobView(r.name));
    });
    const strat = h("textarea", { class: "input mono xs", rows: 10, spellcheck: "false",
      "aria-label": "Strategies" }, z.strategies);
    put(page,
      pageHead("Zapret", "Sites of chosen rules go out without a server, through zapret; when that fails they use the rule's servers"),
      card("On the router", null,
        h("div", { class: "row" },
          installed() ? h("span", { class: "pill ok" }, `zapret2 ${z.router.installed || ""}`)
            : h("span", { class: "pill warn" }, "Not installed"),
          z.router.table ? h("span", { class: "pill" }, "firewall rules on") : null,
          h("button", { type: "button", class: installed() ? "btn ghost" : "btn primary", onclick: install },
            installed() ? "Update" : "Install zapret2")),
        h("p", { class: "xs muted" }, "nfqws2 from bol-van/zapret2's latest GitHub release, about 0.4 MB of flash. "
          + "Sites on a zapret rule see the router's own address. QUIC is refused there, so apps use TCP.")),
      card("Rules", "Ticked: rules for “Find and turn on”. Meta, X, YouTube and Telegram are ticked by default.",
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn primary", disabled: running || !installed(),
            onclick: search(true, z.rules.filter((r) => picked.get(r.name)).map((r) => r.name)) }, "Find and turn on"),
          running ? h("span", { class: "small" }, `Searching${z.run.now ? `: ${z.run.now}` : ""}…`) : null,
          z.run && z.run.state === "failed" ? h("span", { class: "small bad-text" }, z.run.error) : null,
          Object.keys(z.router.rules).length ? h("button", { type: "button", class: "btn ghost", disabled: running,
            onclick: busyClick(() => setRules({}, "zapret off on every rule")) }, "Turn zapret off everywhere") : null),
        h("p", { class: "xs muted" }, `Each rule: its test addresses without zapret first, then the ${z.count} strategies below in order, `
          + "until three open every address; the fastest is used. A rule where none works stays on its servers."),
        z.rules.length ? h("div", { class: "stack", style: "gap: 8px" }, rows) : h("p", { class: "small muted" }, "No proxy rules.")),
      card("Strategies", z.custom ? "Your list (all routers)" : "The default list (all routers): from zapret2's docs and blockcheck2, and Flowseal's lists ported to zapret2",
        h("p", { class: "xs muted" }, "One nfqws2 strategy per line: name | options. Fake files are named plainly, like tls_clienthello_www_google_com.bin."),
        strat,
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn", onclick: busyClick(async () => {
            try { await api("PUT", "/api/zapret/strategies", { text: strat.value }); toast("Strategies saved"); }
            catch (e) { toast(e.message, true); }
            await load();
          }) }, "Save"),
          z.custom ? h("button", { type: "button", class: "btn ghost", onclick: busyClick(async () => {
            await api("PUT", "/api/zapret/strategies", { text: "" }); toast("Back to the default list"); await load();
          }) }, "Back to defaults") : null)));
  };

  await load();
}
