// global.js: part of podkop-manager's web interface (see app.js)

import { binder, comboField, discardWork, flagField, keepFocus, lbl, reviewWork, segField, selectField, textField, tokenField, workChanges, workFor } from "./draft.js";
import { bySection, optionDef, rulesOf, sectionsOf } from "./model.js";
import { pageHead } from "./shell.js";
import { S } from "./state.js";
import { asList, h, put } from "./ui.js";

// --- global settings ----------------------------------------------------------------------------------------

export async function viewGlobal(main, id) {
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

export function settingsOther(o, defs) {
  const known = new Set([...defs.map((d) => d.name), ...S.schema.internal]);
  const other = Object.keys(o).filter((k) => !known.has(k));
  return h("p", { class: "small muted" }, "Other options are kept as they are",
    other.length ? `: ${other.join(", ")}.` : ".");
}
