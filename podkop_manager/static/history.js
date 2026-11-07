// history.js: part of podkop-manager's web interface (see app.js)

import { openApply } from "./apply.js";
import { optionLabel, ruleDest, ruleMatches, rulesOf, runningVersion } from "./model.js";
import { pageHead, seg } from "./shell.js";
import { S } from "./state.js";
import { api, asList, h, icon, isLink, linkName, longWhen, plural, put, toast } from "./ui.js";
import { busyClick } from "./usage.js";

// --- changes, as shown in History and the apply dialog ----------------------------------------------

export const TEXT_LISTS = new Set(["user_domains_text", "user_subnets_text"]);
export const shortValue = (v) => (isLink(v) ? linkName(v) : String(v).length > 60 ? String(v).slice(0, 57) + "…" : String(v));

// a few chips, then "and N more"
export function chipList(items, cls) {
  const box = h("span", { class: "vals" });
  const draw = (all) => put(box, (all ? items : items.slice(0, 6)).map((x) => h("span", { class: `${cls}${isLink(x) ? " plain" : ""}` },
    cls === "add" ? `+ ${shortValue(x)}` : shortValue(x))),
  !all && items.length > 6 ? h("button", { type: "button", class: "link-btn", onclick: () => draw(true) }, `and ${items.length - 6} more`) : null);
  draw(false);
  return box;
}

// option values as the editor names them
export const VALUE_LABELS = {
  connection_type: { proxy: "Proxy", vpn: "VPN", block: "Block", exclusion: "Direct" },
  proxy_config_type: { url: "One link", selector: "Selector", urltest: "Fastest (URLTest)", outbound: "sing-box JSON" },
  user_domain_list_type: { disabled: "Off", dynamic: "List", text: "Text" },
  user_subnet_list_type: { disabled: "Off", dynamic: "List", text: "Text" },
};

// the old and new value of one option
export function changeValue(opt, oldV, newV) {
  const names = VALUE_LABELS[opt];
  if (names) { oldV = oldV === undefined ? oldV : names[oldV] || oldV; newV = newV === undefined ? newV : names[newV] || newV; }
  if (Array.isArray(oldV) || Array.isArray(newV) || TEXT_LISTS.has(opt)) {
    const split = (v) => (TEXT_LISTS.has(opt) && !Array.isArray(v) ? String(v || "").split(/[\s,]+/).filter(Boolean) : asList(v));
    const o = split(oldV), n = split(newV);
    const add = n.filter((x) => !o.includes(x)), del = o.filter((x) => !n.includes(x));
    if (!add.length && !del.length) return h("span", { class: "muted" }, "same items, new order");
    return h("span", { class: "vals" }, add.length ? chipList(add, "add") : null, del.length ? chipList(del, "del") : null);
  }
  if (opt === "outbound_json") return h("span", {}, "changed");
  if (oldV === undefined || oldV === null) return h("span", { class: "vals" }, h("span", { class: "add" }, shortValue(newV)));
  if (newV === undefined || newV === null) return h("span", { class: "vals" }, h("span", { class: "del" }, shortValue(oldV)), h("span", { class: "muted xs" }, "back to default"));
  return h("span", { class: "vals" }, h("span", { class: "del" }, shortValue(oldV)), icon("arrow", 14, { stroke: "var(--muted)" }), h("span", { class: "add" }, shortValue(newV)));
}

// "Direct, 12 domains" for a rule added in a change
export function newRuleSummary(options) {
  const sec = { name: "", options };
  const m = ruleMatches(sec);
  return `${ruleDest(sec)[0]}${m === "nothing yet" ? "" : ", " + m}`;
}

// [section, label, value] rows for a diff; config (optional) gives rule positions
export function changeRows(diff, config) {
  const pos = (n) => rulesOf(config).findIndex((s) => s.name === n) + 1;
  return diff.map((d) => {
    if (d.change === "added") {
      return [d.section, d.type === "section" ? "New rule" : `New ${d.type}`,
        h("span", {}, d.type === "section" ? newRuleSummary(d.options) + (config ? `, rule ${pos(d.section)}` : "") : "")];
    }
    if (d.change === "removed") return [d.section, "Rule removed", h("span", {})];
    if (d.change === "order") return ["", "Rule order", h("span", { class: "mono xs" }, d.new.filter((n) => n !== "settings").join(" → "))];
    return [d.section, optionLabel(d.section, d.option), changeValue(d.option, d.old, d.new)];
  });
}

// --- history -------------------------------------------------------------------------------------------

export const VERSION_STATE = {
  applied: ["applied", "ok"], saved: ["saved", "ok"], unchanged: ["no change", ""], reverted: ["undone", "bad"],
  rejected: ["rejected", "warn"], failed: ["failed", "bad"], pending: ["applying", "accent"], running: ["applying", "accent"],
  unknown: ["unknown", ""], scheduled: ["scheduled", "accent"], cancelled: ["cancelled", ""],
};

export function versionWho(v) {
  if (v.trigger === "probe") return /nightly/i.test(v.note || "") ? "nightly probe" : "probe result, by you";
  if (v.trigger === "usage") return "traffic limit";
  if (v.trigger === "keys") return "provider's new servers";
  return "by you";
}

export function versionMeta(v, running) {
  const parts = [v.state === "scheduled" ? `for ${longWhen(v.due)}` : longWhen(v.t), versionWho(v)];
  if (v.due && v.state !== "scheduled") parts.push(`scheduled for ${longWhen(v.due)}`);
  if (v.diff.length) parts.push(plural(v.diff.length, "change"));
  if (running) parts.push("running now");
  if (v.state === "reverted") parts.push(`the router put the old settings back${v.seconds ? ` after ${v.seconds} s` : ""}`);
  if (v.state === "rejected") parts.push("stopped before podkop was touched");
  return parts.join(" · ");
}

export const problemText = (p) => `${p.section}${p.option ? " / " + p.option : ""}${p.index !== undefined && p.index !== null ? " #" + (p.index + 1) : ""}: ${p.message}`;

export function versionReason(v) {
  if (v.state === "reverted") return ["podkop did not start with these settings", v.reason || v.message, ""];
  if (v.state === "failed") return ["The change failed", v.message, ""];
  if (v.state === "rejected") {
    if (v.problems && v.problems.length) return ["Check failed", v.problems.map(problemText).join("\n"), "warn"];
    return [v.code === "config_changed" ? "podkop's settings changed on the router first" : "Not applied", v.message, "warn"];
  }
  return null;
}

export async function viewHistory(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let list = [], more = true, filter = "all";
  const open = new Set();
  const load = async (older) => {
    const before = older && list.length ? `&before=${list[list.length - 1].id}` : "";
    const got = await api("GET", `/api/routers/${id}/versions?limit=30${before}`);
    list = older ? list.concat(got) : got;
    more = got.length === 30;
  };

  const draw = () => {
    const rt = S.routers[id];
    if (!rt) return;
    const runningId = (runningVersion(rt, list) || {}).id;
    const sched = list.filter((v) => v.state === "scheduled").sort((a, b) => a.due - b.due);
    const shown = list.filter((v) => v.state !== "scheduled" && (filter === "all" || (filter === "ok" ? ["applied", "saved"].includes(v.state)
      : ["reverted", "rejected", "failed"].includes(v.state))));
    put(page,
      pageHead("History"),
      sched.length ? h("section", { class: "stack", "aria-labelledby": "h-sched", style: "gap: 8px" },
        h("h2", { id: "h-sched", style: "margin: 0; font-size: 15px" }, "Scheduled"),
        h("ol", { class: "versions" }, sched.map(scheduledItem))) : null,
      h("div", { class: "row" }, seg([["all", "All"], ["ok", "Applied"], ["bad", "Undone or rejected"]], filter,
        (v) => { filter = v; draw(); }, { label: "Show" })),
      shown.length ? h("ol", { class: "versions" }, shown.map((v) => {
        const [label, cls] = VERSION_STATE[v.state] || [v.state, ""];
        const reason = versionReason(v);
        const isOpen = open.has(v.id);
        const rows = isOpen ? changeRows(v.diff) : [];
        return h("li", { class: "card clip" },
          h("div", { class: "vhead" },
            h("span", { class: "vid" }, `#${v.id}`),
            h("span", { class: `pill ${cls}` }, label),
            h("div", { class: "col" }, h("span", {}, v.note || "Change from the web interface"), h("span", {}, versionMeta(v, v.id === runningId))),
            h("div", { class: "row", style: "gap: 6px" },
              v.diff.length ? h("button", { type: "button", class: "btn ghost sm", "aria-expanded": String(isOpen), onclick: () => {
                if (isOpen) open.delete(v.id); else open.add(v.id); draw();
              } }, isOpen ? "Hide changes" : "Show changes", icon(isOpen ? "up" : "down", 14, { width: 1.6 })) : null,
              ["applied", "saved"].includes(v.state) && v.id !== runningId
                ? h("button", { type: "button", class: "btn sm", onclick: () => restore(v.id) }, "Restore") : null)),
          reason ? h("div", { class: `reason ${reason[2]}` }, h("span", {}, reason[0]), h("span", { style: "white-space: pre-wrap" }, reason[1])) : null,
          isOpen ? h("div", { class: "changes-grid" }, rows.map(([sec, lbl, val]) => [
            h("span", { class: "mono xs" }, sec), h("span", { class: "muted" }, lbl), val])) : null);
      })) : h("div", { class: "card empty" }, list.some((v) => v.state !== "scheduled") ? "Nothing here with this filter." : "Nothing applied from this manager yet."),
      more && list.length ? h("button", { type: "button", class: "btn", style: "align-self: center", onclick: busyClick(async () => {
        await load(true); draw();
      }) }, "Show older") : null);
  };

  const act = (path, body, done) => busyClick(async () => {
    try { await api("POST", path, body); toast(done); } catch (e) { toast(e.message, true); }
    await load(); draw();
  });
  const scheduledItem = (v) => {
    const isOpen = open.has(v.id);
    return h("li", { class: "card clip" },
      h("div", { class: "vhead" },
        h("span", { class: "vid" }, `#${v.id}`),
        h("span", { class: "pill accent" }, "scheduled"),
        h("div", { class: "col" }, h("span", {}, v.note || "Change from the web interface"), h("span", {}, versionMeta(v, false))),
        h("div", { class: "row", style: "gap: 6px" },
          h("button", { type: "button", class: "btn ghost sm", "aria-expanded": String(isOpen), onclick: () => {
            if (isOpen) open.delete(v.id); else open.add(v.id); draw();
          } }, isOpen ? "Hide changes" : "Show changes", icon(isOpen ? "up" : "down", 14, { width: 1.6 })),
          h("button", { type: "button", class: "btn sm", onclick: act(`/api/versions/${v.id}/run`, undefined, `#${v.id}: applying now`) }, "Apply now"),
          h("button", { type: "button", class: "btn sm", onclick: act(`/api/versions/${v.id}/cancel`, { to_draft: true }, `#${v.id} is the draft again: edit it in Rules`) }, "Back to draft"),
          h("button", { type: "button", class: "btn sm danger", onclick: act(`/api/versions/${v.id}/cancel`, {}, `#${v.id} cancelled`) }, "Cancel"))),
      isOpen ? h("div", { class: "changes-grid" }, changeRows(v.diff).map(([sec, lbl, val]) => [
        h("span", { class: "mono xs" }, sec), h("span", { class: "muted" }, lbl), val])) : null);
  };

  const restore = async (vid) => {
    try {
      const v = await api("GET", `/api/versions/${vid}`);
      const d = await api("POST", `/api/routers/${id}/diff`, { config: v.config });
      if (!d.diff.length) return toast("The router already has these settings");
      openApply(id, { base: d.fingerprint, config: v.config, note: `Restore #${vid}`, trigger: "restore",
        done: async () => { await load(); draw(); } });
    } catch (e) { toast(e.message, true); }
  };

  await load();
  draw();
  S.view = {
    onRouter: (r) => {
      if (r.id !== id) return;
      // a scheduled change started, was cancelled or added: the list changed
      const ids = (r.scheduled || []).map((x) => x.id).join();
      if (ids !== list.filter((v) => v.state === "scheduled").map((v) => v.id).sort((a, b) => a - b).join()) load().then(draw);
      else draw();
    },
    onJob: (m) => { if (m.router_id === id && FINAL_STATES.includes(m.job.state)) load().then(draw); },
  };
}

export const FINAL_STATES = ["applied", "saved", "unchanged", "rejected", "reverted", "failed"];
