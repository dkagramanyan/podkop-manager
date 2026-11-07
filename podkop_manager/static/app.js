// podkop-manager web interface: hash routes (#/r/<router>/<section>), plain
// DOM, live updates over server-sent events. ES modules, no build step;
// app.js is the entry.

import { eventText } from "./model.js";
import { drawSide, render, switchOpen } from "./shell.js";
import { S } from "./state.js";
import { api, toast } from "./ui.js";

// --- start --------------------------------------------------------------------------------

export async function loadRouters() {
  S.routers = {};
  for (const r of await api("GET", "/api/routers")) S.routers[r.id] = r;
}

export function connect() {
  const es = new EventSource("/api/stream");
  const live = (on) => {
    if (S.live === on) return;
    S.live = on; drawSide();
    S.view && S.view.onLive && S.view.onLive();
  };
  es.onopen = () => live(true);
  es.onerror = () => {
    live(false);
    // a 401 or a proxy error closes it for good: check the login, then connect again
    if (es.readyState === EventSource.CLOSED) {
      setTimeout(() => api("GET", "/api/manager").then(connect).catch(() => setTimeout(connect, 10000)), 3000);
    }
  };
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    const v = S.view || {};
    if (m.type === "router") {
      S.routers[m.router.id] = m.router;
      if (!switchOpen) drawSide();
      v.onRouter && v.onRouter(m.router);
    } else if (m.type === "event") {
      v.onEvent && v.onEvent(m);
      if (["reverted", "revert_failed", "apply_interrupted", "podkop_down", "recovery_failed", "group_down", "schedule_failed"].includes(m.event)) {
        toast(`${(S.routers[m.router_id] || {}).name}: ${eventText(m.event, m.data)}`, true);
      }
    } else if (m.type === "log") v.onLog && v.onLog(m);
    else if (m.type === "probe") v.onProbe && v.onProbe(m);
    else if (m.type === "usage") v.onUsage && v.onUsage(m);
    else if (m.type === "reliability") v.onReliability && v.onReliability(m);
    else if (m.type === "upgrade") {
      const fn = S.upgradeWatch.get(m.router_id);
      if (fn) fn(m.job);
    } else if (m.type === "job") {
      const fn = S.jobWatch.get(m.version);
      if (fn) fn(m.job);
      v.onJob && v.onJob(m);
    }
  };
}

(async () => {
  [S.schema] = await Promise.all([api("GET", "/api/schema"), loadRouters()]);
  connect();
  window.addEventListener("hashchange", render);
  render();
})();
