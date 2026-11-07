// agent.js: part of podkop-manager's web interface (see app.js)

import { S } from "./state.js";
import { api, h, modal, toast } from "./ui.js";

export async function updateAgent(id, done = async () => {}) {
  let command = "";
  try { ({ command } = await api("GET", `/api/routers/${id}/agent-upgrade`)); }
  catch (e) { return toast(e.message, true); }
  const out = h("p", { class: "small muted", role: "status" });
  const code = h("textarea", { class: "input mono", rows: 5, readonly: true,
    "aria-label": "Router agent upgrade command", value: command, onclick: e => e.target.select() });
  const manual = h("details", {}, h("summary", {}, "Or run a command on the router"),
    h("div", { class: "stack" },
      h("p", { class: "small muted" }, "As root in the OpenWrt router's SSH terminal; needed once for an agent older than 0.10."),
      code, h("button", { type: "button", class: "btn", onclick: async () => {
        try { await navigator.clipboard.writeText(command); toast("Command copied"); }
        catch { code.select(); toast("Select the command and copy it", true); }
      } }, "Copy command")));
  const run = h("button", { type: "button", class: "btn primary", onclick: async () => {
    run.disabled = true; out.textContent = "Sending the files to the router…";
    try {
      await api("POST", `/api/routers/${id}/agent-upgrade`);
      out.textContent = "Installing on the router…";
      S.upgradeWatch.set(id, async (j) => {
        if (j.state === "done") {
          S.upgradeWatch.delete(id); m.close(); await done();
          toast(`Router agent ${j.version || ""} updated`.replace("  ", " "));
        } else if (j.state === "failed") {
          S.upgradeWatch.delete(id); out.textContent = j.message || "The update failed"; run.disabled = false;
        } else out.textContent = j.state === "running" ? "Installing on the router…" : "Waiting for the router…";
      });
    } catch (e) {
      run.disabled = false; out.textContent = e.message;
      if (e.info && e.info.code === "manual") manual.open = true;
    }
  } }, "Update from this manager");
  const m = modal({ title: "Update router agent", narrow: true,
    sub: "Updates the agent, probe and connection health helper. SSH keys, firewall rules and Podkop settings are preserved.",
    onclose: () => S.upgradeWatch.delete(id),
    body: h("div", { class: "stack" },
      h("p", {}, "The manager sends its own copies of the files to the router over SSH and the agent installs them; podkop keeps running."),
      out, manual),
    foot: [h("span"), run] });
}
