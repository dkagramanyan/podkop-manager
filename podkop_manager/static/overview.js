// overview.js: part of podkop-manager's web interface (see app.js)

import { openApply } from "./apply.js";
import { EVENT_DOT, configDiff, destChip, diffSummary, eventText, ruleExit, ruleMatches, ruleNow, rulesOf, runningVersion } from "./model.js";
import { pageHead, routerState } from "./shell.js";
import { S } from "./state.js";
import { agentTime, api, duration, h, hhmm, icon, longWhen, plural, put, shortWhen, toast } from "./ui.js";
import { countriesText, gb } from "./usage.js";

// --- overview -------------------------------------------------------------------------------------

export async function viewOverview(main, id) {
  const page = h("div", { class: "page" });
  put(main, page);
  let data = {};
  const load = async () => {
    const [config, events, probe, versions, draft] = await Promise.allSettled([
      api("GET", `/api/routers/${id}/config`), api("GET", `/api/routers/${id}/events?limit=12`),
      api("GET", `/api/routers/${id}/probe`), api("GET", `/api/routers/${id}/versions?limit=50`),
      api("GET", `/api/routers/${id}/draft`)]);
    const v = (r) => (r.status === "fulfilled" ? r.value : null);
    data = { config: v(config), events: v(events) || [], probe: v(probe), versions: v(versions) || [], draft: v(draft) };
    data.auto = Object.fromEntries(((data.probe && data.probe.auto_rules) || []).map((r) => [r.section, r]));
  };

  const service = (action, label, cls) => h("button", { type: "button", class: `btn ${cls || ""}`, onclick: async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { await api("POST", `/api/routers/${id}/service`, { action }); toast(`podkop: ${action} done`); }
    catch (err) { toast(err.message, true); }
    b.disabled = false;
  } }, label);

  const draw = () => {
    const rt = S.routers[id];
    if (!rt) return;
    const st = rt.status, pk = st && st.podkop;
    const cfg = data.config && data.config.config;
    const running = runningVersion(rt, data.versions);
    put(page,
      pageHead("Overview", null,
        h("button", { type: "button", class: "btn", onclick: async (e) => {
          const b = e.currentTarget; b.disabled = true;
          try { await api("POST", `/api/routers/${id}/refresh`); await load(); draw(); } catch (err) { toast(err.message, true); }
          b.disabled = false;
        } }, icon("refresh", 15), "Refresh"),
        pk && pk.running ? service("restart", "Restart podkop") : pk ? service("start", "Start podkop", "primary") : null,
        pk && pk.running ? service("stop", "Stop", "danger") : null),
      !rt.online ? h("div", { class: "banner bad" }, h("span", { class: "dot bad" }),
        h("span", { class: "grow" }, h("b", {}, "The router does not answer. "), rt.error || ""),
        h("span", { class: "muted small" }, rt.down_since ? `since ${shortWhen(rt.down_since)}` : "")) : null,
      waiting(rt, cfg, running),
      st ? statusStrip(st) : null,
      h("div", { class: "split" },
        h("div", { class: "stack", style: "flex: 2 1 600px; min-width: 0" },
          nowRouting(rt, cfg),
          nightly(rt)),
        eventsCard(rt)));
  };

  const applyProbe = async () => {
    try {
      const p = await api("POST", `/api/routers/${id}/probe/preview`);
      openApply(id, { base: p.fingerprint, config: p.config, note: "Probe result", trigger: "probe",
        done: async () => { await load(); draw(); } });
    } catch (e) { toast(e.message, true); }
  };

  // things that wait for the user: an unapplied draft, a probe result not in podkop yet
  const waiting = (rt, cfg, running) => {
    const notes = [];
    if (data.draft && cfg && data.draft.changes) {
      const d = configDiff(cfg, data.draft.config);
      notes.push(h("div", { class: "note" }, h("span", { class: "dot accent" }),
        h("div", { class: "col" }, h("span", {}, `${plural(d.length, "routing change")} ${d.length === 1 ? "is" : "are"} not applied`),
          h("span", {}, `${diffSummary(d)}.${running ? ` The router still runs #${running.id}.` : ""}`)),
        h("a", { class: "alink", href: `#/r/${id}/rules` }, "Open changes")));
    }
    const ps = data.probe && data.probe.status;
    if (ps && ps.state === "done" && probeIsNew(data.probe)) {
      const rows = ps.rows || [];
      const usable = rows.filter((r) => ["GOOD", "OK", "FLAKY"].includes(r.verdict)).length;
      const rules = data.probe.auto_rules.filter((r) => r.differs).map((r) => `${r.section} ${r.picked} and ${r.picked_fallback} fallback`);
      notes.push(h("div", { class: "note" }, h("span", { class: "dot accent" }),
        h("div", { class: "col" }, h("span", {}, `Probe from ${shortWhen(agentTime(ps.finished))} has new servers`),
          h("span", {}, `${rules.join(", ")}. ${usable} of ${rows.length} servers usable.`)),
        h("button", { type: "button", class: "btn sm", onclick: applyProbe }, "Review and apply")));
    }
    const st = rt.status;
    if (st && st.engine === false) {
      notes.push(h("div", { class: "note" }, h("span", { class: "dot warn" }),
        h("div", { class: "col" }, h("span", {}, "podkop-engine is not installed"),
          h("span", {}, "The probe can't test servers on this router. Reading and applying settings work.")),
        h("a", { class: "alink", href: "https://github.com/FiyeroT/podkop-engine", target: "_blank", rel: "noopener" }, "podkop-engine")));
    }
    if (st && st.probe && st.probe.cron) {
      const [mm, hh] = st.probe.cron.split(/\s+/);
      const at = /^\d+$/.test(mm) && /^\d+$/.test(hh) ? ` at ${hh.padStart(2, "0")}:${mm.padStart(2, "0")}` : "";
      notes.push(h("div", { class: "note" }, h("span", { class: "dot warn" }),
        h("div", { class: "col" }, h("span", {}, `podkop-probe also runs by itself every night${at}`),
          h("span", {}, "Set up on the router before the manager, it changes the servers without it. The manager can run it instead, at the same time.")),
        h("button", { type: "button", class: "btn sm", onclick: async (e) => {
          e.currentTarget.disabled = true;
          try { await api("POST", `/api/routers/${id}/probe/take-over`); toast("The manager runs the nightly probe now"); await load(); draw(); }
          catch (err) { toast(err.message, true); e.currentTarget.disabled = false; }
        } }, "Let the manager run it")));
    }
    for (const a of (rt.usage && rt.usage.alerts) || []) {
      const eta = a.forecast && a.forecast.eta;
      notes.push(h("div", { class: "note" }, h("span", { class: "dot warn" }),
        h("div", { class: "col" }, h("span", {}, `${a.label} has used ${Math.round(a.pct)}% of today's traffic`),
          h("span", {}, `${gb(a.used)} of ${gb(a.limit)}.${eta ? ` At this rate it runs out around ${hhmm(new Date(eta * 1000))}.` : ""}${
            a.action === "avoid" ? ` Rules move to other keys at ${a.threshold}%.` : ""}`)),
        h("a", { class: "alink", href: `#/r/${id}/usage` }, "See usage")));
    }
    return notes.length ? h("section", { class: "card clip waiting", "aria-label": "Waiting for you" }, notes) : null;
  };

  const statusStrip = (st) => {
    const s = routerState(S.routers[id]);
    const pk = st.podkop;
    const days = Math.floor(st.uptime / 86400), hours = Math.floor((st.uptime % 86400) / 3600);
    return h("section", { class: "card status-strip", "aria-label": "Status" },
      h("div", {}, h("span", {}, "podkop"), h("span", {}, h("span", { class: `dot sm ${s.cls}` }), `${pk.version || "?"}, ${s.label}`)),
      h("div", {}, h("span", {}, "sing-box"), h("span", {}, st.singbox.version || "not found")),
      h("div", {}, h("span", {}, "podkop-agent"),
        h("span", {}, `${st.agent.version}, ${pk.paused || !pk.enabled ? "watchdog paused" : "watchdog on"}`)),
      h("div", {}, h("span", {}, "Router"),
        h("span", {}, [st.router.model, (st.router.openwrt || "").replace(/ r\d+-\w+$/, ""), `up ${days ? days + " d" : hours + " h"}`].filter(Boolean).join(", "))),
      st.failsafe && !pk.running ? h("div", { style: "grid-column: 1 / -1" }, h("span", {}, `Failsafe, ${st.failsafe.time}`),
        h("span", { class: "bad-text" }, st.failsafe.message)) : null);
  };

  const unpin = async (sec) => {
    try { await api("POST", `/api/routers/${id}/pin`, { section: sec, server: "auto" }); toast(`${sec}: back to automatic`); }
    catch (e) { toast(e.message, true); }
  };

  const nowRouting = (rt, cfg) => {
    const rules = rulesOf(cfg);
    return h("section", { class: "card clip", "aria-labelledby": "now-routing" },
      h("div", { class: "card-head", style: "align-items: baseline" },
        h("div", { class: "col" }, h("h2", { id: "now-routing" }, "Now routing"),
          h("span", { class: "small muted" }, "First match wins")),
        h("a", { class: "alink", href: `#/r/${id}/rules` }, "Edit rules")),
      !cfg ? h("p", { class: "empty" }, rt.online ? "Could not read podkop's settings." : "Shown when the router answers.")
        : h("div", { class: "scroll-x" }, h("table", { class: "wide" },
          h("tr", {}, h("th", { scope: "col", style: "width: 24px" }, "#"), h("th", { scope: "col" }, "Rule"),
            h("th", { scope: "col" }, "Matches"), h("th", { scope: "col" }, "Goes to"), h("th", { scope: "col" }, "Now")),
          rules.map((sec, i) => {
            const now = ruleNow(rt, sec, data.config.devices);
            return h("tr", {},
              h("td", { class: "muted num" }, i + 1),
              h("td", { class: "mono", style: "font-size: 13px; font-weight: 500" }, sec.name),
              h("td", { style: "color: var(--text-2)" }, ruleMatches(sec)),
              h("td", {}, data.auto[sec.name] && (sec.options || {}).connection_type === "proxy"
                ? h("span", { class: "dest" }, `Proxy, from probe${data.auto[sec.name].countries.length ? ", " + countriesText(data.auto[sec.name].countries) : ""}`) : destChip(sec)),
              h("td", {}, h("div", { class: "col" },
                h("span", { class: "row", style: "gap: 8px" }, h("span", { class: now.bad ? "bad-text" : "" }, now.text),
                  now.mode ? h("span", { class: "mode" }, now.mode) : null,
                  now.mode === "pinned" ? h("button", { type: "button", class: "link-btn xs", onclick: () => unpin(sec.name) }, "Unpin") : null),
                ruleExit(rt, sec))));
          }),
          h("tr", {}, h("td", {}), h("td", { class: "nowrap", style: "color: var(--text-2)" }, "Everything else"),
            h("td", { class: "muted" }, "no rule matched"), h("td", {}, h("span", { class: "dest direct" }, "Direct")),
            h("td", { class: "muted" }, "not through podkop")))));
  };

  const nightly = () => {
    const p = data.probe;
    const ps = p && p.settings;
    if (!ps) return null;
    const last = (p.runs || []).find((r) => r.trigger === "schedule");
    let lastText = "";
    if (last) {
      const day = longWhen(last.started).replace(/ \d\d:\d\d$/, "").replace(/, \d\d:\d\d$/, "");
      lastText = last.state !== "done" ? `Last nightly run ${day.toLowerCase()}: ${last.state}.`
        : last.version_id ? `Last nightly run ${day.toLowerCase()} applied as #${last.version_id}.`
          : `Last nightly run ${day.toLowerCase()} finished, not applied.`;
    }
    let next = "";
    if (ps.schedule) {
      const [hh, mm] = ps.schedule.split(":").map(Number);
      const t = new Date(); t.setHours(hh, mm, 0, 0);
      if (t <= new Date()) t.setDate(t.getDate() + 1);
      next = `Next run in ${duration((t - new Date()) / 1000)}.`;
    }
    return h("section", { class: "card note", "aria-labelledby": "nightly", style: "padding: 16px 18px" },
      h("div", { class: "col", style: "flex: 1 1 320px; gap: 3px" },
        h("h2", { id: "nightly", class: "h2" }, "Nightly probe"),
        h("span", { style: "font-size: 13.5px; color: var(--text-2); font-weight: 400" }, ps.schedule
          ? `Every night at ${ps.schedule}, ${ps.auto_apply ? "applied by itself if podkop starts with it" : "results wait for you to apply them"}.`
          : "Off"),
        lastText || next ? h("span", { class: "small muted" }, [lastText, next].filter(Boolean).join(" ")) : null),
      h("a", { class: "btn", href: `#/r/${id}/rules${(p.auto_rules || []).length ? `/${p.auto_rules[0].section}` : ""}` }, "Schedule"));
  };

  const eventsCard = () => h("aside", { class: "card events", "aria-label": "Events", style: "flex: 1 1 300px; min-width: 0" },
    h("div", { class: "row", style: "justify-content: space-between; margin-bottom: 8px" },
      h("h2", {}, "Events"), h("a", { class: "small", href: `#/r/${id}/history` }, "History")),
    data.events.length ? data.events.map((e) => h("div", { class: "event" },
      h("span", { class: "num" }, shortWhen(e.t)), h("span", { class: `dot ${EVENT_DOT[e.type] || "gray"}` }),
      h("span", {}, eventText(e.type, e.data))))
      : h("p", { class: "muted small" }, "Nothing yet."));

  await load();
  draw();
  let timer;
  const later = () => { clearTimeout(timer); timer = setTimeout(async () => { await load(); draw(); }, 800); };
  S.view = {
    leave: () => clearTimeout(timer),
    onRouter: (r) => {
      if (r.id !== id) return;
      if (r.status && (data.config ? r.status.fingerprint !== data.config.fingerprint : r.online)) later(); else draw();
    },
    onEvent: (m) => m.router_id === id && later(),
    onProbe: (m) => m.router_id === id && m.state !== "running" && later(),
  };
}

// the last probe run picked other servers than some rule has now
export const probeIsNew = (probe) => (probe.auto_rules || []).some((r) => r.differs);
