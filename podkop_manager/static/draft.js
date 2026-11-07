// draft.js: part of podkop-manager's web interface (see app.js)

import { openApply } from "./apply.js";
import { problemText, shortValue } from "./history.js";
import { bySection, configDiff, rulesOf, same, sectionsOf } from "./model.js";
import { render, seg } from "./shell.js";
import { S } from "./state.js";
import { OLD_FP, api, asList, clone, fixFingerprint, h, icon, linkDetails, linkName, linkProto, oldFingerprint, plural, put, toast } from "./ui.js";

// --- the routing draft ---------------------------------------------------------------------------------
// One per router, shared by Rules and Global settings, kept on the manager
// (/draft) so it survives reloads. w.config is edited in place.

export async function loadWork(id) {
  const prev = S.work[id];
  if (prev && prev.saving) saveWork(prev);
  if (prev && prev.inflight) await prev.inflight;      // its saves first, or they would look like someone else's
  const [cur, draft, versions, rules, keys] = await Promise.all([api("GET", `/api/routers/${id}/config`),
    api("GET", `/api/routers/${id}/draft`), api("GET", `/api/routers/${id}/versions?limit=50`).catch(() => []),
    api("GET", `/api/routers/${id}/auto-rules`).catch(() => []), api("GET", `/api/routers/${id}/rule-keys`).catch(() => ({ subs: [], rules: {}, links: {} }))]);
  const old = S.work[id];
  const w = {
    id, orig: cur.config, fp: cur.fingerprint,
    ctx: { lists: cur.community_lists || [], devices: cur.devices || [], interfaces: cur.interfaces || [] },
    config: draft ? draft.config : clone(cur.config), base: draft ? draft.base : cur.fingerprint,
    rev: draft ? draft.updated : null,     // the draft this page started from, see saveWork
    versions, auto: Object.fromEntries(rules.map((r) => [r.section, r])), keys,
    problems: old && old.problems && draft && same(old.config, draft.config) ? old.problems : null,
    saving: null, inflight: null, loaded: Date.now(), redraw: () => {},
  };
  S.work[id] = w;
  return w;
}

// workFor: the draft as loaded a moment ago when moving between rules (or to
// Global settings) of one router; loaded again after any other page (Configs
// may have changed it), after 2 minutes, or when the router's settings changed
export async function workFor(id) {
  const w = S.work[id], rt = S.routers[id];
  if (w && !w.stale && [`${id}/rules`, `${id}/global`].includes(S.from) && Date.now() - w.loaded < 120e3
      && rt && rt.status && rt.status.fingerprint === w.fp) return w;
  return loadWork(id);
}

export const workChanges = (w) => configDiff(w.orig, w.config);

// one save at a time: each PUT goes after the one before, with the rev that one returned
export function saveWork(w) {
  clearTimeout(w.saving);
  w.saving = null;
  w.inflight = (w.inflight || Promise.resolve()).then(() => putWork(w));
  return w.inflight;
}

export async function putWork(w) {
  if (w.stale) return;
  try {
    if (workChanges(w).length) {
      const d = await api("PUT", `/api/routers/${w.id}/draft`, { config: w.config, base: w.base, rev: w.rev });
      w.rev = d ? d.updated : null;
    } else {
      await api("DELETE", `/api/routers/${w.id}/draft${w.rev !== null ? `?rev=${w.rev}` : ""}`);
      w.rev = null;
    }
  } catch (e) {
    if (e.status !== 409) { toast(`The draft was not saved: ${e.message}`, true); return; }
    // another tab, or "Load into draft", changed it: show that one instead of overwriting it
    toast("The draft was changed elsewhere; showing that one", true);
    w.stale = true;
    // not awaited: loadWork waits for this save to end
    if (S.work[w.id] === w) loadWork(w.id).then(render).catch((e2) => toast(e2.message, true));
  }
}

// after every edit: save the draft a moment later, update the bar
export function touch(w) {
  if (S.work[w.id] !== w) return;      // left behind by a newer load of the draft: never save it
  w.problems = w.problems ? Object.assign(w.problems, { stale: true }) : null;
  clearTimeout(w.saving);
  w.saving = setTimeout(() => saveWork(w), 500);
  w.drawBar && w.drawBar();
}

export async function discardWork(w) {
  clearTimeout(w.saving);
  w.saving = null;
  await api("DELETE", `/api/routers/${w.id}/draft?discard=1`);
  await loadWork(w.id);
  render();
}

export async function checkWork(w) {
  w.checking = true; w.drawBar && w.drawBar();
  try {
    const r = await api("POST", `/api/routers/${w.id}/validate`, { config: w.config });
    w.problems = r.problems;
  } catch (e) { toast(e.message, true); }
  w.checking = false;
  w.redraw();
}

export function reviewWork(w) {
  openApply(w.id, { base: w.base, config: w.config, note: "", trigger: "web", done: async (ok) => {
    if (ok) { await loadWork(w.id); render(); }
  } });
}

// the bar over Rules: changes, problems, Discard, Check, Review and apply
export function draftBar(w) {
  const box = h("div");
  w.drawBar = () => {
    const n = workChanges(w).length;
    const rt = S.routers[w.id];
    // while the router applies, its fingerprint moves; that is not someone else's change
    const busy = !!(rt && rt.status && rt.status.podkop.busy);
    const stale = w.base !== w.fp && !busy;
    if (!n && !stale) { put(box); return; }
    const probs = w.problems || [];
    put(box, h("div", { class: `banner${stale ? " bad" : ""}`, role: "region", "aria-label": "Unapplied changes" },
      h("span", { class: `dot ${stale ? "bad" : "accent"}` }),
      h("div", { class: "row grow", style: "flex: 1 1 300px; align-items: baseline; gap: 4px 12px" },
        h("span", { style: "font-size: 14px; font-weight: 600" }, n ? `${plural(n, "change")} not applied` : "Draft"),
        stale ? h("span", { class: "small bad-text" }, "Settings changed on the router since. Discard and edit again.")
          : busy ? h("span", { class: "small muted" }, "The router is applying settings now…")
            : w.checking ? h("span", { class: "small muted" }, "Checking on the router…")
            : w.problems ? h("span", { class: `small ${probs.length ? "bad-text" : "ok-text"}` },
              probs.length ? `${plural(probs.length, "problem")} found by Check${w.problems.stale ? ", changed since" : ""}` : `Check found no problems${w.problems.stale ? ", changed since" : ""}`) : null),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn ghost", onclick: async () => {
          if (confirm(`Discard ${plural(n, "change")}? The router keeps its settings.`)) await discardWork(w);
        } }, "Discard"),
        !stale ? h("button", { type: "button", class: "btn", disabled: w.checking, onclick: () => checkWork(w) }, w.problems ? "Check again" : "Check") : null,
        !stale ? h("button", { type: "button", class: "btn primary", onclick: () => reviewWork(w) }, "Review and apply") : null)),
    probs.length ? h("div", { class: "problem-box", style: "margin-top: 8px" }, probs.map((p) => h("div", {},
      p.section && rulesOf(w.config).some((s) => s.name === p.section)
        ? h("a", { href: `#/r/${w.id}/rules/${p.section}`, style: "color: inherit; text-decoration: underline" }, problemText(p)) : problemText(p)))) : null);
  };
  w.drawBar && w.drawBar();
  return box;
}

// --- fields bound to one section of the draft --------------------------------------------------------

export function binder(w, section, defs, { defaults = true } = {}) {
  const sec = () => sectionsOf(w.config).find((s) => s.name === section);
  const orig = bySection(w.orig).get(section);
  const o = () => (sec().options || (sec().options = {}));
  const def = (opt) => (defs.find((d) => d.name === opt) || {}).default;
  const b = {
    get: (opt) => o()[opt],
    val: (opt) => (o()[opt] !== undefined ? o()[opt] : def(opt)),
    set: (opt, v, redraw) => {
      if (v === "" || v === null || v === undefined || (Array.isArray(v) && !v.length)) delete o()[opt];
      else o()[opt] = v;
      touch(w);
      if (redraw) w.redraw();
    },
    old: (opt) => (orig ? (orig.options || {})[opt] : undefined),
    changed: (opt) => !!orig && !same(b.old(opt), o()[opt]),
    isNew: !orig,
    // dot: accent when changed in the draft, gray when it differs from podkop's default
    dot: (opt) => {
      if (b.changed(opt)) return h("span", { class: "dot sm accent", title: "changed in this draft" });
      const d = def(opt), v = o()[opt];
      return defaults && v !== undefined && d !== undefined && !same(asList(v), asList(d)) ? h("span", { class: "dot sm gray", title: "differs from podkop's default" }) : null;
    },
    was: (opt) => {
      if (!b.changed(opt) || Array.isArray(b.old(opt)) || Array.isArray(o()[opt])) return null;
      const old = b.old(opt);
      return h("span", { class: "was" }, old === undefined ? "was not set" : `was ${shortValue(old)}`);
    },
    problems: (opt) => (w.problems || []).filter((p) => p.section === section && p.option === opt && (p.index === undefined || p.index === null)),
    problemEls: (opt) => b.problems(opt).map((p) => h("span", { class: "field-problem" }, p.message)),
    indexProblem: (opt, i) => (w.problems || []).find((p) => p.section === section && p.option === opt && p.index === i),
  };
  return b;
}

// focus survives a redraw when controls carry data-k
export function keepFocus(fn) {
  const a = document.activeElement;
  const k = a && a.getAttribute && a.getAttribute("data-k");
  const pos = a && "selectionStart" in a ? a.selectionStart : null;
  fn();
  if (!k) return;
  const el = document.querySelector(`[data-k="${CSS.escape(k)}"]`);
  if (el) { el.focus(); if (pos !== null && "setSelectionRange" in el) try { el.setSelectionRange(pos, pos); } catch { /* not a text field */ } }
}

// label with its marker dot
export const lbl = (b, opt, text, forId) => h(forId ? "label" : "span", { class: forId ? null : "label", for: forId || null }, b.dot(opt), text);

export function textField(b, opt, { mono, placeholder, width, type } = {}) {
  return h("input", { class: `input mid${mono ? " mono" : ""}`, style: width ? `max-width: ${width}` : null, type: type || "text",
    "data-k": `${opt}`, value: b.get(opt) ?? "", placeholder: placeholder ?? (b.val(opt) ?? ""),
    oninput: (e) => b.set(opt, e.target.value), onchange: () => keepFocus(() => {}) });
}

export function comboField(b, opt, values, { mono = true } = {}) {
  const lid = `dl-${opt}`;
  return h("span", { style: "display: contents" },
    h("input", { class: `input mid${mono ? " mono" : ""}`, list: lid, "data-k": opt, value: b.get(opt) ?? "", placeholder: b.val(opt) ?? "",
      oninput: (e) => b.set(opt, e.target.value) }),
    h("datalist", { id: lid }, values.map((v) => h("option", { value: v }))));
}

export function selectField(b, opt, values, { mono } = {}) {
  const v = b.val(opt);
  return h("select", { class: `input mid${mono ? " mono" : ""}`, "data-k": opt, onchange: (e) => b.set(opt, e.target.value, true) },
    v === undefined ? h("option", { value: "" }, "—") : null,
    [...new Set([...values, ...(v !== undefined ? [v] : [])])].map((x) => h("option", { value: x, selected: x === v }, x)));
}

export function flagField(b, opt, text, sub, { disabled } = {}) {
  return h("label", { class: sub ? "check" : "check inline" },
    h("input", { type: "checkbox", "data-k": opt, checked: b.val(opt) === "1", disabled,
      onchange: (e) => b.set(opt, e.target.checked ? "1" : "0", true) }),
    sub ? h("span", { class: "col" }, h("span", { class: "row", style: "gap: 8px" }, text, b.dot(opt)), h("span", {}, sub))
      : h("span", { class: "row", style: "gap: 8px" }, text, b.dot(opt)));
}

export function segField(b, opt, values, labels) {
  return seg(values.map((v, i) => [v, labels ? labels[i] : v]), b.val(opt), (v) => b.set(opt, v, true), { small: true, label: opt });
}

// chips for a list of words (domains, subnets, URLs); new ones are green
export function tokenField(b, opt, { placeholder, splitter = /[\s,]+/ } = {}) {
  const items = asList(b.get(opt));
  const old = asList(b.old(opt));
  const add = (text) => {
    const more = text.split(splitter).map((x) => x.trim()).filter(Boolean).filter((x) => !items.includes(x));
    if (more.length) b.set(opt, [...items, ...more], true);
  };
  return h("div", { class: "tokens" },
    items.map((x, i) => h("span", { class: `token${b.isNew || !old.includes(x) ? " new" : ""}${b.indexProblem(opt, i) ? " bad" : ""}`,
      title: b.indexProblem(opt, i) ? b.indexProblem(opt, i).message : null }, x,
    h("button", { type: "button", "aria-label": `Remove ${x}`, onclick: () => b.set(opt, items.filter((y) => y !== x), true) },
      icon("close", 10, { width: 2 })))),
    h("label", { class: "sr", for: `add-${opt}` }, `Add to ${opt}`),
    h("input", { id: `add-${opt}`, "data-k": `add-${opt}`, placeholder: placeholder || "Add, Enter",
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === ",") { e.preventDefault(); const v = e.target.value; e.target.value = ""; keepFocus(() => add(v)); }
        else if (e.key === "Backspace" && !e.target.value && items.length) keepFocus(() => b.set(opt, items.slice(0, -1), true));
      },
      onpaste: (e) => {
        const text = (e.clipboardData || window.clipboardData).getData("text");
        if (/[\s,]/.test(text.trim())) { e.preventDefault(); keepFocus(() => add(text)); }
      },
      onblur: (e) => { if (e.target.value.trim()) { const v = e.target.value; e.target.value = ""; add(v); } } }));
}

// Off / List / Text for user domains and subnets
export function userListField(b, kind) {
  const type = `user_${kind}_list_type`, list = `user_${kind}s`, text = `user_${kind}s_text`;
  const mode = b.val(type) || "disabled";
  const name = kind === "domain" ? "Your domains" : "Your subnets";
  const changed = b.changed(type) || b.changed(list) || b.changed(text);
  const label = h("span", { class: "label" }, changed ? h("span", { class: "dot sm accent" }) : null, name);
  if (mode === "disabled" && kind === "subnet") {
    return h("div", { class: "mini" }, label, h("span", { class: "row" }, h("span", { class: "small muted" }, "Off"),
      h("button", { type: "button", class: "link-btn", onclick: () => b.set(type, "dynamic", true) }, "Turn on")));
  }
  return h("div", { class: "field" },
    h("div", { class: "row", style: "justify-content: space-between" }, label,
      seg([["disabled", "Off"], ["dynamic", "List"], ["text", "Text"]], mode, (v) => b.set(type, v, true), { small: true, label: name })),
    mode === "dynamic" ? tokenField(b, list, { placeholder: kind === "domain" ? "Add domain, Enter" : "Add subnet, Enter" }) : null,
    mode === "text" ? h("textarea", { class: "input", rows: 5, "data-k": text, oninput: (e) => b.set(text, e.target.value) }, b.get(text) || "") : null,
    mode === "dynamic" ? h("span", { class: "help" }, "Green: new in this draft") : null,
    b.problemEls(list), b.problemEls(text));
}

// --- links ----------------------------------------------------------------------------------------------

export const LINK_RE = /[a-z0-9]+:\/\/[^\s"'<>]+/gi;

// the list of server links of a rule: rows with name, protocol, host; drag, remove, add
export function serversField(w, b, opt, { title, help, collapsible }) {
  const links = asList(b.get(opt));
  const old = asList(b.old(opt));
  const ui = w.ui[`${b.name}:${opt}`] || (w.ui[`${b.name}:${opt}`] = { open: !collapsible, all: false });
  const head = h("div", { class: "servers-head" },
    h("span", {}, b.changed(opt) ? h("span", { class: "dot sm accent" }) : null, `${title}, ${links.length}`));
  if (collapsible && !ui.open) {
    return h("div", { class: "mini" }, h("div", { class: "col" }, h("span", { class: "row", style: "gap: 8px; font-size: 13px; font-weight: 500" },
      b.changed(opt) ? h("span", { class: "dot sm accent" }) : null, `${title}, ${links.length}`), h("span", { class: "xs muted" }, help)),
    h("button", { type: "button", class: "btn sm", style: "width: 32px; padding: 0", "aria-label": `Show ${title.toLowerCase()}`,
      onclick: () => { ui.open = true; w.redraw(); } }, icon("down", 14, { width: 1.6 })));
  }
  const shown = ui.all ? links : links.slice(0, 6);
  let drag = null;
  const move = (from, to) => { const l = [...links]; l.splice(to, 0, l.splice(from, 1)[0]); b.set(opt, l, true); };
  const addArea = h("textarea", { class: "input", rows: 2, id: `paste-${opt}`, "data-k": `paste-${opt}`,
    placeholder: "Paste links, one per line: vless://, trojan://, ss://, hysteria2://, socks://" });
  const addLinks = () => {
    const found = (addArea.value.match(LINK_RE) || []).filter((l) => !links.includes(l));
    if (!found.length) return toast("No links found: they start with vless://, trojan://, ss://, hysteria2:// or socks://", true);
    addArea.value = "";
    b.set(opt, [...links, ...found], true);
  };
  const oldFp = links.filter(oldFingerprint);
  return h("div", { class: "servers" }, head,
    oldFp.length ? h("div", { class: "row", style: "flex-wrap: nowrap; gap: 10px; padding: 10px 14px; background: var(--warn-row); border-bottom: 1px solid var(--line-soft)" },
      h("span", { class: "xs grow", style: "color: var(--warn-deep); line-height: 1.5" },
        `${plural(oldFp.length, "link")} use${oldFp.length === 1 ? "s" : ""} a TLS fingerprint (fp=${(OLD_FP.exec(oldFp[0].split("#")[0]) || [])[2]}) that new REALITY servers reject.`),
      h("button", { type: "button", class: "btn xs", onclick: () => b.set(opt, links.map(fixFingerprint), true) }, "Use chrome")) : null,
    collapsible ? h("div", { class: "servers-more" }, h("span", { class: "xs muted" }, help), " ",
      h("button", { type: "button", class: "link-btn", onclick: () => { ui.open = false; w.redraw(); } }, "Hide")) : null,
    shown.map((l, i) => {
      const p = b.indexProblem(opt, i);
      const isNew = b.isNew || !old.includes(l);
      return h("div", { class: `srv${p ? " bad" : isNew ? " new" : ""}`, draggable: "true",
        ondragstart: (e) => { drag = i; e.dataTransfer.effectAllowed = "move"; e.currentTarget.classList.add("dragging"); },
        ondragend: (e) => e.currentTarget.classList.remove("dragging"),
        ondragover: (e) => e.preventDefault(),
        ondrop: (e) => { e.preventDefault(); if (drag !== null && drag !== i) move(drag, i); drag = null; } },
      h("span", { class: "grip", "aria-hidden": "true" }, icon("grip", 16)),
      h("div", { class: "col" },
        h("div", {}, h("span", { class: "nm" }, linkName(l)), h("span", { class: "proto" }, linkProto(l)),
          isNew ? h("span", { class: "tag ok" }, "new") : null),
        h("span", { class: "host ellipsis", title: linkDetails(l) }, linkDetails(l)),
        p ? h("span", { class: "err" }, p.message) : null),
      h("button", { type: "button", class: "btn icon", "aria-label": `Move ${linkName(l)} up`, title: "Move up", disabled: i === 0,
        onclick: () => move(i, i - 1) }, icon("up", 14, { width: 1.6 })),
      h("button", { type: "button", class: "btn icon", "aria-label": `Remove ${linkName(l)}`, onclick: () => b.set(opt, links.filter((_, j) => j !== i), true) },
        icon("trash", 14, { width: 1.6 })));
    }),
    links.length > 6 ? h("div", { class: "servers-more" }, h("button", { type: "button", class: "link-btn", onclick: () => { ui.all = !ui.all; w.redraw(); } },
      ui.all ? "Show fewer" : `Show ${links.length - 6} more`)) : null,
    h("div", { class: "servers-add" },
      h("div", { class: "row", style: "justify-content: space-between" },
        h("label", { for: `paste-${opt}`, class: "xs", style: "font-weight: 500; color: var(--text-2)" }, "Add servers"),
        h("button", { type: "button", class: "btn xs", onclick: addLinks }, "Add")),
      addArea, b.problemEls(opt)));
}
