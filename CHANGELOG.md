# Changelog

Versions are podkop-probe's (`probe.sh`); a release also carries
podkop-agent and podkop-manager.

## 1.43.0 — 2026-11-11

Kill switch and leak fixes (agent 0.18.0).

- **The guard stands at once after a reboot.** The block list was written
  only after every remote and community list had been downloaded, one after
  the other, so until then (or for good, with the lists' host unreachable)
  kill-switch sites went direct while podkop was down. It is now built at
  once from the rules' own domains and subnets and the lists already in RAM;
  lists are fetched in the background, a failed fetch is tried again after
  10 minutes, and the guard is built again when one arrives.
- **Kill switches are closed again right after a reload.** A sing-box reload
  (self-heal, an apply of servers) puts every selector back on its default,
  which opened a closed kill switch until the next self-heal round, up to
  30 s later.
- **A hung sing-box is noticed.** When sing-box runs but its API has not
  answered for 2 minutes, nothing could watch servers or close a kill
  switch, silently. Now: a `clash_api_silent` event and one podkop restart
  through the watchdog (its limit of 3 an hour holds).
- **The apply lock no longer leaks** into the background job that resolves
  kill-switch domains (minutes with many names or the WAN down), which
  stalled applies, self-heal and recovery for as long.
- One malformed address in a list (`300.1.1.1`, `/40`) made nft refuse the
  whole block list; addresses are validated now, and a refusal is logged
  and reported once, not every 15 s.
- The guard was rebuilt at every podkop start and stop (its key included the
  flag podkop writes then), delaying the DNS block by seconds; builds from
  the daemon, an apply and the kill-switch command no longer run at once.
- Events of the kill switch have readable texts in the manager.

## 1.42.0 — 2026-11-10

- **Kill switch works with self-heal off.** Until now the agent watched only
  the rules self-heal was on for, so a kill switch on a rule (or on one that
  sends through a rule) with self-heal off never closed. The kill-switch
  rules and the rules they send through are now tested and tracked even
  where self-heal is off: their switch closes and opens as before, nothing
  is borrowed or lent for them, and their outages are reported. Needs agent
  0.17.0; with an older agent the switch keeps asking for self-heal.

## 1.41.0 — 2026-11-10

- **Kill switch** is the new name of "Strict" (same switch on the rule).
- It is now also offered on rules that send their traffic through another
  rule (an outbound rule whose JSON is a selector of e.g. `main-out`, like
  meta, github or ameriabank): such a rule refuses connections at once while
  none of that rule's own servers answers, even when that rule borrowed
  servers from others, and opens again the moment one of them answers. Its
  domains and subnets are in the guard like the others' (agent 0.16).
- The guard also blocks the addresses the kill-switch rules' own domains
  resolve to (resolved on the router through outside resolvers, refreshed
  every hour, at most 400 names): a device with its own DNS that reaches
  such an address directly is stopped, as it is with podkop's lists.
  Community lists are not resolved (too many names; their subnets are in).
- Agent 0.16.0: `heal` status `guard.resolved`; `strict_closed` events of
  rules that follow another rule carry `via: true`.

## 1.40.0 — 2026-11-09

- **Self-heal fallback countries** (a rule's probe parameters → "If none
  works"): when nothing in the rule's countries answers, the self-heal probe
  may take a server in Europe or in any country; the nightly probe keeps to
  the rule's countries. Until now a rule like netflix (BG/LT/LV/PL/SK) stayed
  on borrowed servers of other rules for days.
- **Probe placement by memory**: a router with less than 60 MB free tests
  its servers on the manager through the router instead of starting a
  sing-box with every server on the router (out of memory there means a
  crash and the failsafe). Overview shows the router's free memory (agent
  0.15 reports it).
- **List downloads** that fail ("fetch rule-set … 504") show on Connection
  health as "List download failed", by list; a failed list stays stale and
  new domains of that service go direct until it loads.

## 1.39.0 — 2026-11-09

- **Rule edits without a podkop restart** (agent 0.14): a change that touches
  only servers, a rule's own domains or its URLTest settings (test URL,
  interval, tolerance) or an outbound JSON is applied like a server change:
  sing-box reloads in place in about a second instead of podkop restarting
  (20–30 s without proxy or firewall rules). The rule's domain list file is
  made anew in RAM. Subnets, community lists and list files still restart
  podkop (they feed its firewall sets). The result says what changed.
- Fixed: `exit-ips` left a temp folder in RAM at every call (2727 on reserv
  after two days); `job <id> <wait>` now waits through a reload too; the
  daemon's event watcher ends with the daemon.

## 1.38.0 — 2026-11-09

- **Strict guard** (agent 0.13): while podkop is down or restarting, the
  destinations of strict rules are blocked on the router instead of going
  direct: their IPv4 subnets (user, local, remote and community lists) in an
  nft table of ours that rejects forwarded traffic from the LAN, in place
  whenever strict rules exist (with podkop up these packets never reach it),
  and their domains (user, local, remote and community lists, decompiled
  like podkop reads them) as NXDOMAIN answers in a dnsmasq file that is in
  only while podkop is away: put in before every podkop restart and within a
  second of sing-box going away (the daemon follows ubus service events),
  taken out once podkop runs. Lists are built in RAM and fetched again daily.
  Connection health shows the counts and whether the guard is active.
- Agent 0.13.0: `heal` status carries `guard`; events `strict_guard`; the
  uninstall removes the table and the file.

## 1.37.0 — 2026-11-09

- **Strict rules** (a switch on each Fastest rule, agent 0.12): the rule's
  traffic goes through its own servers or nowhere. Self-heal never lends it
  other rules' servers (servers from the rule's own history and from the
  probe are still taken); while none of its servers answers, connections are
  refused at once instead of hanging until a timeout, and open again the
  moment one of them answers. Overview shows "refusing connections", the
  Self-heal table marks strict rules. Events `strict_closed` / `strict_open`.
- Agent 0.12.0: command `strict`, `heal` status carries `strict` and
  `groups.<rule>.block`. The refusing member is `<rule>-strict-out` in the
  rule's selector (this sing-box build has no block outbound).

## 1.36.0 — 2026-11-09

- **Self-heal settles down**: a rule is up again only after 4 good checks in
  a row (2 minutes), and borrowed servers stay at least 5 minutes. One
  flapping server no longer reloads sing-box (and cuts every connection)
  every minute.
- **IPv6** (Connection health → IPv6, agent 0.11): podkop routes IPv4 only;
  a LAN that gets IPv6 from the router lets a device with its own DNS reach
  any site over IPv6 past podkop. One switch adds a firewall rule that keeps
  IPv6 from the LAN off the internet (REJECT: devices fall back to IPv4 at
  once). Overview warns while the LAN has IPv6 and the rule is off.
- **Config file in RAM**: Global settings → sing-box shows a warning while
  the config file is on flash (podkop rewrites it at every start, self-heal
  at every change) with a one-click move to /tmp.
- Agent 0.11.0: `status` carries `ipv6`, new command `ipv6 on|off`; the
  uninstall removes the rule.

## 1.35.0 — 2026-11-07

- **Scheduled changes**: Review and apply → When → Later applies the change
  at a day and time you pick, with the same checks and automatic undo, even
  with no browser open. It waits in History → Scheduled (Apply now, Back to
  draft, Cancel); Rules and Global settings show "N changes scheduled".
- At its time only the options the change touches are applied, on top of the
  router's settings then: servers the nightly probe or self-heal put in
  meanwhile are kept. An option changed on the router too, differently, is a
  conflict: nothing is applied, the change is marked rejected and an alert
  shows. A router that is away or busy is tried every 30 s for an hour.
- Manager only: no agent update needed.

## 1.34.1 — 2026-11-07

- A single link added as a key can be chosen under a rule's **Servers from
  keys** (1.34.0 refused it with "keys are ids of this router's
  subscriptions"); the rule's probe line counts keys, not subscriptions.
  No change on the router: agent 0.10.0 stays.

## 1.34.0 — 2026-11-07

- **Update agent from the manager** (agent 0.10.0): Settings → Update agent
  (also in Connection health) sends this manager's own router files to the
  agent, which installs them with the same installer and rollback as before;
  progress shows in the dialog. An agent older than 0.10 still needs the
  command pasted once; the dialog keeps it under "Or run a command".
- install.sh --upgrade ends a leftover test sing-box of a connection check
  (agents before 0.9.1 could leave one holding the check's lock for good)
  instead of waiting 90 s and stopping.
- Reliability on the server: `GET /health` (no login) and a Docker
  `HEALTHCHECK` that uses it; a copy of everything (database and SSH keys) is
  saved in the data volume daily, the last 14 kept, listed under Settings →
  Backup; the image is built from exact dependency versions
  (requirements.txt); 10 wrong logins from one address lock it out for 10
  minutes; `PODKOP_MANAGER_HTTPS=1` sends the login cookie over HTTPS only.
- Speed: the agent's `status` carries the groups and servers and keeps the
  router's constant facts (model, versions) in RAM, so a poll is one SSH
  command instead of two and ~10 processes fewer on the router every 30 s;
  exit IPs of all rules are looked up in one call, four at a time (`exit-ips`);
  API answers and the interface's files are gzip-compressed.
- Self-heal probe is quick: only the down rules' countries, pings only, no
  speed test (`-q --no-speed`), and only those rules get its servers; the
  other rules keep theirs until the nightly probe, whose result stays the
  "last probe" they take servers from.
- **Keys and usage** is one list of equal rows: name and kind, status,
  today's usage with the limit, period, what the last probe found of the
  key's servers, the rules that use it, Edit; a row opens to its chart, the
  split by rule, the limit's source and what happens at the mark. "Add key"
  is a dialog in the page header.
- **Single links are keys like subscriptions** (agent 0.10.0): a link added
  under Keys is tested by the probe at every run (`probe-start` and
  `probe-nodes` take `links`), rules that take servers from the probe can be
  limited to it under "Servers from keys", and its usage counts as before.
- The web interface is ES modules (17 files instead of one 3400-line
  app.js); CI lints them (a name used without its import fails) and opens
  every page in Chromium with Playwright. The diagnostics tests run in 5 s
  instead of 30 (PODKOP_DIAG_TICK).

## 1.33.1 — 2026-11-07

- Faster self-heal (agent 0.9.1): every 30 s the agent tests the server each
  URLTest group uses now, and every server of a group whose server failed or
  that has none working (all at once, 5 s). A group is down after two such
  checks in a row: 30–75 s instead of two URLTest rounds (7 minutes).
- A group reported down reports its recovery also after an apply in between
  (the self-heal probe's), so its alert goes away; the manager also clears a
  "servers stopped answering" alert when the router has no group down.
- The manager keeps the router-resolved server addresses: Overview no longer
  shows a probe whose servers were just applied as "has new servers".
- Down groups of one router are healed one at a time, and lending servers is
  retried while the router is busy (no more "apply is in progress").
- install.sh --upgrade waits up to 90 s for a running connection check or
  apply instead of stopping at once; a connection check's test sing-box no
  longer inherits the check's lock.

## 1.33.0 — 2026-11-07

- Self-heal, on by default. Agent 0.9.0 reports a URLTest group whose own
  servers have all failed for two test rounds (at least 6 minutes) while the
  WAN answers, and lends it up to 3 working servers of other groups at once,
  in sing-box's config in RAM only. Manager 0.21.0 then lends servers the
  section had in earlier applied versions (when there was nothing to borrow),
  runs the probe (at most once per 30 minutes) and applies its result.
  Borrowed servers are removed when the group's own servers answer again or
  podkop's settings change; podkop's lists are downloaded again if their last
  update failed. Sections switched off in Connection health → Self-heal are
  never changed and never lend servers.
- The probe through the router no longer stops when ipinfo.io and
  api.ipify.org don't answer (podkop may route them into the group that is
  down); only a dead SSH tunnel stops it.
- Probe picks spread across networks: at most 2 servers of a rule from one
  /24 while a healthy server elsewhere is left. Server addresses are resolved
  on the router (agent `resolve`).
- Loading a saved config no longer puts in the servers of a probe run older
  than a day; the file's servers are kept and an event says so.
- The web interface has a browser tab icon.

## 1.32.0 — 2026-11-04

- Manager 0.20.0 redesigns Connection health around current status, freshness
  and suggested next steps, with expandable DNS, WAN, HTTP and download evidence.
- Repeated observations are grouped by source, type and target. History windows
  and filters make bursts easier to investigate; counts include the full window
  rather than only the most recent 300 observations.
- Server history distinguishes cooldown recommendations from confirmed runtime
  exclusions. Live refreshes preserve unsaved monitoring settings and input focus.

## 1.31.3 — 2026-11-03

- Agent 0.8.3 validates diagnostic and recovery server tags without jq regex
  functions, which OpenWrt's standard jq package does not provide. Valid tags
  no longer fail with the misleading "up to four server tags expected" error.
  The four-server limit and strict filename character checks are preserved.
- CI now builds jq 1.8.1 without Oniguruma and tests diagnostics, recovery and
  upgrades with BusyBox tools, including valid and invalid server tags.

## 1.31.2 — 2026-11-03

- Agent 0.8.2 runs Connection health on routers without the optional BusyBox
  `timeout` command. Router, primary and provider DNS lookups retain a
  six-second limit; stalled or cancelled lookups are stopped and reaped.
- Regression tests run the full collector without `timeout`, check successful
  and failed DNS results, and verify lookup deadlines and process cleanup.

## 1.31.1 — 2026-11-03

- Manager 0.19.1 detects older router agents before connection checks and
  explains how to upgrade instead of reporting `unknown command diagnostics`.
- **Update agent** provides a separate upgrade command that preserves paired
  SSH keys, forwarding access, firewall rules, service state and Podkop settings.
  Failed file validation or service restarts restore the previous agent files.
- Agent 0.8.1 reports installed capabilities, including missing connection health
  helpers. Manager recognises upgrades without a restart.

## 1.31.0 — 2026-11-03

- Manager 0.19.0 and agent 0.8.0 add Connection health: incident grouping,
  current routing context, separate DNS/direct/proxy checks, resource and WAN
  snapshots, and bounded download checks for stalled transfers.
- Optional temporary exclusion requires paired transport failures and a
  verified replacement. Runtime overrides preserve UCI lists, respect manual
  pins, roll back failed reloads, and expire when Manager stops renewing them.
- Authenticated Kuma webhooks preserve failures and recoveries without using
  notifications as routing commands. Access rejections remain distinct from
  transport failures.
- Probe selection offers browsing, interactive and streaming priorities and
  incorporates recent confirmed failures. Histories follow the connection
  identity when URLTest tags or display names change.
- Router installers and releases include reliability.sh. Monitoring and
  recovery are off by default until the first router check is reviewed.

## 1.30.2 — 2026-10-31

- **Logs hide failed reverse lookups** (manager 0.18.2). Devices ask PTR
  names by themselves (Apple's Bonjour asks `lb._dns-sd._udp.<its
  network>.in-addr.arpa` every few seconds), and some zones never answer, so
  sing-box filled Logs with "exchange failed … in-addr.arpa. IN PTR" errors
  although nothing broke. Such lines (in-addr.arpa and ip6.arpa, also the
  "N more exchanges failed" summaries) are now level "noise": hidden in
  All, Warnings and Errors, shown with **Show unimportant lines**. Lines
  stored before are marked too. Failed lookups of other names stay errors.

## 1.30.1 — 2026-10-31

- **An update shows without clearing the browser's cache** (manager 0.18.1).
  The web interface's files had no Cache-Control, so browsers kept app.js
  for hours by their own guess and a normal reload ran the old one: new
  parts were missing until Cmd+Shift+R, a cleared cache or a private
  window. They are now sent with `Cache-Control: no-cache`: the browser asks
  on every load whether they changed and gets a short "not modified" when
  not, so pages load as fast and a normal reload shows the new version.

## 1.30.0 — 2026-10-31

- **Your own servers in a rule with keys** (manager 0.18.0). Under "Servers,
  3 from VPN Service #11521" there is **Add your own servers**: paste links
  that are not in any key. They go into the URLTest after the keys' servers,
  tagged "yours", with a remove button.
  - When a provider changes its servers, the new list keeps your own (also
    a server the provider dropped, if you added it yourself).
  - Saved at once, like the keys; Discard puts them back. A rule whose own
    servers changed in a draft not applied yet waits for it.

## 1.29.0 — 2026-10-31

- **Servers from keys, without the probe** (manager 0.17.0). The Keys choice
  moved from Probe parameters into the rule, above **Take links from probe**,
  as **Servers from keys**.
  - Probe off: the rule's URLTest gets every server of the chosen keys
    (podkop's protocols, each server once), shown as "Servers, 9 from Gas
    VPN" instead of the editable list.
  - The manager rereads the subscriptions every 10 minutes. When a
    provider's servers changed, it applies the new list by itself (History:
    "provider's new servers"), and an open draft gets it too. Each new list
    is applied once: one the router did not take is not tried again.
    A rule whose keys were changed in a draft not applied yet waits for it.
  - Probe on: the probe picks only from the chosen keys, as in 1.28.0.
  - Discard puts the keys back with the probe parameters. A 1.28.0 rule's
    keys move over by themselves.

## 1.28.0 — 2026-10-30

- **A rule's servers from chosen keys** (manager 0.16.0). Probe parameters
  have a **Keys** row: a button for each subscription in Keys and usage.
  With Gas VPN on, the rule's URLTest takes only Gas VPN's servers (still
  the fastest that pass its other filters); with none on, all keys, as
  before. Each rule chooses on its own, e.g. main from one key and youtube
  from all.
  - A server's key is found by its link in the key's server list, as for
    traffic and moving rules to other keys; the probe still tests every key.
  - Other keys' servers are not listed under "left out", like other
    countries.
  - A rule whose chosen keys were all removed takes every key's servers again.

## 1.27.0 — 2026-10-25

- **Rules that share another rule's servers** (podkop-agent 0.7.0). A rule of
  type sing-box JSON with `{"type": "selector", "outbounds": ["main-out"]}`
  sends its domains through main's servers: same servers, same switching,
  nothing more for the router to test or keep connected. Such rules split
  main's domains into segments (Anthropic, Meta, GitHub, ...).
  - The agent's check of a rule's sing-box JSON accepts the outbounds of
    other rules; before, it refused `main-out` as unknown, and with it every
    apply of such a config.
  - Exit IP in Now routing follows the rule to main and to main's server now.

## 1.26.0 — 2026-10-19

- **No minutes-long hang when a server dies** (podkop-agent 0.6.0). When a
  gRPC server stopped answering, sing-box kept the connections through it
  open, waiting for data that never came; apps (phones above all) hung on
  them for minutes, even after urltest had switched to another server. The
  agent now adds two sing-box options podkop leaves off, after every podkop
  start (the daemon, within ~15 s, sing-box reloads in place) and in applies
  that change only servers:
  - gRPC transports: HTTP/2 pings, `idle_timeout` 15s and `ping_timeout`
    15s (a link's own values stay). A quiet connection is pinged and dropped
    when no answer comes, with everything on it, so apps reconnect at once.
  - urltest groups: `interrupt_exist_connections`, so a switch closes the
    connections still open through the server it left.
  - On openwrt-reserv, the server of a running download blocked: without
    the pings the download stayed frozen for over 3 minutes (also after the
    switch); with them the connection was closed 29 s after the block.
  - podkop's own files are not changed. The reload when the agent adds the
    options cost at most one failed DNS check (~0.1 s).
- podkop-agent finds podkop's sing-box through procd: `sing-box check`, the
  exit-IP check and the probe run sing-box processes too.

## 1.25.0 — 2026-10-19

- **No outage when only servers change** (podkop-agent 0.5.0). podkop's
  restart stops sing-box, removes its firewall rules and DNS setup, rebuilds
  everything and loads the IP lists again: 20-30 s in which nothing goes
  through the proxy. When an apply changes only which servers rules use
  (the nightly probe, Take links from probe, edited server lists), the agent
  now builds the new sing-box config with podkop's own functions (the same
  `sing_box_init_config` its start runs), puts it in place and has sing-box
  reload it in place (SIGHUP). Firewall, DNS and lists stay as they are.
  - On openwrt-reserv with openwrt-main's config: DNS through sing-box
    checked every 0.1 s failed 0-1 times during the swap (about 0.1 s),
    against 20-30 s for a restart; sing-box stays the same process.
  - The config is byte for byte what podkop's own restart writes (checked:
    a restart afterwards reports "configuration is unchanged").
  - Any other change (domains, lists, DNS, settings) still restarts podkop,
    and so does a swap that fails for any reason; the health check and the
    automatic undo work as before.
  - The apply dialog shows "sing-box took the new servers, podkop kept
    running" (manager 0.15.0).

## 1.24.0 — 2026-10-18

- **Nightly probe in the rules:** its settings (time, apply by itself, TLS
  fingerprint, where servers are tested, podkop-engine download) moved from
  Global settings into every rule that takes links from the probe, under the
  rule's probe parameters. It stays one run for all those rules, so they
  share the settings. Overview's **Schedule** opens such a rule.
- Manager 0.14.0.

## 1.23.0 — 2026-10-18

- **Add router without the root password** (published here: the v1.22.0
  release was made before it, so its install.sh lacked `--release`). Add
  router shows a command to run on the router. It downloads install.sh of the manager's own release
  (`--release`) and installs podkop-agent, podkop-probe and the manager's key,
  with the Clash API options and, if ticked, the forwarding-only key
  (`--forward-key`; a run without it removes that key). At the end it prints
  `podkop-router <address> <port> <host key>`; pasted into Add router, the
  manager checks that this host key answers and that its key runs
  podkop-agent there before it adds the router. **Reinstall agent** shows the
  same command. The password fields and PODKOP_MANAGER_ROUTER_PASSWORD are
  gone; the manager never logs in with a password.
- **Faster build:** podkop-engine is no longer in the Docker image. GitHub
  serves it slowly (~100 KB/s), and a build stopped during the download lost
  the cached apt step too. The manager now downloads it once in the
  background when it starts (~13 MB, checked by SHA-256) into the data
  volume, where rebuilds keep it. Global settings shows the download and
  has a button to try again if it failed. Testing servers on the manager's
  server works as before once it is there.
- Manager 0.13.0.

## 1.22.0 — 2026-10-18

- **Test servers on the manager's server, through the router** (manager
  0.12.0, Global settings → Nightly probe → Test servers). The router only
  reads the keys (with its own device ID, as before) and turns each server
  into a sing-box outbound with podkop's own parser (podkop-agent 0.4.0:
  `probe-nodes`; podkop-probe `--build-nodes`). The manager's container runs
  podkop-probe on them (`--nodes FILE`) with podkop-engine-full, and every
  test connection, the DNS for the servers' names included, goes out through
  the router over SSH (`--via`), so the servers see the router's address and
  the router's processor and memory stay free.
  - It uses a second manager key that may only forward connections: the page
    shows its line for `/etc/dropbear/authorized_keys`
    (`command="/bin/false",no-agent-forwarding,no-X11-forwarding,no-pty`), and
    **Check the router** tries it. The manager never changes the router's keys.
  - Servers that use UDP (hysteria2, TUIC, WireGuard) can't be tested
    through SSH and are skipped.
  - The Docker image adds busybox, curl, jq and podkop-engine-full's static
    sing-box (checked by SHA-256). The backup keeps the second key.

## 1.21.0 — 2026-10-18

- **Faster probe:** one sing-box runs every server under test, each behind its
  own local port, instead of one sing-box per server: no start-up per server,
  and a few MB per server tested instead of ~40 MB. Servers are tested one per
  CPU core at a time (a router with ~100 MB free managed only 2 before; more
  than the CPU can handshake makes every ping look slower). A server sing-box
  refuses is set aside with its message; if sing-box stops during the test, it
  is started again and the servers tested meanwhile are tested again.
- **Less work per server:** 20 ping requests instead of 40 (losses still show
  in 5% steps), and a speed test of 3 s instead of up to 8 s. The speed counts
  from the first second on, after the tunnel's ramp-up, so the short test
  gives the steady speed. A test also ends after 2 s when the outcome is clear
  (at 85% of the line's own speed and above the minimum: a node can't show
  more than the line). `-n`, `-F` and `-q` (now 2 s) set other sizes.
  On openwrt-reserv (dual core, 35 servers, a ~45 Mbit/s line): 347 s → ~175 s.
- The test's local ports start at 20000, below the ones Linux gives outgoing
  connections, and a port held by any connection is skipped (a busy port could
  stop the test from starting).
- Manager 0.11.0: **Overview → Now routing** shows where each rule's traffic
  leaves to the internet: country and exit IP of the server it uses now (or
  of a VPN rule's interface), looked up when the server changes and hourly.
- **Less flash wear on the router:**
  - the nightly self-update downloads to /tmp and writes
    /usr/bin/podkop-probe only for a newer version;
  - the probe's own nightly run doesn't rewrite /etc/config/podkop or restart
    podkop when the servers are the same;
  - the agent rewrites its pins file only when a pin was dropped.
- podkop-agent 0.3.2: `exit-ip <section>`; the router's UTC offset in the
  status.

Fixes from a review of the whole project:

- **Security:**
  - podkop-agent accepts only http(s) subscription keys;
  - podkop-probe never passes a key to curl where it could be read as an
    option;
  - **Reinstall agent** checks the router's SSH host key before it sends the
    root password;
  - install.sh refuses a cut-off download of the agent or the probe.
- **The router keeps its settings:**
  - after a crash the watchdog restarts podkop with the settings it has (made
    in LuCI or by the probe) and goes back to the last applied ones only if
    podkop does not start with them;
  - a moment when ubus does not answer no longer looks like changed settings;
  - Restart and Stop from the manager wait for an apply in progress;
  - the probe's own run waits for the agent's apply;
  - an apply killed while the agent runs (out of memory) is undone at once,
    not at the next reboot.
- **Agent:**
  - several checks at once no longer share scratch files;
  - pins come back even when the Clash API answers late, and two pin changes
    at once don't lose one;
  - a probe that could not start, ran past 3 hours or was killed is marked
    failed, and its sing-box and work folder are removed;
  - the traffic counts in RAM keep only the current links;
  - a refused probe request no longer replaces the last run's.
- **Manager:**
  - a command that times out closes its SSH channel;
  - a move of rules for a traffic limit counts only once the router took it
    (it is tried again otherwise), runs in the background, one at a time;
  - rules and their preview never get the servers of a key moved away from;
  - **Let the manager run it** keeps the time on the router's clock (the
    manager may run in another time zone), takes the router's keys when the
    manager has none, and refuses a cron line without one daily time;
  - an imported YouTube list with several countries takes the first, and
    "any country" becomes "plays";
  - with the fingerprint fix on, the probe table shows where the fixed
    servers went;
  - removing a router stops its probe and apply tracking;
  - a probe still running after 3 hours is marked failed;
  - install steps time out;
  - bad input gets a 400 instead of a 500.
- **UI:**
  - draft saves go one at a time (fast typing on a slow line no longer
    reports "changed elsewhere" and loses the edit);
  - Rules and Global settings open from the last read settings when the router
    is away, and switching between rules doesn't read the router again;
  - the page no longer stays dimmed after a quick click elsewhere;
  - Review and apply, Show older, Save config and Add key ignore a second
    click while the first is under way;
  - renaming or duplicating a probe rule keeps its probe parameters on the
    page that opens next.

## 1.20.0 — 2026-10-18

- Manager 0.10.0: adding a router that already runs podkop changes nothing in
  podkop:
  - its settings are saved in **Configs** ("as found");
  - the subscriptions of a podkop-probe that runs there by itself go into
    **Keys and usage**;
  - its main and YouTube lists become rules that take links from the probe,
    with the same countries and limits.
  - Overview says when that podkop-probe still runs by itself every night.
    **Let the manager run it** turns its cron line off and runs the probe from
    the manager at the same time.
- **Add router** and **Reinstall agent** can leave the firewall as it is
  (Clash API stays open to the LAN).
- **Without podkop-engine:** the agent installs, reads and applies settings.
  Overview says the probe can't run there.
- **TLS fingerprint:** a switch in Global settings → Nightly probe gives REALITY
  links with fp=qq, 360, ios, android or edge (rejected by servers since
  Xray-core v26.9.8) fp=chrome, both in the probe's test and in podkop. A rule's
  own links show a **Use chrome** fix.
- Logs: wget's progress from podkop's list update and crond's job starts are
  no longer shown as errors.
- podkop-agent 0.3.1: `probe-config`, `probe-cron off`, engine and probe cron
  in the status. podkop-probe: `--fix-fingerprint`.

## 1.19.0 — 2026-10-18

- Manager 0.9.0: **Routing → Configs**. Save a router's running config or
  its draft, with each rule's probe parameters; download it as a JSON file;
  import such a file or podkop's own `/etc/config/podkop`; load any saved
  config into a router's draft (its probe rules get the last probe's
  servers right away).
- **Settings → Backup**: download the whole manager (database and SSH key)
  as one `.tar.gz`, and restore it, e.g. on a new server.
- **YouTube region** in a rule's probe parameters: not checked, any (video
  plays), or the country YouTube must detect (RU: no ads), instead of the
  fixed "sees Russia".
- The manager's probe runs judge servers by their exit only
  (`--no-entry-check`): relays that enter in another country, like
  whitelist bypasses entering in Russia, are no longer dropped before the
  test. A rule whose countries no tested server exits in says so.
- Stability fixes in the manager:
  - **Background work:** polling, usage, the nightly scheduler and pruning
    no longer stop for good after an unexpected error. A garbled answer
    from the router marks it offline instead.
  - **Nightly probe:** if the router is unreachable at the set time, it is
    retried within the hour.
  - **Traffic limits:**
    - A key is no longer avoided again right after its provider reset.
    - A failed move to other keys is retried every 10 minutes.
    - Keys counted on the router include the current hour.
  - **Restore:** only one runs at a time. Apply tracking stops before it,
    and the upload size is checked while it arrives.
  - **Bad input:** files, rules and requests get a clear error instead of
    a server error. The login check no longer blocks the server.
  - **Cleanup:** old versions (beyond 500 per router) and old probe results
    (beyond 30) are pruned. A removed router or key leaves no settings or
    traffic behind.
  - **UI:**
    - Probe parameters keep focus while you type, and Exit IP keeps the
      spaces between addresses.
    - A rule editor you left can no longer overwrite the draft.
    - Live updates reconnect after an error.
    - Overview reads the config once the router is back.
- **Discard** also puts back the rules' probe parameters (and what "Load
  into draft" changed) as they were before the draft.
- **Two tabs:** a draft changed in another tab, or by "Load into draft", is
  no longer overwritten. The page reloads it instead.
- **After a restart or restore:** the manager follows the changes and probe
  runs the router was still busy with.
- **Traffic per server:** with agent 0.3.0, traffic is credited to the
  server it went through, not to the position in the rule. Moving servers
  no longer mixes up their bytes.
- podkop-agent 0.3.0 (**Settings → Reinstall agent** on each router to get it):
  - **Interrupted apply:** a change cut off by a reboot or a killed process
    is undone when the agent starts again (event "apply_interrupted").
  - **Killed probe:** a probe killed for lack of memory shows as failed,
    instead of "running" forever.
  - **Failed undo:** when the old settings can't be put back, the change is
    reported as failed instead of "the old settings are back".
  - **Smaller fixes:**
    - The apply lock is no longer held by podkop's own background jobs.
    - `last-good` is written atomically.
    - A damaged traffic state is reset.
- podkop-probe:
  - **Self-update:** a cut-off download is never installed (end marker and
    `sh -n`).
  - **Server names:** names like "Безлимит" or "Для всех устройств" are no
    longer taken for placeholder entries. Control characters in names
    don't break the results.
  - **Cleanup and memory:** a closed SSH session cleans up the test
    sing-box instances. The YouTube and speed tests respect the router's
    free RAM.
  - **Nightly lock:** a stale `--cron` lock no longer blocks every run.
  - **Relays:** relays that share one entry address but differ in SNI,
    path or user get their own speed and YouTube tests instead of copying
    another one's.

## 1.18.0 — 2026-10-18

- Manager 0.8.0: the Servers and Servers probe pages are gone. **Keys and
  usage** comes right after Overview.
- A Fastest (URLTest) rule has a **Take links from probe** switch; when on,
  the probe's parameters (region, protocol, exit IP, name, ping, speed,
  YouTube, how many) and **Run probe** are shown under it.
- The probe tests the countries of the rules (all, if a rule takes any);
  the probe page's countries move into the rules once ("Europe" is one
  choice in the rule). Schedule and auto-apply are in **Global settings**.
- Overview: **Review and apply** for a new probe result, **Unpin** for a
  pinned rule.

## 1.17.0 — 2026-10-18

- Manager 0.7.0: every rule, main and youtube included, can take its servers
  from the probe, each with its own filter (countries, protocols, exit IP,
  name), how many to keep, ping and speed limits, and a YouTube requirement
  (plays, or sees Russia without ads). The probe's own main and YouTube
  lists are gone; existing ones turn into such rules with the same limits.
- Servers probe keeps what to test (countries, quick run), the schedule and
  auto-apply; the probe tests with the loosest limits of the rules.
- Keys are added and removed under **Keys and usage** (subscriptions and
  single links); Servers probe only lists them.
- Login name: `PODKOP_MANAGER_USER` in `.env` (default `admin`).

## 1.16.0 — 2026-10-18

- Manager 0.6.0: new page **Keys and usage**. For each key: used today
  against its limit, a forecast at the last 3 hours' rate, the split by rule,
  the last 14 days and today's traffic by server. The menu shows the highest
  daily limit used, Overview warns when a key will run out today.
- Limits come from the provider (subscription-userinfo, read every 10 min)
  or are set by you; single links can be added as keys with their own limit.
- At 90% (or your threshold) a key either only warns or moves its rules:
  the probe's rules that use its servers are rebuilt from the last probe
  without them, through the usual checked apply, and put back after the
  reset. History shows these changes as "traffic limit".
- Agent 0.2.0: counts traffic per server from sing-box's connections, and
  reads each key's usage through podkop-probe (same device for the
  provider). Reinstall the agent from Settings to get it.
- probe: `--sub-info` prints each key's usage header and links.
- `.env`: `TZ` sets the time zone for "today" and the nightly probe.

## 1.15.0 — 2026-10-18

- Manager 0.5.0: pages swap in when their data is loaded, with a short fade,
  instead of going blank first; quick clicks show only the last page.
- Servers probe: a key's result ("device limit reached", server count) is
  shown only next to the key that ran, not next to whatever key now sits in
  the same place in the list.
- Less text: no page subtitles, shorter hints; the probe table drops the OK
  column (hover the result instead).

## 1.14.1 — 2026-10-18

- The web interface's port is `PODKOP_MANAGER_PORT` in `.env` (default 8080).

## 1.14.0 — 2026-10-18

- Manager 0.4.0: credentials come from `.env`: `PODKOP_MANAGER_PASSWORD`
  (web login) and optionally `PODKOP_MANAGER_ROUTER_PASSWORD` (used when the
  root password field is left empty). `set-password` and the password change
  in Settings are gone; a new password logs everyone out.

## 1.13.0 — 2026-10-18

- The repository is now `podkop-manager` (old links redirect).
- Manager 0.3.0 runs with Docker Compose (`compose.yaml`, host network, data
  in a volume); `install-ubuntu.sh` and the systemd unit are removed.
- New layout: the manager at the root, router files in `router/`. Release
  assets keep their names, so installed routers keep updating.
- One README for everything.

## 1.12.0 — 2026-10-18

- Manager 0.2.0: new web interface: a menu with a router switcher, Overview,
  Routing rules, Global settings, Servers, Servers probe, Logs, History,
  Settings.
- Changes to rules and settings collect in a draft kept on the server until
  **Review and apply**; the apply dialog shows each step live.
- Rules can take servers from your subscriptions: by exit country, protocol,
  exit IP or name. They are refreshed after every probe run.
- Logs filter by source (podkop, sing-box, engine, agent) and level.
- History shows what started each change (you, a restore, the probe) and
  pages back further. Restoring an old version goes through the same check.
- Password can be changed in Settings.
- Probe page shows each key's result ("device limit reached", server count).
- probe: links with the same exit IP are no longer dropped; the best links
  are kept in order.
- probe: saves each server's link next to the results (`<csv>.links`).
- Agent 0.1.1: passes those links on and returns the full probe log.

## 1.11.0 — 2026-10-17

- podkop-manager: web interface on a Linux server for podkop on one or more
  routers: all settings and sections, checked apply with history and
  restore, server pinning, delay tests, probe runs (manual or nightly),
  live logs.
- podkop-agent on the router: checks every change before podkop is touched,
  undoes it if podkop doesn't start, brings podkop back after a crash,
  keeps pinned servers after restarts.
- `agent/install.sh`: agent, probe, a key that may run only the agent,
  Clash API closed to the LAN.

## 1.10.1 — 2026-10-17

- Shorter README; download and update links use the current repo name.

## 1.10.0 — 2026-10-17

- Needs podkop 0.7.24 and podkop-engine. Servers are built by podkop's own
  parser, so they are tested exactly as podkop runs them; xhttp works.
- The next 5 servers of each list go into podkop's fallback links.
- After the restart: wait for podkop's groups, reset a hand-picked server,
  restore the old settings if podkop fails to start.
- Clear message when a key's device limit is reached.

## 1.9.1 — 2026-09-27

- "y" at the final question applies (it used to do nothing); unknown
  answers ask again.
- `--apply-saved` applies the last run's lists without testing again.

## 1.9.0 — 2026-09-27

- Separate `youtube` section (above `main`) with servers where YouTube sees
  Russia (no ads).
- Never picked: ping 700 ms or more, 40 Mbit/s or less (`--max-ping`,
  `--min-speed`).
- Choose where to apply: both, main, youtube, or other sections
  (`--apply-to`).

## 1.8.0 — 2026-09-27

- YouTube check: servers where YouTube doesn't play are never picked.
- Fix: the settings file with the keys was readable by everyone.

## 1.7.0 — 2026-09-26

- `--install` asks for keys, countries and time; menu item 4 changes them.

## 1.6.0 — 2026-09-26

- "Repeat this every night?" installs `/usr/bin/podkop-probe` with the saved
  keys; the nightly line is `podkop-probe --cron`, which also updates
  itself. Log in `/tmp/podkop-probe.log`.
- `--install`, `--update`, `--uninstall`.
- Quieter output: one progress line, a short table, one question.

## 1.5.0 — 2026-09-26

- Several speed tests at once, sized to the router's line.
- "Europe only" also checks the server's IP, so relays through Russia are
  skipped.
- Fix: the pick list always showed speed as "-".

## 1.4.0 — 2026-09-26

- Fix: the speed test showed 0 Mbit/s on every server.
- Speed test stops once the result is stable; links to the same server share
  one test.

## 1.3.0 — 2026-09-26

- Latency and speed tested in one session per server; picks ranked by
  latency and speed equally.
- Type server numbers at the end to choose your own.

## 1.2.0 — 2026-09-26

- Subscriptions fetched like Happ, Streisand, INCY and others do, so panels
  return real servers; JSON subscriptions supported.
- Country filter (`--countries`), Europe by default.
- Subscription name, traffic and expiry shown.
- `tools/sub-check.sh`: what a subscription returns to each client app.

## 1.1.0 — 2026-09-26

- Several subscriptions at once, speed test, nightly run.
- podkop isn't restarted when the picked servers didn't change.

## 1.0.0 — 2026-09-26

- First version: tests every server of a subscription on the router, picks
  the best and puts them into podkop.
