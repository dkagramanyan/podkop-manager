// configs.js: part of podkop-manager's web interface (see app.js)

import { draftChanges, pageHead } from "./shell.js";
import { S } from "./state.js";
import { api, h, modal, put, shortWhen, toast } from "./ui.js";
import { busyClick } from "./usage.js";

// --- saved configs: archive, export, import -------------------------------------------------------------------

// a saved config's router part, in words: "kill switch on meta; Protected: none; nightly probe at 03:00"
export function routerText(c) {
  const r = c.router || {}, out = [];
  const list = (k, what) => { if (Array.isArray(r[k])) out.push(`${what} ${r[k].length ? `on ${r[k].join(", ")}` : "on no rule"}`); };
  list("kill_switch", "kill switch"); list("protected", "Protected"); list("heal_off", "self-heal off");
  if (r.probe && Object.keys(r.probe).length) out.push(r.probe.schedule ? `nightly probe at ${r.probe.schedule}` : "nightly probe off");
  return out.join("; ");
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
    run(async () => api("POST", "/api/configs/import", { text, file: file.name }), `Imported ${file.name}`);
  } });

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
      + (routerText(c) ? ` Once it is applied: ${routerText(c)}.` : ""),
      "Load into draft", "primary", async () => {
        try {
          const r = await api("POST", `/api/routers/${id}/configs/${c.id}/load`);
          if (r.now) toast(`${rt.name} runs these settings already: the config's switches are being set`);
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
              routerText(c) ? h("span", { class: "xs muted", title: routerText(c) }, ` · ${routerText(c)}`) : null),
            h("td", { class: "r nowrap" }, h("span", { class: "row", style: "gap: 6px; justify-content: flex-end; flex-wrap: nowrap" },
              h("button", { type: "button", class: "btn xs", onclick: () => loadInto(c) }, "Load into draft"),
              h("a", { class: "btn xs", href: `/api/configs/${c.id}/file`, download: "" }, "Download"),
              h("button", { type: "button", class: "btn xs", onclick: () => rename(c) }, "Rename"),
              h("button", { type: "button", class: "btn xs danger", onclick: () => confirm(`Delete ${c.name}?`, "The saved config goes away; routers keep what they run.",
                "Delete", "danger", () => run(() => api("DELETE", `/api/configs/${c.id}`), "Deleted")) }, "Delete")))))))
          : h("p", { class: "empty" }, "No saved configs. Save this router's config, or import a file: one downloaded here, or podkop's /etc/config/podkop.")),
      h("p", { class: "small muted" }, "A config keeps every rule's settings: podkop's, the probe's, kill switch, Protected, self-heal off and the nightly probe; "
        + "the switches are set after the config is applied. Keys are not in it: they stay in this manager."));
  };

  await load();
  draw();
  S.view = { onRouter: (r) => { if (r.id === id) draw(); } };
}
