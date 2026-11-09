// rules.js: part of podkop-manager's web interface (see app.js)

import { LINK_RE, binder, comboField, draftBar, flagField, keepFocus, lbl, segField, selectField, serversField,
  tokenField, touch, userListField, workChanges, workFor } from "./draft.js";
import { bySection, destChip, optionDef, ruleMatches, rulesOf, same, sectionsOf } from "./model.js";
import { pageHead, seg } from "./shell.js";
import { S } from "./state.js";
import { agentTime, api, asList, clone, fixFingerprint, h, icon, isLink, linkDetails, linkName, linkProto, modal,
  oldFingerprint, plural, put, shortWhen, spinner, toast } from "./ui.js";
import { EUROPE, busyClick, countriesText, countryName, hasEurope } from "./usage.js";

// --- routing rules -----------------------------------------------------------------------------------------

export const CONNECTIONS = [["proxy", "Proxy"], ["vpn", "VPN"], ["block", "Block"], ["exclusion", "Direct"]];
export const PROXY_TYPES = [["url", "One link"], ["selector", "Selector"], ["urltest", "Fastest (URLTest)"], ["outbound", "sing-box JSON"]];
export const PROXY_HELP = {
  url: "One server.",
  selector: "Uses the first server until you switch it in the Clash dashboard.",
  urltest: "Uses the fastest server.",
  outbound: "Checked with sing-box before applying.",
};

export async function viewRules(main, id, sub) {
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
            auto ? h("span", { class: "tag", style: "background: var(--line-soft); color: var(--text-2)" }, "from probe")
              : keyRule(w, s) ? h("span", { class: "tag", style: "background: var(--line-soft); color: var(--text-2)" }, `from ${keyLabels(w, s.name)}`) : null),
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
export const autoDest = (rule, sec) => `Proxy, fastest ${asList((sec.options || {}).urltest_proxy_links).length} from probe${
  rule.countries.length ? ", " + countriesText(rule.countries) : ""}`;

export function ruleEditor(w, s, { onRename }) {
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
    // the probe parameters and keys move first: the page that opens next reads them
    if (ruleKeys(w, old).length || ownLinks(w, old).length) {
      try {
        await api("PUT", `/api/routers/${w.id}/rule-keys/${name}`, { keys: ruleKeys(w, old), links: ownLinks(w, old) });
        await api("PUT", `/api/routers/${w.id}/rule-keys/${old}`, { keys: [], links: [] });
        w.keys.rules[name] = ruleKeys(w, old); w.keys.links[name] = ownLinks(w, old);
        delete w.keys.rules[old]; delete w.keys.links[old];
      } catch (e) { toast(e.message, true); }
    }
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
    if (ruleKeys(w, s.name).length || ownLinks(w, s.name).length) {
      try {
        const r = await api("PUT", `/api/routers/${w.id}/rule-keys/${name}`, { keys: ruleKeys(w, s.name), links: ownLinks(w, s.name) });
        w.keys.rules[name] = r.keys; w.keys.links[name] = r.links;
      } catch (e) { toast(e.message, true); }
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
          if (ruleKeys(w, s.name).length || ownLinks(w, s.name).length) api("PUT", `/api/routers/${w.id}/rule-keys/${s.name}`, { keys: [], links: [] })
            .then(() => { delete w.keys.rules[s.name]; delete w.keys.links[s.name]; }).catch(() => {});
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
    ct === "proxy" && pt === "urltest" ? ruleKeysField(w, s) : null,
    ct === "proxy" && pt === "urltest" ? h("label", { class: "check" },
      h("input", { type: "checkbox", role: "switch", checked: !!auto, onchange: (e) => toggleAuto(w, s, e.target.checked) }),
      h("span", { class: "col" }, h("span", { style: "font-weight: 500" }, "Take links from probe"),
        auto ? null : h("span", {}, "The probe tests your keys and picks servers by region, ping and speed"))) : null,
    ct === "proxy" && pt === "urltest" && auto ? autoRuleEditor(w, s, auto) : null,
    ct === "proxy" && (pt === "urltest" || (pt === "outbound" && viaRule(w, s))) ? strictField(w, s) : null,
    ct === "proxy" && pt === "url" ? oneLinkField(b) : null,
    ct === "proxy" && pt === "selector" ? serversField(w, b, "selector_proxy_links", { title: "Servers" }) : null,
    ct === "proxy" && pt === "urltest" && !auto ? (keyRule(w, s) ? keyServers(w, s) : serversField(w, b, "urltest_proxy_links", { title: "Servers" })) : null,
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

export function oneLinkField(b) {
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

export function proxyAdvanced(w, b, s, ct, pt) {
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

// --- URLTest rules that take the servers of chosen keys (subscriptions) ---------------------------------------------------
// Without the probe the rule gets every server of its keys, and your own
// servers besides, and the manager keeps them up to date when a provider
// changes its servers; with the probe, the probe picks only from those keys.

// a rule's keys, without keys removed since
export const ruleKeys = (w, name) => (w.keys.rules[name] || []).filter((id) => w.keys.subs.some((k) => k.id === id));
export const ownLinks = (w, name) => w.keys.links[name] || [];
export const keyLabels = (w, name) => w.keys.subs.filter((k) => ruleKeys(w, name).includes(k.id)).map((k) => k.label).join(", ");
export const keyRule = (w, s) => !w.auto[s.name] && ruleKeys(w, s.name).length > 0
  && ((s.options || {}).connection_type || "proxy") === "proxy" && (s.options || {}).proxy_config_type === "urltest";

// the keys' servers, then the rule's own, each once (a link without its name and fingerprint), as the manager counts them
export function keyLinks(w, name) {
  const seen = new Set(), out = [];
  const all = [...w.keys.subs.filter((k) => ruleKeys(w, name).includes(k.id)).flatMap((k) => k.links), ...ownLinks(w, name)];
  for (const l of all) {
    const n = l.split("#")[0].replace(/([?&])fp=[^&#]*&?/, "$1").replace(/[?&]$/, "");
    if (!seen.has(n)) { seen.add(n); out.push(l); }
  }
  return out;
}

// the draft gets the keys' servers (a key whose servers were not read yet has none: nothing changes)
export function fillKeyServers(w, s) {
  if (!keyRule(w, s)) return;
  const links = keyLinks(w, s.name), o = s.options;
  if (!links.length || same(asList(o.urltest_proxy_links), links)) return;
  o.urltest_proxy_links = links;
  touch(w);
}

export function ruleKeysField(w, s) {
  if (!w.keys.subs.length) return null;
  const chosen = ruleKeys(w, s.name), auto = w.auto[s.name];
  const set = async (ids) => {
    try {
      const r = await api("PUT", `/api/routers/${w.id}/rule-keys/${s.name}`, { keys: ids });
      w.keys.rules[s.name] = r.keys; w.keys.links[s.name] = r.links;
      if (auto) { const ui = w.ui[`${s.name}:auto`]; if (ui) ui.preview = null; } else fillKeyServers(w, s);
    } catch (e) { toast(e.message, true); }
    w.redraw();
  };
  return h("div", { class: "field", role: "group", "aria-label": "Servers from keys" },
    h("span", { class: "label xs muted", style: "font-size: 12px" }, "Servers from keys"),
    h("div", { class: "row", style: "gap: 6px" }, w.keys.subs.map((k) =>
      h("button", { type: "button", class: "toggle", "aria-pressed": String(chosen.includes(k.id)), title: plural(k.links.length, "server"),
        onclick: () => set(chosen.includes(k.id) ? chosen.filter((x) => x !== k.id) : [...chosen, k.id]) }, k.label))),
    h("span", { class: "help" }, !chosen.length ? (auto ? "None chosen: the probe picks from all keys" : "None chosen: the servers you add below")
      : auto ? "The probe picks only from these keys"
        : "Every server of these keys. When a provider changes its servers, the manager applies the new ones by itself"));
}

// the servers a rule without the probe has from its keys, as in the draft: the keys' come
// from the provider, your own ones you add and remove here
export function keyServers(w, s) {
  const links = asList(s.options.urltest_proxy_links), own = ownLinks(w, s.name);
  const ui = w.ui[`${s.name}:keys`] || (w.ui[`${s.name}:keys`] = { all: false });
  const shown = ui.all ? links : links.slice(0, 6);
  const fromKeys = links.filter((l) => !own.includes(l)).length;
  const setOwn = async (next) => {
    try {
      const r = await api("PUT", `/api/routers/${w.id}/rule-keys/${s.name}`, { keys: ruleKeys(w, s.name), links: next });
      w.keys.links[s.name] = r.links;
      fillKeyServers(w, s);
    } catch (e) { toast(e.message, true); }
    w.redraw();
  };
  const addArea = h("textarea", { class: "input", rows: 2, id: `own-${s.name}`, "data-k": `own-${s.name}`,
    placeholder: "Paste links, one per line: vless://, trojan://, ss://, hysteria2://, socks://" });
  const add = () => {
    const found = (addArea.value.match(LINK_RE) || []).filter((l) => !own.includes(l));
    if (!found.length) return toast("No links found: they start with vless://, trojan://, ss://, hysteria2:// or socks://", true);
    addArea.value = "";
    setOwn([...own, ...found]);
  };
  return h("div", { class: "servers" },
    h("div", { class: "servers-head" }, h("span", {}, `Servers, ${fromKeys} from ${keyLabels(w, s.name)}${own.length ? ` and ${own.length} your own` : ""}`)),
    links.length ? null : h("p", { class: "empty" }, "The manager has not read these keys' servers yet. They come within 10 minutes."),
    shown.map((l) => h("div", { class: "srv" },
      h("div", { class: "col" },
        h("div", {}, h("span", { class: "nm" }, linkName(l)), h("span", { class: "proto" }, linkProto(l)),
          own.includes(l) ? h("span", { class: "tag" }, "yours") : null),
        h("span", { class: "host ellipsis", title: linkDetails(l) }, linkDetails(l))),
      own.includes(l) ? h("button", { type: "button", class: "btn icon", "aria-label": `Remove ${linkName(l)}`,
        onclick: () => setOwn(own.filter((x) => x !== l)) }, icon("trash", 14, { width: 1.6 })) : null)),
    links.length > 6 ? h("div", { class: "servers-more" }, h("button", { type: "button", class: "link-btn", onclick: () => { ui.all = !ui.all; w.redraw(); } },
      ui.all ? "Show fewer" : `Show ${links.length - 6} more`)) : null,
    h("div", { class: "servers-add" },
      h("div", { class: "row", style: "justify-content: space-between" },
        h("label", { for: `own-${s.name}`, class: "xs", style: "font-weight: 500; color: var(--text-2)" }, "Add your own servers"),
        h("button", { type: "button", class: "btn xs", onclick: add }, "Add")),
      addArea, h("span", { class: "help" }, "Kept when the provider changes its servers")));
}

// --- rules whose servers the probe picks from the subscriptions ----------------------------------------------------------

// the Fastest rule an outbound rule sends through: its JSON is a selector whose first member is "<rule>-out"
export function viaRule(w, s) {
  let o;
  try { o = JSON.parse((s.options || {}).outbound_json || ""); } catch { return null; }
  const first = o && o.type === "selector" && Array.isArray(o.outbounds) ? String(o.outbounds[0] || "") : "";
  const name = first.endsWith("-out") ? first.slice(0, -4) : "";
  const via = rulesOf(w.config).find((r) => r.name === name);
  return via && (via.options || {}).proxy_config_type === "urltest" ? via : null;
}

// Kill switch: the rule's traffic goes through its servers or nowhere (saved on
// the router at once, like "Take links from probe"; agent 0.12, through
// another rule: agent 0.16, with self-heal off: agent 0.17)
function strictField(w, s) {
  const heal = w.heal;
  if (!heal || !heal.supported || !Array.isArray(heal.strict)) return null;
  const via = (s.options || {}).proxy_config_type === "outbound" ? viaRule(w, s) : null;
  const watched = via ? via.name : s.name;
  const off = heal.needs_heal && (heal.off.includes("*") || heal.off.includes(watched));
  const on = heal.strict.includes(s.name);
  return h("label", { class: "check", title: off ? `This agent watches ${watched}'s servers only with self-heal on` : null },
    h("input", { type: "checkbox", role: "switch", checked: on, disabled: off, onchange: async (e) => {
      const sections = heal.strict.filter((x) => x !== s.name).concat(e.target.checked ? [s.name] : []);
      e.target.disabled = true;
      try { Object.assign(heal, await api("PUT", `/api/routers/${w.id}/strict`, { sections })); toast(e.target.checked ? `Kill switch on for ${s.name}` : `Kill switch off for ${s.name}`); }
      catch (err) { toast(err.message, true); }
      finally { w.redraw(); }
    } }),
    h("span", { class: "col" }, h("span", { style: "font-weight: 500" }, "Kill switch"),
      h("span", {}, off ? `Needs self-heal on for ${watched}, or agent 0.17 (Connection health)`
        : via ? `Through ${via.name}'s servers or nowhere: connections refused at once while none of them answers (even if ${via.name} borrowed servers), its sites blocked on the router while podkop is down`
          : "Through its own servers or nowhere: no borrowed servers, connections refused at once while none of its servers answers, its sites blocked on the router while podkop is down (works with self-heal off too)")));
}

export async function toggleAuto(w, s, on) {
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
      fillKeyServers(w, s);
    }
  } catch (e) { toast(e.message, true); }
  w.redraw();
}

// a rule's filter and limits for the probe's servers, what it matches now, and its servers in the draft
// The router's nightly probe: one run for every rule that takes links from
// it, so each such rule shows the same settings. Saved right away, not in the
// draft. key: a suffix for the element ids.
export function nightlyProbeBlock(w, key, redraw) {
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

export function autoRuleEditor(w, s, rule) {
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
          h("a", { href: `#/r/${w.id}/usage`, class: "xs" }, p ? `Tests ${plural(p.keys, "key")} from Keys and usage` : "Keys and usage")),
        h("label", {}, "Selection priorities ", h("select", { onchange: (e) => update({ profile: e.target.value }, true) },
          [ ["balanced", "Browsing: reliability and latency"], ["interactive", "Interactive: reliability and response consistency"],
            ["streaming", "Streaming: reliability and download speed"] ].map(([value, label]) =>
            h("option", { value, selected: (rule.profile || "balanced") === value }, label)))),
        h("div", { class: "form-grid", style: "grid-template-columns: minmax(110px, 140px) minmax(0, 1fr); align-items: start" },
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
          h("label", { for: "heal-fb", class: "lbl", style: "font-weight: 500; padding-top: 8px" }, "If none works"),
          h("div", { class: "ctl" }, h("select", { id: "heal-fb", class: "input mid", style: "max-width: 360px",
            onchange: (e) => update({ heal_fallback: e.target.value }, true) },
            [["", "Wait for one of these countries (self-heal keeps looking)"], ["europe", "Self-heal may take a server in Europe"], ["any", "Self-heal may take a server in any country"]]
              .map(([value, label]) => h("option", { value, selected: (rule.heal_fallback || "") === value }, label))),
          h("span", { class: "help" }, "Only while self-heal finds nothing in the chosen countries; the nightly probe keeps to them")),
          h("label", { for: "namef", class: "lbl", style: "font-weight: 500; padding-top: 8px" }, "Name contains"),
          h("div", { class: "ctl" }, h("input", { id: "namef", class: "input mid", style: "max-width: 360px", "data-k": "auto-name", placeholder: "Any, e.g. Warsaw",
            value: rule.name_contains, oninput: (e) => update({ name_contains: e.target.value }) })),
          h("span", { class: "lbl", style: "font-weight: 500; padding-top: 8px" }, "Keep"),
          h("div", { class: "row small", style: "gap: 8px 10px" },
            h("label", { class: "sr", for: "keep" }, "Servers to keep by reliability, key diversity and latency"),
            h("input", { id: "keep", class: "input mid", style: "width: 56px", inputmode: "numeric", "data-k": "auto-top", value: rule.top,
              oninput: (e) => { const n = parseInt(e.target.value, 10); if (n >= 1) update({ top: n }); } }),
            h("span", {}, "selected across keys and"),
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
