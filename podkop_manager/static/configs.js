// configs.js: part of podkop-manager's web interface (see app.js)

import { draftChanges, pageHead } from "./shell.js";
import { S } from "./state.js";
import { api, h, modal, put, shortWhen, toast } from "./ui.js";
import { busyClick } from "./usage.js";

// --- saved configs: archive, export, import -------------------------------------------------------------------

// {"format": "podkop-manager-zapret", "rules": {rule: {args, url, name, service}}} -> it, or null
export function zapretFile(text) {
  let d;
  try { d = JSON.parse(text.replace(/^\ufeff/, "")); } catch { return null; }
  if (!d || typeof d !== "object" || d.config || !d.rules || typeof d.rules !== "object") return null;
  const rules = Object.values(d.rules);
  return d.format === "podkop-manager-zapret" || (rules.length && rules.every((r) => r && Array.isArray(r.args))) ? d : null;
}

export async function viewConfigs(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let list = [];
  const load = async () => { list = await api("GET", "/api/configs"); };
  const run = async (fn, ok) => {
    try { await fn(); if (ok) toast(ok); await load(); draw(); } catch (e) { toast(e.message, true); }
  };

  const fileInput = h("input", { type: "file", hidden: true, onchange: async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const text = await file.text();
    const z = zapretFile(text);
    if (z) importZapret(file.name, z);
    else run(async () => api("POST", "/api/configs/import", { text, file: file.name }), `Imported ${file.name}`);
  } });

  // a zapret file (zapret first for some rules, with their strategies) goes to this router, not to the list
  const importZapret = (fname, z) => {
    const rt = S.routers[id], names = Object.keys(z.rules);
    confirm(`Turn on zapret first on ${rt.name}?`,
      `${fname}: ${names.join(", ")} get zapret first with the file's strategies; zapret2 is installed on the router first if it has none (about 0.4 MB). `
      + "Each rule keeps its servers for when zapret does not open its sites; other zapret rules stay as they are.",
      "Turn on", "primary", async () => {
        try {
          toast(`Turning zapret on for ${names.join(", ")}…`);
          const r = await api("POST", `/api/routers/${id}/zapret/import`, { rules: z.rules });
          const on = r.rules.filter((x) => !r.refused.includes(x));
          toast(`${r.installed ? "zapret2 installed; " : ""}zapret first on ${on.join(", ") || "no rule"}`
            + `${r.refused.length ? `; not on ${r.refused.join(", ")} (kill switch or Protected)` : ""}`, r.refused.length > 0);
        } catch (err) { toast(err.message, true); }
      });
  };

  const confirm = (title, text, label, cls, go) => {
    const m = modal({ title, narrow: true, body: h("p", { class: "small" }, text),
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: `btn ${cls}`, onclick: () => { m.close(); go(); } }, label))] });
  };

  const rename = (c) => {
    const input = h("input", { class: "input", id: "cfg-name", value: c.name });
    const save = () => { m.close(); run(() => api("PUT", `/api/configs/${c.id}`, { name: input.value })); };
    input.onkeydown = (e) => { if (e.key === "Enter") save(); };
    const m = modal({ title: "Rename config", narrow: true, body: h("div", { class: "field" }, h("label", { for: "cfg-name" }, "Name"), input),
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: save }, "Rename"))] });
    input.select();
  };

  const loadInto = (c) => {
    const rt = S.routers[id];
    confirm(`Load ${c.name}?`, `It becomes the draft of ${rt.name}${draftChanges(rt) ? ", replacing the draft you have" : ""}. Rules that take links from probe get the last probe's servers. Nothing changes on the router until you apply.`
      + ((c.zapret || []).length ? ` Once it is applied, ${c.zapret.join(", ")} get zapret first with the file's strategies (zapret2 is installed on the router first if it has none).` : ""),
      "Load into draft", "primary", async () => {
        try {
          const r = await api("POST", `/api/routers/${id}/configs/${c.id}/load`);
          if (r.zapret === "now") toast(`${rt.name} runs these settings already: zapret first is being turned on for ${c.zapret.join(", ")}`);
          location.hash = `#/r/${id}/rules`;
        }
        catch (e) { toast(e.message, true); }
      });
  };

  const draw = () => {
    const rt = S.routers[id];
    if (!rt) return;
    put(page,
      pageHead("Configs", "podkop's settings with each rule's probe parameters",
        h("button", { type: "button", class: "btn", onclick: () => fileInput.click() }, "Import file"),
        draftChanges(rt) ? h("button", { type: "button", class: "btn", onclick: busyClick(() => run(() => api("POST", "/api/configs", { router_id: id, from: "draft" }), "Draft saved")) }, "Save draft") : null,
        h("button", { type: "button", class: "btn primary", disabled: !rt.online,
          onclick: busyClick(() => run(() => api("POST", "/api/configs", { router_id: id }), "Saved")) }, `Save ${rt.name}'s config`)),
      fileInput,
      h("section", { class: "card clip", "aria-label": "Saved configs" },
        list.length ? h("div", { class: "scroll-x" }, h("table", { class: "wide" },
          h("tr", {}, h("th", { scope: "col" }, "Name"), h("th", { scope: "col" }, "From"), h("th", { scope: "col" }, "Saved"),
            h("th", { scope: "col" }, "Rules"), h("th", { scope: "col", class: "r" }, h("span", { class: "sr" }, "Actions"))),
          list.map((c) => h("tr", {},
            h("td", { style: "font-weight: 500" }, c.name),
            h("td", { style: "color: var(--text-2)" }, c.source, c.podkop ? h("span", { class: "xs muted" }, ` · podkop ${c.podkop}`) : null),
            h("td", { class: "num nowrap", style: "color: var(--text-2)" }, shortWhen(c.t)),
            h("td", { style: "color: var(--text-2)" }, c.rules.join(", ") || "–",
              c.from_probe.length ? h("span", { class: "xs muted" }, ` · ${c.from_probe.length} from probe`) : null,
              (c.zapret || []).length ? h("span", { class: "xs muted", title: c.zapret.join(", ") }, ` · zapret first: ${c.zapret.join(", ")}`) : null),
            h("td", { class: "r nowrap" }, h("span", { class: "row", style: "gap: 6px; justify-content: flex-end; flex-wrap: nowrap" },
              h("button", { type: "button", class: "btn xs", onclick: () => loadInto(c) }, "Load into draft"),
              h("a", { class: "btn xs", href: `/api/configs/${c.id}/file`, download: "" }, "Download"),
              h("button", { type: "button", class: "btn xs", onclick: () => rename(c) }, "Rename"),
              h("button", { type: "button", class: "btn xs danger", onclick: () => confirm(`Delete ${c.name}?`, "The saved config goes away; routers keep what they run.",
                "Delete", "danger", () => run(() => api("DELETE", `/api/configs/${c.id}`), "Deleted")) }, "Delete")))))))
          : h("p", { class: "empty" }, "No saved configs. Save this router's config, or import a file: one downloaded here, or podkop's /etc/config/podkop.")),
      h("p", { class: "small muted" }, "A config can carry its rules' zapret first: it is turned on after the config is applied. "
        + "Import file also takes a zapret file alone (zapret first for some rules, with their strategies): it goes to this router at once."));
  };

  await load();
  draw();
  S.view = { onRouter: (r) => { if (r.id === id) draw(); } };
}
