// settings.js: part of podkop-manager's web interface (see app.js)

import { updateAgent } from "./agent.js";
import { loadRouters } from "./app.js";
import { drawSide, pageHead, routerList, routerState } from "./shell.js";
import { S } from "./state.js";
import { ago, api, h, longWhen, modal, put, quietDraw, shortWhen, steadyRouter, toast } from "./ui.js";
import { busyClick } from "./usage.js";

// --- settings (the manager's) -------------------------------------------------------------------------------

export const splitIps = (v) => v.split(/[\s,]+/).filter(Boolean).join(" ");

export async function viewSettings(main) {
  const info = S.manager || (S.manager = await api("GET", "/api/manager"));
  // the command that installs podkop-agent and this manager's key on a router, from the options
  const commandPart = (prefix) => {
    const close = h("input", { type: "checkbox", checked: true });
    const allow = h("input", { class: "input mono", id: `${prefix}-clash`, placeholder: "e.g. 192.168.1.10, optional" });
    const forward = h("input", { type: "checkbox" });
    const code = h("code", { class: "grow", style: "font-size: 12px; line-height: 1.5; color: var(--text-2); word-break: break-all" }, "…");
    let cmd = "", timer = null;
    const update = async () => {
      try {
        cmd = (await api("POST", "/api/install-command", { close_clash: close.checked, allow_clash: splitIps(allow.value), forward: forward.checked })).command;
        put(code, cmd);
      } catch (e) { cmd = ""; put(code, e.message); }
    };
    close.onchange = forward.onchange = update;
    allow.oninput = () => { clearTimeout(timer); timer = setTimeout(update, 400); };
    update();
    return [
      h("label", { class: "check" }, close, h("span", { class: "col" }, h("span", {}, "Close podkop's Clash API (port 9090) to the LAN"),
        h("span", {}, "Anyone on the LAN can switch its servers otherwise. Off: the firewall is not changed."))),
      h("div", { class: "field", style: "max-width: 560px" }, h("label", { for: `${prefix}-clash` }, "Computers that may still reach the Clash API"), allow,
        h("span", { class: "help" }, "Only when it is closed")),
      info.local_probe ? h("label", { class: "check" }, forward, h("span", { class: "col" },
        h("span", {}, "Let this manager test servers through the router"),
        h("span", {}, "Adds a second key that may only open connections through the router: no shell, no commands"))) : null,
      h("span", { style: "font-size: 13px; font-weight: 500" }, "Run this on the router as root, over SSH"),
      h("div", { class: "row", style: "align-items: flex-start; flex-wrap: nowrap; padding: 10px 12px; border-radius: 6px; background: var(--chip)" },
        code, h("button", { type: "button", class: "btn xs", onclick: async () => {
          if (!cmd) return;
          try { await navigator.clipboard.writeText(cmd); toast("Command copied"); } catch { toast("Select the command and copy it", true); }
        } }, "Copy")),
    ];
  };
  const page = h("div", { class: "page" });
  put(main, page);

  const rename = (rt) => {
    const input = h("input", { class: "input", id: "rn", value: rt.name });
    const save = async () => {
      try { S.routers[rt.id] = await api("PATCH", `/api/routers/${rt.id}`, { name: input.value.trim() }); m.close(); draw(); drawSide(); }
      catch (e) { toast(e.message, true); }
    };
    input.onkeydown = (e) => { if (e.key === "Enter") save(); };
    const m = modal({ title: `Rename ${rt.name}`, narrow: true, body: h("div", { class: "field" }, h("label", { for: "rn" }, "Name"), input),
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: save }, "Rename"))] });
    input.select();
  };

  const reinstall = (rt) => {
    const m = modal({ title: `Reinstall podkop-agent on ${rt.name}`, narrow: true,
      sub: "Puts this manager's podkop-agent, podkop-probe and key on the router again. podkop's settings stay as they are.",
      body: [commandPart("ri")],
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: busyClick(async () => {
          try { await api("POST", `/api/routers/${rt.id}/refresh`); m.close(); toast(`${rt.name} checked`); } catch (e) { toast(e.message, true); }
        }) }, "Done, check the router"))] });
  };

  const remove = (rt) => {
    const also = h("input", { type: "checkbox", checked: true });
    const go = h("button", { type: "button", class: "btn danger" }, "Remove");
    go.onclick = async () => {
      go.disabled = true;
      try {
        const r = await api("DELETE", `/api/routers/${rt.id}?uninstall=${also.checked ? 1 : 0}`);
        delete S.routers[rt.id];
        m.close(); toast(r.note || `${rt.name} removed`, !!r.note);
        draw(); drawSide();
      } catch (e) { toast(e.message, true); go.disabled = false; }
    };
    const m = modal({ title: `Remove ${rt.name}?`, narrow: true,
      body: [h("label", { class: "check" }, also, h("span", { class: "col" }, h("span", {}, "Also remove podkop-agent from the router"),
        h("span", {}, "Its key, firewall rules and watchdog go too. podkop and its settings stay."))),
      h("p", { class: "small muted" }, "History, logs and probe settings of this router are deleted from the manager.")],
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"), go)] });
  };

  const f = {
    name: h("input", { class: "input", id: "a-name", placeholder: "e.g. country-house" }),
    pairing: h("input", { class: "input mono", id: "a-pair", placeholder: "podkop-router 192.168.1.1 22 ssh-ed25519 AAAA…", autocomplete: "off",
      oninput: () => {
        const p = f.pairing.value.trim().split(/\s+/);
        if (p[0] === "podkop-router" && p.length === 5) { f.host.placeholder = p[1]; f.port.placeholder = p[2]; }
      } }),
    host: h("input", { class: "input mono", id: "a-host", placeholder: "from the line" }),
    port: h("input", { class: "input", id: "a-port", placeholder: "from the line", inputmode: "numeric" }),
  };
  const addOut = h("div");
  const addBtn = h("button", { type: "submit", class: "btn primary lg" }, "Add");
  const addForm = h("form", { class: "stack", "aria-labelledby": "s-add", onsubmit: async (e) => {
    e.preventDefault();
    if (!f.pairing.value.trim()) { f.pairing.focus(); return; }
    addBtn.disabled = true; put(addOut, h("p", { class: "small muted" }, "Checking the router…"));
    try {
      const r = await api("POST", "/api/routers", { name: f.name.value.trim(), pairing: f.pairing.value.trim(),
        host: f.host.value.trim(), port: +f.port.value || null });
      await loadRouters();
      location.hash = `#/r/${r.id}`;
    } catch (err) { put(addOut, h("p", { class: "problem-box" }, err.message)); addBtn.disabled = false; }
  } },
  h("span", { class: "small muted" }, "podkop stays as it is: its settings are kept in Configs. The probe needs podkop-engine."),
  commandPart("a"),
  h("div", { class: "field" }, h("label", { for: "a-pair" }, "The line the command printed at the end"), f.pairing),
  h("div", { class: "auto-grid", style: "grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr))" },
    h("div", { class: "field" }, h("label", { for: "a-name" }, "Name"), f.name),
    h("div", { class: "field" }, h("label", { for: "a-host" }, "Address"), f.host, h("span", { class: "help" }, "If this server reaches the router at another address")),
    h("div", { class: "field" }, h("label", { for: "a-port" }, "SSH port"), f.port)),
  addOut, h("div", {}, addBtn));

  const restore = () => {
    const file = h("input", { type: "file", id: "bak-file", class: "input", accept: ".gz,.tgz,application/gzip" });
    const out = h("div");
    const go = h("button", { type: "button", class: "btn danger" }, "Restore");
    go.onclick = async () => {
      const f = file.files[0];
      if (!f) { file.focus(); return; }
      go.disabled = true; put(out, h("p", { class: "small muted" }, "Restoring…"));
      try {
        const r = await fetch("/api/backup", { method: "POST", headers: { "X-Podkop-Manager": "1", "Content-Type": "application/gzip" }, body: f });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || r.statusText);
        location.hash = "#/"; location.reload();
      } catch (e) { put(out, h("p", { class: "problem-box" }, e.message)); go.disabled = false; }
    };
    const m = modal({ title: "Restore a backup?", narrow: true,
      body: [h("p", { class: "small" }, "Everything in this manager is replaced by the backup: routers, keys, rules, configs, history and the SSH key."),
        h("div", { class: "field" }, h("label", { for: "bak-file" }, "Backup file"), file), out],
      foot: [h("span"), h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel"), go)] });
  };

  const draw = () => {
    const list = routerList();
    put(page,
      pageHead("Settings"),
      h("section", { class: "card clip", "aria-labelledby": "s-routers" },
        h("div", { class: "card-head" }, h("div", { class: "col" }, h("h2", { id: "s-routers" }, "Routers"),
          list.length ? null : h("span", { class: "small muted" }, "None yet: add your first router below"))),
        list.length ? h("div", { class: "scroll-x" }, h("table", { class: "wide" },
          h("tr", {}, h("th", { scope: "col" }, "Name"), h("th", { scope: "col" }, "Address"), h("th", { scope: "col" }, "State"),
            h("th", { scope: "col" }, "podkop-agent"), h("th", { scope: "col", class: "r" }, h("span", { class: "sr" }, "Actions"))),
          list.map((rt) => {
            const st = routerState(rt);
            const label = !rt.online && rt.down_since ? `offline since ${shortWhen(rt.down_since)}` : st.label;
            return h("tr", {},
              h("td", { style: "font-weight: 500" }, h("a", { href: `#/r/${rt.id}`, style: "color: inherit" }, rt.name)),
              h("td", { class: "mono-cell", style: "color: var(--text-2)" }, `${rt.host}:${rt.port}`),
              h("td", {}, h("span", { class: `pill ${st.cls}` }, label)),
              h("td", { style: "color: var(--text-2)" }, rt.status ? rt.status.agent.version : "–"),
              h("td", { class: "r nowrap" }, h("span", { class: "row", style: "gap: 6px; justify-content: flex-end; flex-wrap: nowrap" },
                h("button", { type: "button", class: "btn xs", onclick: () => rename(rt) }, "Rename"),
                h("button", { type: "button", class: "btn xs", onclick: () => updateAgent(rt.id, async () => { await loadRouters(); draw(); }) }, "Update agent"),
                h("button", { type: "button", class: "btn xs", onclick: () => reinstall(rt) }, "Reinstall agent"),
                h("button", { type: "button", class: "btn xs danger", onclick: () => remove(rt) }, "Remove"))));
          }))) : null),
      // folded once there is a router (open when asked for from the router switcher)
      h("details", { class: "card pad add-router", open: !list.length || S.addRouter, ontoggle: (e) => { S.addRouter = e.target.open; } },
        h("summary", {}, h("h2", { id: "s-add" }, "Add router")), addForm),
      h("div", { style: "display: grid; grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr)); gap: 16px; align-items: start" },
        h("section", { class: "card pad", "aria-labelledby": "s-mgr", style: "gap: 12px" },
          h("div", { class: "col" }, h("h2", { id: "s-mgr" }, "This manager"), h("span", { class: "small muted" }, `podkop-manager ${info.release || ""} (manager ${info.version})`.replace("  ", " "))),
          h("span", { style: "font-size: 13px; font-weight: 500" }, "SSH key the routers accept"),
          h("div", { class: "row", style: "align-items: flex-start; flex-wrap: nowrap; padding: 10px 12px; border-radius: 6px; background: var(--chip)" },
            h("code", { class: "grow", style: "font-size: 12px; line-height: 1.5; color: var(--text-2); word-break: break-all" }, info.public_key),
            h("button", { type: "button", class: "btn xs", onclick: async () => {
              try { await navigator.clipboard.writeText(info.public_key); toast("Key copied"); } catch { toast("Select the key and copy it", true); }
            } }, "Copy"))),
        h("section", { class: "card pad", "aria-labelledby": "s-acc", style: "gap: 12px" },
          h("h2", { id: "s-acc" }, "Login"),
          h("span", { class: "small muted" }, "Login and password are set in .env (PODKOP_MANAGER_USER, PODKOP_MANAGER_PASSWORD)"),
          h("div", { class: "row" },
            h("form", { method: "post", action: "/logout", style: "display: contents" }, h("button", { type: "submit", class: "btn" }, "Log out")))),
        h("section", { class: "card pad", "aria-labelledby": "s-bak", style: "gap: 12px" },
          h("h2", { id: "s-bak" }, "Backup"),
          h("span", { class: "small muted" }, "Everything in one file: routers, keys, rules, configs, history and the SSH key. Keep it private."),
          h("div", { class: "row" },
            h("a", { class: "btn", href: "/api/backup", download: "" }, "Download backup"),
            h("button", { type: "button", class: "btn", onclick: restore }, "Restore…")),
          savedBackups()),
        notifyCard()));
  };

  // where alerts go; loaded once, edits live in notifyS until saved
  let notifyS = null, notifyEvents = [], eventsOpen = false;      // (the list stays open across redraws)
  // an event's name in the words the pages use: "strict_closed" -> "kill switch closed"
  const WORDS = { strict: "kill switch", heal: "self-heal", group: "rule", fds: "open files", vpn: "VPN", api: "API", clash: "sing-box", dns: "DNS" };
  const eventName = (e) => e.split("_").map((w) => WORDS[w] || w).join(" ");
  Promise.all([api("GET", "/api/notify"), api("GET", "/api/notify/events").catch(() => [])])
    .then(([n, ev]) => { notifyS = n; notifyEvents = ev; if (!page.querySelector(":focus")) draw(); }).catch(() => {});
  // the events a message is sent for, in groups, each one can be switched off
  const eventGroups = () => {
    const groups = [["Servers and rules", /^(group_|server_|strict_)/], ["Changes and the probe", /^(revert|apply_|schedule_|change_|settings_|probe_|heal_)/],
      ["Router, podkop and VPN", /./]];
    const out = groups.map(([name]) => [name, []]);
    for (const e of notifyEvents) out[groups.findIndex(([, re]) => re.test(e))][1].push(e);
    return out.filter(([, list]) => list.length);
  };
  const watchedList = () => {
    const rows = routerList().flatMap((rt) => (((rt.status || {}).watched) || []).map((x) => ({ rt, x })));
    if (!rows.length) return h("span", { class: "small muted" }, "No server is watched. On a rule's page, the bell beside a server watches it: a message when it stops answering for 3 minutes, and when it is back.");
    return h("div", { class: "stack", style: "gap: 4px" }, rows.map(({ rt, x }) => h("div", { class: "row small", style: "gap: 8px" },
      h("span", { class: `dot ${x.state === "down" ? "bad" : x.state === "up" ? "ok" : "gray"}` }),
      h("a", { href: `#/r/${rt.id}/rules/${x.section}` }, `${rt.name} · ${x.section}`), h("span", {}, x.name || x.md5.slice(0, 8)),
      h("span", { class: "muted" }, x.state === "down" ? `does not answer since ${ago(x.since)}` : x.state === "up" ? "answers"
        : x.state === "gone" ? "not in the rule any more" : "first test within a minute"))));
  };
  const notifyCard = () => {
    if (!notifyS) return null;
    const field = (key, label, type, hint) => h("label", { class: "col", style: "gap: 4px" },
      h("span", { style: "font-size: 13px; font-weight: 500" }, label),
      h("input", { class: "input", type, value: notifyS[key], autocomplete: "off", placeholder: hint, oninput: (e) => { notifyS[key] = e.target.value; } }));
    const save = async (then) => {
      try { notifyS = await api("PUT", "/api/notify", notifyS); if (then) await then(); else toast("Notifications saved"); }
      catch (err) { toast(err.message, true); }
    };
    return h("section", { class: "card pad", "aria-labelledby": "s-ntf", style: "gap: 12px" },
      h("h2", { id: "s-ntf" }, "Notifications"),
      h("span", { class: "small muted" }, "A message when a rule loses all its servers or gets them back, a watched server stops answering, a kill switch closes or opens, podkop goes down, a change is reverted or not applied, the nightly probe fails, or a router is out of reach for 5 minutes. A message that could not be sent is sent later, with its time."),
      field("telegram_token", "Telegram bot token", "password", "from @BotFather"),
      field("telegram_chat", "Telegram chat id", "text", "your id or a group's, e.g. 123456789"),
      field("webhook_url", "Webhook URL (JSON POST)", "url", "https://…"),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: notifyS.daily, onchange: (e) => { notifyS.daily = e.target.checked; } }),
        h("span", {}, "A daily summary at 9:00")),
      field("quiet", "Quiet hours", "text", "e.g. 23:00-08:00: Telegram messages come without a sound then"),
      notifyEvents.length ? h("details", { class: "small", open: eventsOpen, ontoggle: (ev) => { eventsOpen = ev.target.open; } },
        h("summary", {}, `Which events send a message: ${notifyEvents.length - (notifyS.off || []).length} of ${notifyEvents.length}`),
        eventGroups().map(([name, list]) => h("div", { class: "stack", style: "gap: 4px; margin-top: 10px" },
          h("b", {}, name),
          h("div", { class: "row", style: "gap: 4px 14px" }, list.map((e) => h("label", { class: "check inline" },
            h("input", { type: "checkbox", checked: !(notifyS.off || []).includes(e), onchange: (ev) => {
              const off = new Set(notifyS.off || []);
              if (ev.target.checked) off.delete(e); else off.add(e);
              notifyS.off = [...off];
            } }), h("span", { title: e }, eventName(e)))))))) : null,
      h("span", { style: "font-size: 13px; font-weight: 500" }, "Watched servers"),
      watchedList(),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn primary", onclick: () => save() }, "Save"),
        h("button", { type: "button", class: "btn", onclick: () => save(async () => { await api("POST", "/api/notify/test"); toast("Test notification sent"); }) }, "Save and send a test")));
  };

  // the daily copies in the data folder (filled in after the page shows)
  // (asked once: the page redraws at every status of a router)
  let backupsBox = null;
  const savedBackups = () => {
    if (backupsBox) return backupsBox;
    const box = backupsBox = h("div", { class: "col", style: "gap: 4px" });
    api("GET", "/api/backups").then(({ backups, keep }) => {
      put(box, h("span", { style: "font-size: 13px; font-weight: 500" }, `Saved on this server daily, the last ${keep} kept`),
        backups.length ? h("ul", { class: "plain small", style: "margin: 0; padding: 0; list-style: none" }, backups.slice(0, 5).map((b) =>
          h("li", { class: "row", style: "gap: 8px" },
            h("a", { href: `/api/backups/${b.name}`, download: "" }, longWhen(b.t)),
            h("span", { class: "muted" }, `${(b.size / 1024).toFixed(0)} kB`))))
          : h("span", { class: "small muted" }, "None yet: the first copy is made within an hour of the start"));
    }).catch((e) => put(box, h("span", { class: "small muted" }, e.message)));
    return box;
  };

  draw();
  const redraw = quietDraw(page, draw, () => Object.values(S.routers).map(steadyRouter).join());
  S.view = { onRouter: () => redraw() };
}
