// vpn.js: part of podkop-manager's web interface (see app.js)

import { touch, workFor } from "./draft.js";
import { rulesOf } from "./model.js";
import { pageHead } from "./shell.js";
import { api, h, put, toast } from "./ui.js";
import { busyClick } from "./usage.js";

// --- OpenVPN client on the router (agent 0.21) ----------------------------------------------

export const VPN_DEV = "pkvpn";

export async function viewVpn(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let st, w;
  const form = { file: null, fileName: "", username: null, password: "" };

  const load = async () => {
    try { [st, w] = await Promise.all([api("GET", `/api/routers/${id}/vpn`), workFor(id)]); }
    catch (e) {
      put(page, pageHead("VPN"), h("div", { class: "card empty" }, /unknown command/.test(e.message)
        ? "This router's agent has no VPN support yet. Update it first: Settings → Update agent (agent 0.21 or newer)."
        : e.message));
      return;
    }
    draw();
  };

  const save = async (body, msg) => {
    try { st = await api("PUT", `/api/routers/${id}/vpn`, body); toast(msg); draw(); return true; }
    catch (e) { toast(e.message, true); return false; }
  };

  const install = busyClick(async () => {
    let job;
    try { job = (await api("POST", `/api/routers/${id}/vpn/install`)).job; } catch (e) { toast(e.message, true); return; }
    toast("Installing OpenVPN on the router…");
    for (let i = 0; i < 150; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      let j;
      try { j = await api("GET", `/api/routers/${id}/vpn/job/${job}`); } catch { continue; }
      if (j.state === "done") { toast("OpenVPN installed"); return load(); }
      if (j.state === "failed") { toast(`Install failed: ${j.message}`, true); return load(); }
    }
    toast("The install is still running on the router; reload the page later", true);
  });

  const card = (title, sub, ...body) => h("section", { class: "card sect", "aria-label": title },
    h("div", { class: "col" }, h("h2", {}, title), sub ? h("span", {}, sub) : null), body);

  const statePill = () => !st.installed ? h("span", { class: "pill warn" }, "Not installed")
    : !st.configured ? h("span", { class: "pill warn" }, "No config")
      : !st.enabled ? h("span", { class: "pill" }, "Off")
        : st.up ? h("span", { class: "pill ok" }, `Connected, ${st.address}`)
          : h("span", { class: "pill bad" }, st.running ? "Connecting…" : "Not running");

  const vpnRules = () => rulesOf(w.config).filter((s) => (s.options || {}).connection_type === "vpn" && (s.options || {}).interface === VPN_DEV);

  const addRule = () => {
    let k = 1;
    while (rulesOf(w.config).some((s) => s.name === `vpn_${k}`)) k++;
    w.config.sections.push({ name: `vpn_${k}`, type: "section", anonymous: false,
      options: { connection_type: "vpn", interface: VPN_DEV, user_domain_list_type: "dynamic" } });
    touch(w);
    location.hash = `#/r/${id}/rules/vpn_${k}`;
  };

  const devicesEditor = (rule) => {
    const cur = (st.allow || {})[rule.name] || [];
    const ta = h("textarea", { class: "input mono", rows: 3, spellcheck: "false", placeholder: "192.168.1.20\n192.168.1.32/30",
      "aria-label": `Devices allowed to use ${rule.name}` }, cur.join("\n"));
    return h("div", { class: "field" },
      h("span", { class: "label" }, h("b", { class: "mono" }, rule.name)),
      ta,
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn", onclick: busyClick(async () => {
          const allow = { ...(st.allow || {}), [rule.name]: ta.value.split(/[\s,]+/).filter(Boolean) };
          await save({ allow }, `Devices of ${rule.name} saved`);
        }) }, "Save devices"),
        h("span", { class: "xs muted" }, cur.length ? `Only these devices use ${rule.name}; the others go on as if it weren't there`
          : "Empty: every device uses this rule")));
  };

  const draw = () => {
    const fileIn = h("input", { type: "file", accept: ".ovpn,.conf,text/plain", class: "input", onchange: async (e) => {
      const f = e.target.files[0];
      form.file = f ? await f.text() : null; form.fileName = f ? f.name : "";
    } });
    const user = h("input", { class: "input mid", autocomplete: "off", value: form.username ?? st.username ?? "",
      oninput: (e) => { form.username = e.target.value; } });
    const pass = h("input", { class: "input mid", type: "password", autocomplete: "new-password",
      placeholder: st.password_set ? "saved, leave empty to keep" : "", oninput: (e) => { form.password = e.target.value; } });
    const rules = vpnRules();
    put(page,
      pageHead("VPN", "OpenVPN on the router: the sites of VPN rules go through it"),
      h("div", { class: "cards-2" },
        card("Tunnel", null,
          h("div", { class: "stack", style: "gap: 12px" },
            h("div", { class: "row" }, statePill(), st.remote ? h("span", { class: "small muted mono" }, st.remote) : null),
            !st.installed ? h("div", { class: "stack", style: "gap: 8px" },
              h("p", { class: "small muted" }, "The router needs the openvpn-openssl and kmod-tun packages (about 1 MB of flash). If the package server is cut off, they come through a working server."),
              h("div", {}, h("button", { type: "button", class: "btn primary", onclick: install }, "Install OpenVPN"))) : null,
            h("label", { class: "check" },
              h("input", { type: "checkbox", role: "switch", checked: st.enabled, disabled: !st.installed || !st.configured,
                onchange: busyClick(async (e) => {
                  const on = e.target.checked;
                  if (!await save({ enabled: on }, on ? "VPN on" : "VPN off")) e.target.checked = !on;
                }) }),
              h("span", { class: "col" }, h("span", { style: "font-weight: 500" }, "VPN on"),
                h("span", {}, "Off or not connected: the sites of VPN rules don't open (they never go out direct)"))))),
        card("Config", "The .ovpn file from your VPN provider, with certificates inline",
          h("div", { class: "form-grid" },
            h("label", { class: "lbl" }, "File"), h("div", { class: "ctl" }, fileIn,
              h("span", { class: "xs muted" }, st.configured ? "A config is saved; choose a file to replace it" : "No config yet")),
            h("label", { class: "lbl" }, "Login"), h("div", { class: "ctl" }, user),
            h("label", { class: "lbl" }, "Password"), h("div", { class: "ctl" }, pass,
              h("span", { class: "xs muted" }, "Kept on the router only (root-only file), never in the manager"))),
          h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", onclick: busyClick(async () => {
            const body = {};
            if (form.file !== null) body.config = form.file;
            const u = (form.username ?? st.username ?? "").trim();
            if (form.username !== null || form.password) { body.username = u; if (form.password) body.password = form.password; }
            if (!Object.keys(body).length) { toast("Choose a file or change the login first", true); return; }
            if (await save(body, "VPN config saved")) Object.assign(form, { file: null, fileName: "", username: null, password: "" });
          }) }, "Save")))),
      card("Rules and devices", "Rules with connection type VPN and interface pkvpn",
        rules.length ? h("div", { class: "stack", style: "gap: 14px" }, rules.map(devicesEditor))
          : h("p", { class: "small muted" }, "No rule uses the VPN yet."),
        h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: addRule }, "Add a VPN rule"),
          h("span", { class: "xs muted" }, "Then add its domains on the rule page and apply"))),
      card("Log", "OpenVPN's last lines on the router",
        h("div", { class: "row" }, h("button", { type: "button", class: "btn ghost", onclick: busyClick(load) }, "Refresh")),
        st.log && st.log.length ? h("pre", { class: "mono xs", style: "white-space: pre-wrap; max-height: 320px; overflow: auto" }, st.log.join("\n"))
          : h("p", { class: "small muted" }, "Nothing logged yet.")));
  };

  await load();
}
