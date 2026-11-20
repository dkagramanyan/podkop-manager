// devices.js: part of podkop-manager's web interface (see app.js)

import { reviewWork, touch, workChanges, workFor } from "./draft.js";
import { rulesOf, sectionsOf } from "./model.js";
import { pageHead } from "./shell.js";
import { api, asList, h, modal, plural, put, toast } from "./ui.js";

// --- a device's whole traffic through one rule, or never through podkop ---------------------
// These are podkop's own settings: "Fully routed IPs" of a rule and "IPs never
// routed" of the global settings. The page shows them by device and changes
// the draft, so a change is reviewed and applied like any other.

const bare = (x) => String(x).replace(/\/32$/, "");
const isAddr = (x) => /^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/.test(x);

export async function viewDevices(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let w, found = [], why = "";
  try { w = await workFor(id); } catch (e) { put(page, pageHead("Devices"), h("div", { class: "card empty" }, e.message)); return; }
  try { found = (await api("GET", `/api/routers/${id}/devices`)).devices; } catch (e) { why = e.message; }

  const settings = () => sectionsOf(w.config).find((s) => s.name === "settings");
  const targets = () => rulesOf(w.config).filter((s) => ["proxy", "vpn"].includes((s.options || {}).connection_type || "proxy"));
  // "" by the rules, "-" never through podkop, or the rule's name
  const choice = (ip) => {
    const st = settings();
    if (st && asList(st.options.routing_excluded_ips).map(bare).includes(ip)) return "-";
    const r = targets().find((s) => asList(s.options.fully_routed_ips).map(bare).includes(ip));
    return r ? r.name : "";
  };
  const without = (o, key, ip) => {
    const left = asList(o[key]).filter((x) => bare(x) !== ip);
    if (left.length) o[key] = left; else delete o[key];
  };
  const choose = (ip, v) => {
    const st = settings();
    if (st) without(st.options, "routing_excluded_ips", ip);
    for (const s of rulesOf(w.config)) without(s.options, "fully_routed_ips", ip);
    if (v === "-" && st) st.options.routing_excluded_ips = asList(st.options.routing_excluded_ips).concat([ip]);
    else if (v) {
      const s = rulesOf(w.config).find((x) => x.name === v);
      s.options.fully_routed_ips = asList(s.options.fully_routed_ips).concat([ip]);
    }
    touch(w); draw();
  };

  const headBtn = h("span", { style: "display: contents" });
  w.drawBar = () => {
    const n = workChanges(w).length;
    put(headBtn, n ? h("button", { type: "button", class: "btn primary", disabled: w.base !== w.fp, onclick: () => reviewWork(w) }, `Review and apply, ${n}`) : null);
  };
  w.redraw = () => draw();

  const draw = () => {
    // the devices the router sees, then the addresses that are set and not seen now
    const rows = found.map((d) => ({ ...d }));
    const st = settings();
    const set = [...(st ? asList(st.options.routing_excluded_ips) : []), ...targets().flatMap((s) => asList(s.options.fully_routed_ips))].map(bare);
    for (const ip of set) if (!rows.some((d) => d.ip === ip)) rows.push({ ip, mac: "", name: "", fixed: null, away: true });
    rows.sort((a, b) => (choice(b.ip) !== "") - (choice(a.ip) !== "") || a.ip.localeCompare(b.ip, undefined, { numeric: true }));
    const add = h("input", { class: "input", style: "width: 220px", placeholder: "192.168.1.50 or 192.168.2.0/24", "aria-label": "Address to add" });
    const addIt = () => {
      const v = add.value.trim();
      if (!isAddr(v)) { toast("An IPv4 address or a subnet like 192.168.2.0/24", true); return; }
      if (!found.some((d) => d.ip === bare(v))) found.push({ ip: bare(v), mac: "", name: "", fixed: null, away: true });
      add.value = ""; draw();
    };
    add.onkeydown = (e) => { if (e.key === "Enter") addIt(); };
    const n = rows.filter((d) => choice(d.ip) !== "").length;
    put(page,
      pageHead("Devices", `${plural(rows.length, "device")}${n ? `, ${n} with a route of ${n === 1 ? "its" : "their"} own` : ""}`, headBtn),
      why ? h("div", { class: "banner" }, h("span", { class: "dot accent" }), h("span", { class: "grow small" }, `The router's device list could not be read (${why}). Addresses can still be set by hand.`)) : null,
      h("section", { class: "card clip", "aria-labelledby": "d-list" },
        h("div", { class: "card-head" }, h("div", { class: "col" }, h("h2", { id: "d-list" }, "Where each device's traffic goes"),
          h("span", { class: "small muted" }, "By the rules: only what the rules list goes through their servers. Through one rule: everything the device sends. Never through podkop: everything direct, the rules do not apply."))),
        rows.length ? h("div", { class: "scroll-x" }, h("table", { class: "wide" },
          h("tr", {}, h("th", { scope: "col" }, "Device"), h("th", { scope: "col" }, "Address"), h("th", { scope: "col" }, "Traffic")),
          rows.map((d) => {
            const v = choice(d.ip);
            return h("tr", {},
              h("td", {}, h("div", { class: "col" }, h("span", { style: "font-weight: 500" }, d.name || (d.away ? "not seen now" : "no name")),
                d.mac ? h("span", { class: "small muted mono-cell" }, d.mac) : null)),
              h("td", {}, h("div", { class: "col" }, h("span", { class: "mono-cell" }, d.ip),
                v && d.fixed === false ? h("span", { class: "small warn-text" }, "address from DHCP, it may change: give the device a static lease on the router") : null)),
              h("td", {}, h("select", { class: "input", style: "width: auto", "aria-label": `Traffic of ${d.name || d.ip}`, onchange: (e) => choose(d.ip, e.target.value) },
                h("option", { value: "", selected: v === "" }, "By the rules"),
                targets().map((s) => h("option", { value: s.name, selected: v === s.name }, `Everything through ${s.name}`)),
                h("option", { value: "-", selected: v === "-" }, "Never through podkop"))));
          }))) : h("p", { class: "empty" }, "The router sees no device now."),
        h("div", { class: "card-foot" }, h("span", { class: "small muted" }, "A device that is not in the list, or a whole subnet"),
          h("div", { class: "row" }, add, h("button", { type: "button", class: "btn", onclick: addIt }, "Add")))));
    w.drawBar();
  };
  draw();
}

// --- where does a name or an address go -------------------------------------------------------

const place = (e) => (e && !e.error && e.ip ? `${e.country ? e.country + " · " : ""}${e.ip}` : "");

export function routeAnswer(r) {
  const lines = [];
  const m = r.match, rule = r.rule;
  if (m && rule && rule.type === "excluded") {
    lines.push(["", "This device is in \u201cIPs never routed\u201d: everything it sends goes direct, the rules do not apply."]);
  } else if (m && rule) {
    const by = rule.by === "domains" ? "its own domains" : rule.by === "subnets" ? "its own addresses"
      : rule.by === "community" ? `the community list ${rule.list}` : m.every ? "everything this device sends" : rule.list ? `the list ${rule.list}` : "an entry of the rule";
    lines.push([rule.type === "block" ? "bad" : "ok", `Rule ${rule.section} takes it (${by}).`]);
    if (rule.type === "vpn") lines.push(["", "It goes through the VPN interface of that rule."]);
    else if (rule.type === "block") lines.push(["bad", "That rule blocks it."]);
    else if (rule.type === "exclusion") lines.push(["", "That rule sends it direct, past the other rules."]);
    else if (rule.zapret) lines.push(["", "Server now: none. It goes out direct through zapret, so the site sees the router's own address."]);
    else if (rule.server) lines.push(["", `Server now: ${rule.server}${place(rule.exit) ? `, comes out at ${place(rule.exit)}` : ""}.`]);
    if (rule.zapret === false) lines.push(["", "zapret is on for this rule, but its check fails now, so the rule uses its servers."]);
    if (rule.kill_switch) lines.push(["", "The rule has a kill switch: with none of its servers answering, the connection is refused."]);
  } else if (r.kind === "domain" && r.dns && r.dns.fakeip && r.unread.length) {
    lines.push(["warn", `podkop routes this name (the router's DNS answers with a FakeIP address), by one of the lists that could not be read just now: ${r.unread.map((x) => x.replace(/-community-ruleset$/, "")).join(", ")}. Ask again in a minute.`]);
  } else {
    lines.push(["", "No rule takes it: it goes direct, through the provider."]);
    if (r.unread.length && !(r.dns && r.dns.address && !r.dns.fakeip)) lines.push(["warn", `${plural(r.unread.length, "list")} could not be read just now (${r.unread.map((x) => x.replace(/-community-ruleset$/, "")).join(", ")}), so this is not certain. Ask again in a minute.`]);
  }
  if (r.kind === "domain" && r.dns) {
    if (!r.dns.address) lines.push(["warn", "The router's DNS gives no address for this name."]);
    else if (m && !r.dns.fakeip) lines.push(["warn", `The router's DNS answers ${r.dns.address}, a real address: podkop's DNS is not in the way for this name, so a device reaches it direct. Look at Connection health.`]);
    else if (!m && r.dns.fakeip) lines.push(["warn", "The router's DNS answers with a FakeIP address all the same: sing-box gets the connection and sends it direct."]);
  }
  if (r.kind === "ip" && m && !m.every && r.nft_set === "") lines.push(["warn", "The address is in the rule's list, but not in podkop's firewall sets: the router does not hand such a connection to sing-box. The lists may still be loading."]);
  if (r.seen && (!rule || r.seen.section !== rule.section)) lines.push(["", `Last seen going through ${r.seen.section}.`]);
  return lines;
}

export function openRoute(id, rules) {
  const input = h("input", { class: "input", placeholder: "chatgpt.com or 31.13.24.5", "aria-label": "Name or address", autofocus: true });
  const from = h("input", { class: "input", style: "width: 170px", placeholder: "from device, optional", "aria-label": "Device address, optional",
    title: "A device's address, when devices have routes of their own (Devices page)" });
  const out = h("div", { class: "col", style: "gap: 6px; min-height: 60px", "aria-live": "polite" });
  const go = h("button", { type: "button", class: "btn primary" }, "Ask the router");
  const ask = async () => {
    if (!input.value.trim()) return;
    go.disabled = true;
    put(out, h("span", { class: "small muted" }, "Asking the router… the first question after a while downloads the community lists and can take a minute."));
    try {
      const r = await api("POST", `/api/routers/${id}/route`, { target: input.value, from: from.value });
      put(out, h("span", { style: "font-weight: 600" }, r.target),
        routeAnswer(r).map(([cls, text]) => h("span", { class: cls ? `${cls}-text` : null }, text)),
        r.rule && r.rule.known && rules ? h("a", { class: "alink", href: `#/r/${id}/rules/${r.rule.section}`, onclick: () => m.close() }, `Open rule ${r.rule.section}`) : null);
    } catch (e) { put(out, h("span", { class: "bad-text" }, e.message)); }
    go.disabled = false;
  };
  go.onclick = ask;
  input.onkeydown = from.onkeydown = (e) => { if (e.key === "Enter") ask(); };
  const m = modal({ title: "Where does it go?", sub: "The rules sing-box runs on the router now, tried in their order. Not the draft.",
    narrow: true, body: [h("div", { class: "row", style: "gap: 8px; flex-wrap: wrap" }, h("div", { style: "flex: 1 1 240px" }, input), from, go), out] });
  setTimeout(() => input.focus(), 0);
}
