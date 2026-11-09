// apply.js: part of podkop-manager's web interface (see app.js)

import { FINAL_STATES, changeRows, chipList, problemText } from "./history.js";
import { diffSummary, ruleDest, ruleMatches, rulesOf, runningVersion } from "./model.js";
import { seg } from "./shell.js";
import { S } from "./state.js";
import { api, asList, h, icon, longWhen, modal, plural, put, spinner, toast } from "./ui.js";

// --- review and apply ------------------------------------------------------------------------------------


// how many server links podkop's parser reads in a config
export const linkCount = (config) => rulesOf(config).filter((s) => (s.options || {}).connection_type === "proxy").reduce((n, s) => {
  const o = s.options, pt = o.proxy_config_type || "url";
  return n + (pt === "url" ? (o.proxy_string ? 1 : 0) : pt === "selector" ? asList(o.selector_proxy_links).length
    : pt === "urltest" ? asList(o.urltest_proxy_links).length + asList(o.urltest_fallback_links).length : 0);
}, 0);

// the diff, one block per rule in the new order
export function changesBlock(diff, config) {
  const rules = rulesOf(config).map((s) => s.name);
  const groups = new Map();
  for (const d of diff) {
    const k = d.change === "order" ? "\u0000order" : d.section;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(d);
  }
  const keys = [...groups.keys()].sort((a, b) => {
    const r = (k) => (k === "settings" ? -1 : k === "\u0000order" ? 1e6 : rules.includes(k) ? rules.indexOf(k) : 1e5);
    return r(a) - r(b);
  });
  return h("section", { class: "dchanges", "aria-label": "Changes" }, keys.map((k) => {
    const items = groups.get(k);
    if (k === "\u0000order") {
      return [h("div", { class: "sec" }, h("span", {}, "Rule order"), h("span", { class: "muted" }, "first match wins")),
        h("div", { class: "rows" }, h("span", {}, "New order"), h("span", { class: "mono xs" }, items[0].new.filter((n) => rules.includes(n)).join(" → ")))];
    }
    const added = items.find((d) => d.change === "added"), removed = items.find((d) => d.change === "removed");
    const pos = rules.indexOf(k) + 1;
    const head = h("div", { class: "sec" }, h("span", {}, k),
      added ? h("span", { class: "tag ok" }, "new rule") : removed ? h("span", { class: "tag bad" }, "removed") : null,
      h("span", { class: "muted" }, added ? `added as rule ${pos}` : removed ? "this rule goes away"
        : k === "settings" ? `global settings, ${plural(items.length, "setting")}` : `rule ${pos}, ${plural(items.length, "setting")}`));
    if (removed) return [head];
    if (added) {
      const sec = { name: k, options: added.options };
      const domains = asList(added.options.user_domains);
      return [head, h("div", { class: "rows" },
        h("span", {}, "Sends to"), h("span", {}, ruleDest(sec)[1] === "direct" && added.options.connection_type === "exclusion" ? "Direct, not through podkop" : ruleDest(sec)[0]),
        h("span", {}, "Matches"), domains.length ? chipList(domains, "add") : h("span", {}, ruleMatches(sec)))];
    }
    return [head, h("div", { class: "rows" }, changeRows(items, config).map(([, label, val]) => [h("span", {}, label), val]))];
  }));
}

export const STEP_TEXT = {
  check: ["Checking the settings on the router", "Checked the settings on the router", "The router refused these settings"],
  write: ["Saving a backup and writing the new settings", "Saved a backup and wrote the new settings", "Could not write the new settings"],
  restart: ["Restarting podkop and checking that it runs", "Restarted podkop, it runs", "podkop did not come up"],
  reload: ["Only servers changed: sing-box takes them without a podkop restart", "sing-box took the new servers, podkop kept running", "sing-box could not take them in place, so podkop restarts"],
  revert: ["Putting the old settings back", "Put the old settings back", "podkop does not run with the old settings either"],
};

// steps [key, status (todo, now, done, fail), seconds] from the job's state and when each state began
export function jobSteps(state, marks, now) {
  const secs = (a, b) => (marks[a] ? Math.round(((marks[b] || now) - marks[a]) / 1000) : null);
  const s = (key, status, from, to) => ({ key, status, t: status === "todo" ? null : secs(from, to) });
  switch (state) {
    case "pending": case "queued": case "checking":
      return [s("check", "now", "check"), s("write", "todo"), s("restart", "todo")];
    case "reloading": return [s("check", "done", "check", "reload"), s("write", "done"), s("reload", "now", "reload")];
    case "restarting": return [s("check", "done", "check", marks.reload ? "reload" : "restart"), s("write", "done"),
      marks.reload ? s("reload", "fail", "reload", "restart") : null, s("restart", "now", "restart")].filter(Boolean);
    case "reverting": return [s("check", "done", "check", "restart"), s("write", "done"), s("restart", "fail", "restart", "revert"), s("revert", "now", "revert")];
    case "applied": return marks.reload && !marks.restart
      ? [s("check", "done", "check", "reload"), s("write", "done"), s("reload", "done", "reload", "end")]
      : [s("check", "done", "check", "restart"), s("write", "done"), s("restart", "done", "restart", "end")];
    case "saved": return [s("check", "done", "check", "end"), s("write", "done"), { key: "saved", status: "done", t: null }];
    case "unchanged": return [s("check", "done", "check", "end"), { key: "unchanged", status: "done", t: null }];
    case "rejected": return [s("check", "fail", "check", "end")];
    case "reverted": return [s("check", "done", "check", "restart"), s("write", "done"), s("restart", "fail", "restart", "revert"), s("revert", "done", "revert", "end")];
    case "failed": return marks.revert ? [s("check", "done", "check", "restart"), s("write", "done"), s("restart", "fail", "restart", "revert"), s("revert", "fail", "revert", "end")]
      : [s("check", "done", "check", "end"), s("write", "fail")];
    default: return [s("check", "now", "check")];
  }
}

export function stepRow(st) {
  const text = st.key === "saved" ? ["", "podkop is stopped, so it was not restarted", ""] : st.key === "unchanged" ? ["", "Nothing to change on the router", ""] : STEP_TEXT[st.key];
  const label = st.status === "done" ? text[1] : st.status === "fail" ? text[2] : text[0];
  const ic = st.status === "now" ? spinner() : h("span", { class: `step-icon ${st.status}` },
    st.status === "done" ? icon("check", 12, { stroke: "#FFFFFF", width: 2.4 }) : st.status === "fail" ? icon("close", 12, { stroke: "#FFFFFF", width: 2.4 }) : null);
  return h("div", { class: `step ${st.status}` }, h("span", { style: "display: inline-flex; width: 20px; justify-content: center" }, ic),
    h("span", {}, label), h("span", {}, st.t !== null && st.t !== undefined ? `${st.t} s` : ""));
}

export let applyOpening = false;     // a second click while the router is asked: no second dialog

export async function openApply(id, { base, config, note, trigger, done }) {
  const rt = S.routers[id];
  if (applyOpening) return;
  applyOpening = true;
  let d, versions;
  try {
    [d, versions] = await Promise.all([api("POST", `/api/routers/${id}/diff`, { config }),
      api("GET", `/api/routers/${id}/versions?limit=50`).catch(() => [])]);
  } catch (e) { toast(e.message, true); return; } finally { applyOpening = false; }
  if (!d.diff.length) { toast("Nothing to apply: the router already has these settings"); return; }
  const running = runningVersion(rt, versions);
  const stale = d.fingerprint !== base;
  const n = d.diff.length;

  const checks = h("section", { class: "checks wait", "aria-label": "Checks" }, h("span", {}, "Checking on the router…"));
  const noteInput = h("input", { id: "apply-note", class: "input", value: note || "", placeholder: diffSummary(d.diff) });
  const steps = h("section", { class: "steps", "aria-label": "Progress", "aria-live": "polite", hidden: true });
  const result = h("div", { "aria-live": "polite" });
  const info = h("span", {}, "Will be saved in History");
  const close = h("button", { type: "button", class: "btn" }, "Close, keep going");
  const go = h("button", { type: "button", class: "btn primary", disabled: true }, "Apply");
  let problems = null, finished = false;
  // Now, or Later at a time of the browser's clock (the next 03:00 to start with)
  const pad = (x) => String(x).padStart(2, "0");
  const local = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const first = new Date(); first.setHours(3, 0, 0, 0);
  if (first.getTime() < Date.now() + 120e3) first.setDate(first.getDate() + 1);
  let later = false;
  const dueInput = h("input", { type: "datetime-local", id: "apply-due", class: "input", style: "width: auto", value: local(first),
    min: local(new Date()), "aria-label": "Apply at" });
  const whenBox = h("div", { class: "field" });
  const drawWhen = () => {
    put(whenBox, h("span", { class: "label" }, "When"),
      h("div", { class: "row", style: "gap: 10px" },
        seg([["now", "Now"], ["later", "Later"]], later ? "later" : "now", (v) => { later = v === "later"; drawWhen(); }, { label: "When" }),
        later ? dueInput : null),
      later ? h("span", { class: "help" }, "Applied then even with this page closed: only these changes, on top of what the router has at that time. "
        + "If the same setting was changed there in between, it is not applied and you get an alert.") : null);
    go.textContent = later ? "Schedule" : "Apply";
  };
  drawWhen();

  const m = modal({
    title: `Apply ${plural(n, "change")} to ${rt.name}`,
    sub: stale ? "podkop's settings changed on the router after this draft was started. Applying it would undo that, so the router will refuse it."
      : `Compared with what the router runs now${running ? ` (#${running.id})` : ""}. Nobody changed it on the router in the meantime.`,
    body: [changesBlock(d.diff, config), checks,
      h("div", { class: "field" }, h("label", { for: "apply-note" }, "Note for history"), noteInput),
      whenBox, steps, result,
      h("div", { class: "assure" }, icon("shield", 18, { stroke: "var(--info)", width: 1.6 }),
        h("p", {}, `If podkop does not come up, the router puts ${running ? `#${running.id}` : "the current settings"} back by itself within about 30 s. This works even if you close this page or the manager goes offline.`))],
    foot: [info, h("div", { class: "row" }, close, go)],
  });
  close.onclick = () => m.close();

  const showChecks = () => {
    if (problems === null) return;
    if (problems.length) {
      put(checks, h("span", {}, "podkop would not start with these settings"),
        problems.map((p) => h("span", { class: "bad-text" }, problemText(p))));
      checks.className = "checks bad";
    } else {
      const links = linkCount(config);
      put(checks, h("span", {}, "Checked on the router, podkop accepts these settings"),
        links ? h("span", {}, icon("check", 14, { stroke: "var(--ok)", width: 2 }), `${plural(links, "link")} read by podkop's parser`) : null,
        h("span", {}, icon("check", 14, { stroke: "var(--ok)", width: 2 }), "Required fields set"),
        h("span", {}, icon("check", 14, { stroke: "var(--ok)", width: 2 }), "Lists and domains valid"));
      checks.className = "checks";
    }
    go.disabled = stale || problems.length > 0 || finished;
  };
  api("POST", `/api/routers/${id}/validate`, { config }).then((r) => { problems = r.problems; showChecks(); })
    .catch((e) => { put(checks, h("span", {}, `Could not check: ${e.message}`)); checks.className = "checks bad"; go.disabled = stale; });

  const scheduleIt = async () => {
    const due = new Date(dueInput.value).getTime() / 1000;
    if (!due || due < Date.now() / 1000 + 60) {
      put(result, h("div", { class: "result bad" }, "Pick a time at least a minute from now")); return;
    }
    go.disabled = true; noteInput.disabled = true;
    try {
      const r = await api("POST", `/api/routers/${id}/schedule`, { base, config, note: noteInput.value.trim() || diffSummary(d.diff), due, trigger });
      finished = true;
      put(whenBox);
      put(result, h("div", { class: "result ok" }, `Scheduled for ${longWhen(due)} as #${r.version}. You can apply it now, put it back into the draft or cancel it in History.`));
      put(info, h("span", {}, `Saved as #${r.version} in `, h("a", { href: `#/r/${id}/history`, onclick: () => m.close() }, "History")));
      go.textContent = "Done"; go.disabled = false; go.onclick = () => m.close(); close.textContent = "Close";
      done && done(true);
    } catch (e) {
      put(result, h("div", { class: "result bad" }, e.message)); go.disabled = false; noteInput.disabled = false;
    }
  };

  go.onclick = async () => {
    if (later) return scheduleIt();
    put(whenBox);
    go.disabled = true; go.textContent = "Applying"; noteInput.disabled = true;
    steps.hidden = false;
    const marks = { check: Date.now() };
    let state = "pending", timer, vid;
    const draw = () => put(steps, jobSteps(state, marks, Date.now()).map(stepRow));
    const onJob = (job) => {
      if (finished || !job || !job.state || job.state === state) return;
      state = job.state;
      const t = Date.now();
      if (state === "reloading") marks.reload = marks.reload || t;
      if (state === "restarting") marks.restart = marks.restart || t;
      if (state === "reverting") { marks.restart = marks.restart || marks.check; marks.revert = t; }
      if (FINAL_STATES.includes(state)) end(job, t);
      draw();
    };
    const end = (job, t) => {
      finished = true; marks.end = t; clearInterval(timer); S.jobWatch.delete(vid);
      const ok = ["applied", "saved", "unchanged"].includes(job.state);
      if (job.problems && job.problems.length) { problems = job.problems; showChecks(); }
      put(result, h("div", { class: `result ${ok ? "ok" : "bad"}` }, ok ? (job.state === "applied" ? (job.hot ? `Applied without restarting podkop: ${(job.message.match(/\((.*)\)/) || [])[1] || "only sing-box's config changed"}.` : "Applied, podkop is running.") : job.message)
        : job.state === "reverted" ? `podkop did not start with these settings, so the router put the old ones back: ${job.reason || job.message}` : job.message));
      go.textContent = ok ? "Done" : "Applying failed";
      close.textContent = "Close";
      go.disabled = !ok;
      go.onclick = () => m.close();
      done && done(ok);
    };
    draw();
    timer = setInterval(draw, 1000);
    try {
      const r = await api("POST", `/api/routers/${id}/apply`, { base, config, note: noteInput.value.trim() || diffSummary(d.diff), trigger });
      vid = r.version;
      put(info, h("span", {}, `Saved as version #${vid} in `, h("a", { href: `#/r/${id}/history`, onclick: () => m.close() }, "History")));
      S.jobWatch.set(vid, onJob);
      const poll = async () => {
        if (finished) return;
        const v = await api("GET", `/api/versions/${vid}`).catch(() => null);
        if (v && !["pending", "running"].includes(v.state)) onJob(Object.assign({}, v.details || {}, { state: v.state, message: v.message }));
        else setTimeout(poll, 3000);
      };
      setTimeout(poll, 3000);
    } catch (e) {
      clearInterval(timer); finished = true;
      put(result, h("div", { class: "result bad" }, e.message));
      go.textContent = "Apply"; close.textContent = "Close";
    }
  };
}
