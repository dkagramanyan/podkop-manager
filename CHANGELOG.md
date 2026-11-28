# Changelog

Versions are podkop-probe's (`probe.sh`); a release also carries
podkop-agent and podkop-manager.

## 1.62.0 — 2026-11-28

zapret is removed (agent 0.32.0, manager 0.49.0). Update the manager, then
**Update agent** on each router (reserv first): the agent takes zapret away
by itself within a minute, nothing to click.

- **Rules that had zapret first now use their servers** (YouTube, Meta, X,
  Discord and the others): more traffic goes through the keys. No setting
  to change; the rules, kill switches and Protected stay as they were.
- **QUIC works again for rules by domain.** While a router had zapret rules,
  its firewall refused the devices' QUIC to every rule by domain, not only to
  the zapret ones.
- **Fewer reloads of sing-box.** zapret's checks moved rules between the
  direct path and their servers and mended missing parts, each time with a
  reload that cut every connection and made other changes answer "an apply
  is in progress".
- How the agent takes it away: sing-box first gets podkop's config without
  the zapret outbounds (with them in and the table gone their traffic would
  loop); only then nfqws2 stops and its table, `/etc/podkop-zapret`,
  `/etc/init.d/podkop-zapret` and their lines in `/etc/sysupgrade.conf` go.
  While sing-box cannot be reloaded (an apply runs) it tries again the next
  round. One event, `zapret_removed`, says when it is done.
- **Devices' own DNS is opt-in again** (Connection health): a new router
  gets it off, and while it is on the router no longer answers the public
  resolvers' names (dns.google, cloudflare-dns.com, mask.icloud.com…) with
  "no such name". That part of 1.60.2 broke iCloud Private Relay and apps
  with a fixed DNS name; the agent removes its dnsmasq file by itself. A
  router where the switch is on keeps it on (port 53 to the router, DNS over
  TLS/HTTPS to the known resolvers refused): untick it there if you don't
  need it.
- Gone from the web interface: Zapret first on a rule's page, the zapret
  tags in the rules list, the strategy list and check interval in Settings,
  the zapret file in Configs → Import, zapret's events and alerts. A config
  file from 1.57–1.61 with a `zapret` part still imports (that part is
  left out).

## 1.61.1 — 2026-11-26

Manager 0.48.1 (no agent change). Update the manager; nothing else to do.

- **YouTube's zapret state tells the truth about videos.** With 1.61.0 the
  rule was "zapret" while only some videos played: the provider's own
  video server opened, the video servers of Google itself (where the
  videos it does not hold come from) got no answer with any strategy of
  the list. Two of Google's video servers are now among YouTube's check
  addresses, so such a rule shows "zapret opens its sites only partly", its
  traffic goes through its servers (YouTube plays), and the search for a
  strategy that opens everything goes on by itself every 6 hours.
- A rule whose check addresses are a shorter list of its service's gets the
  rest on the manager's first look after an update; a list of your own is
  left as it is.

## 1.61.0 — 2026-11-26

A zapret rule is checked with all of its addresses, and gets its strategy by
itself (agent 0.31.0, manager 0.48.0). Update the manager, then the agent
on every router with zapret rules; nothing else to do.

- **A site that opens only partly no longer stays on zapret.** The check
  used one address per rule (YouTube: `www.youtube.com`). With the strategy
  the routers had, that page opened while `youtubei.googleapis.com` (what
  the YouTube apps talk to) and the provider's own video servers
  (`rr…googlevideo.com`) got no answer: the rule showed "zapret", and
  YouTube did not play. A rule now keeps a list of check addresses; the
  router opens each of them through zapret at every check. When one fails
  3 checks in a row while the first still opens, the rule's traffic goes to
  its servers (state "partial", event `zapret_partial` with the addresses
  that failed), and comes back after 6 checks in a row with all of them
  open, or when its strategy changes. A rule that goes to its servers
  again within 6 hours of coming back stays there for 6 hours (each move
  reloads sing-box; an address that opens now and then must not move the
  rule back and forth). The first address works as before (sing-box's own
  check).
- **The strategy is fitted by itself.** When the router reports a partly
  opening rule, the manager runs the strategy search on that router with
  all of the rule's addresses and sets the fastest strategy that opens
  every one (`zapret_fitted`), or tells that none does (`zapret_unfit`; the
  rule stays on its servers, tried again every 6 hours). Rules set before
  this version get their service's addresses on the first look, so a wrong
  strategy is found and replaced without a click.
- **An address learnt on the router's line.** YouTube's video servers
  differ per provider. A check address may be a rule for finding one
  (`zapret.json`: where to read it, which word, how its letters map), so
  the search and the check test the video server of the router's own
  provider. YouTube's list: the site, the apps' API, the pictures' host
  and that video server.
- A router whose agent is older than 0.31 keeps working as before: it is
  searched with the plain addresses only and has no check of the others.
- The rule page's "Check addresses" are all kept and checked (one per
  line), and shows the ones that did not open in the last check. A config
  file carries them (`urls`); a file from before gets its service's.

## 1.60.2 — 2026-11-26

Agent 0.30.2, manager 0.47.1.

- **"Make the LAN's devices use the router's DNS" also closes the
  resolvers' names.** A rule by domain could carry dns.google.com or
  chrome.cloudflare-dns.com through its server; the firewall rule (by
  address) did not see that, apps (YouTube) got the sites' real addresses
  and went past podkop and zapret into the provider's block. While the
  switch is on, dnsmasq answers the well-known resolvers' names NXDOMAIN (a
  file in RAM, put back by the daemon), so apps use the router's DNS. This
  also turns iCloud Private Relay off on the network.

- **On by default.** A router gets the switch turned on when it is added
  (manager 0.47.1); Connection health turns it off. Routers added earlier
  are left as they are.

## 1.60.1 — 2026-11-26

Agent 0.30.1. Update the agent on every router with zapret rules.

- **Sites of a zapret rule open at once on phones and TVs.** QUIC of a
  zapret rule was refused on the router's side only: the device got no
  answer and waited for its own timeout on every connection (YouTube and
  Instagram apps looked dead on Wi-Fi). While there are zapret rules, the
  devices' QUIC (UDP 443) to podkop's FakeIP addresses is now refused with
  an ICMP answer before podkop takes it, so apps switch to TCP at once.
  That covers every rule by domain: one on its servers loses only QUIC
  inside the tunnel. A table of an older agent is replaced by the daemon.

## 1.60.0 — 2026-11-26

zapret mends itself, watched servers, a reworked apply (manager 0.47.0,
agent 0.30.0). Update the agent on every router (Settings → Update agent).

- **zapret no longer drops off by itself.** The agent puts a zapret rule's
  outbounds back into sing-box when they are missing for two rounds (after
  a podkop restart the one try could fail silently and every zapret rule
  stayed on its servers), starts nfqws2 again when it does not run (procd
  gave up after 5 crashes), and adds a missing firewall table instead of
  leaving every rule out. Each case is an event; `zapret_missing` (could
  not be mended) is an alert.
- **One stale zapret rule no longer blocks the others.** A rule that is
  gone or no proxy rule leaves zapret by itself; `zapret set` checks only
  the rules it adds or changes. The rule page turns zapret on or off for
  its own rule only, so a page open for a while no longer turns off rules
  set elsewhere; the strategy search merges into the rules as they are then.
- **zapret's state on every page**: zapret, on its servers, missing,
  stopped, paused (kill switch or Protected); firewall counters (QUIC
  refused) in the support report.
- **Support report**: zapret's state, table and log, 1000 log lines, 200
  events, and the stored log around a moment you pick on the Logs page.
- **Watched servers**: the bell beside a server watches it. The router
  tests it every minute, whether its rule uses it or not; after 3 failed
  minutes you get `server_down` (not while the provider's line is down),
  and `server_up` when it answers again. Settings → Notifications lists
  them.
- **Notifications**: each event can be switched off; quiet hours send
  Telegram messages without a sound; only a failed channel is retried (the
  working one got the same alert four times); an alert that could not be
  sent goes out later with its time.
- **Apply**: the draft stays while the router applies and ends with the
  job (a status poll mid-apply dropped it, with its cleanup and a loaded
  config's switches); a draft follows settings the router changed since
  (nightly probe, self-heal) unless the same setting changed on both
  sides; a loaded config's switches and zapret are kept until set, retried
  on a busy router, dropped with Discard, carried by a scheduled change; a
  reboot mid-apply ends the change from the router's event; the last
  change that did not stay is shown above the draft until dismissed.
- **Apply dialog**: Cancel before applying, a red check block and "Back to
  the draft" when the router undid it, buttons always in view, full screen
  on phones. Opening a probe rule no longer edits the draft ("Use them").
- **Overview** says whether every rule works and names the ones that
  don't; a dot per rule there and in the Rules list.
- **Phones**: the menu folds into a button, one rule panel open at a time,
  stacked forms, larger tap targets.
- Fixed: a key's edit sent its full subscription address to the browser;
  a raised limit left the key avoided until the next reset; monthly usage
  counted 15 days; a busy router skipped the nightly probe; the newest full
  probe result could be pruned behind heal runs; a VPN password being
  typed was cut by a finished check; Stop podkop asks first; sysupgrade
  keeps zapret's files, uninstall removes them.

## 1.59.0 — 2026-11-21

- **Nightly probe has a card of its own, at the bottom of Keys and usage**
  (time, apply the result by itself, chrome fingerprint, where servers are
  tested, and a Run now button), with a one-line summary ("Every night at
  03:00, applies the result by itself" or "Off"). It was at the end of the
  closed Probe panel of each rule that takes links from the probe, the same
  settings repeated on every such rule. A rule's Probe panel keeps the
  rule's own filter (countries, protocols, keep, limits, services) and links
  to the card (manager 0.46.0).

## 1.58.0 — 2026-11-20

A config keeps every setting of its rules (manager 0.45.0, agent 0.29.2).

- **Fixed (agent 0.29.2): zapret first was refused on every rule as soon as
  any rule had a kill switch or Protected** ("zapret works only on proxy
  rules without kill switch or protection"): the check looked the list up
  in itself instead of the rule's name. Update the agent on routers with a
  kill switch or Protected rule.

- **The router's switches go into a config**: Save config takes the
  router's Kill switch, Protected and self-heal off lists (for the config's
  rules) and its nightly probe settings (time, apply by itself, chrome
  fingerprint, where servers are tested, extra options) along with podkop's
  sections, the probe rules and zapret first, in a `router` part of the
  file. Load into draft says what comes back; once applied the lists
  replace the router's, the probe settings this manager's, each one on its
  own (an event names what was set and what was refused). When the router
  runs those settings already they are set at once. A file without the part
  (older ones, podkop's own) leaves them as they are.
- **Keys never go into a config**: subscriptions, which keys a rule takes
  servers from and your own servers stay in the manager's database (its
  /data volume and backups), by rule name, so a loaded config keeps them.
- A file with zapret first on a rule that also has a kill switch or
  Protected is refused (the router would refuse it); Save config leaves such
  a rule's zapret out.
- **zapret2 installs without GitHub's answer**: when GitHub cannot be
  reached or refuses (its limit for addresses without a login is 60 asks an
  hour), the release this manager downloaded and checked before is used.

## 1.57.0 — 2026-11-20

Zapret first travels with a config (manager 0.44.0; agent 0.29 or later).

- **A config file carries its rules' zapret first**: an optional `zapret`
  part, `{rule: {args, url, name, service}}`, next to podkop's sections and
  the probe rules. Configs → Import file keeps it (the list says "zapret
  first: youtube, meta, …"); Load into draft says which rules get it, and
  once the draft is applied the manager installs zapret2 on the router if
  it has none and turns zapret first on for those rules with the file's
  strategies (the router's other zapret rules stay). When the router runs
  those very settings already, nothing is to apply and it is turned on at
  once. Either way an event says how it went ("zapret first on …", or why
  not: an agent without zapret, the release not fetched).
- **Save a router's config** takes its zapret first along (the rules of the
  config only), so Download gives one file with both.
- **A zapret file alone** (`"format": "podkop-manager-zapret"`, only the
  rules) is taken by Import file too, and goes to that router at once.
- Every zapret part is checked like the rule page's: nfqws2 options only,
  an http(s) test address, a known service, and proxy rules of the config.

## 1.56.0 — 2026-11-20

A rule's page in fewer screens (manager 0.43.0; the agent is unchanged).

- **The switches are at the top**: Kill switch, Protected and Zapret first
  sit under the rule's name, each with a one-line state ("Now: direct,
  through zapret", "Searching: trying …"); the long explanations moved to
  their tooltips and to the Zapret panel.
- **Every part can be closed**: Matches, Sends to, Probe (its filter, the
  servers it matches now and the nightly probe), Zapret first (service,
  check addresses, strategies tried) and Testing and proxy options. A closed
  part shows a summary ("community meta, 33 domains, 17 subnets", "PL, DE ·
  vless · keep 5 + 2", "on · YouTube · fake md5 + multidisorder"). Open or
  closed stays for every rule, in this browser; "Expand all" / "Collapse
  all" next to Duplicate. Probe and Zapret start closed.
- **The list keeps its place**: from one rule to another the page no longer
  jumps to the top; on a wide screen the rule's page stays in sight beside
  the list (it scrolls on its own when taller than the window), and on a
  phone a rule opens at its page instead of at the top of the list.

## 1.55.1 — 2026-11-20

- **One check interval for zapret rules**, set in Settings → Zapret (15 s,
  30 s, 1 min, 2 min or 5 min; default 30 s instead of 1 min): sing-box's
  URLTest of a zapret rule and the agent's own test of its direct path run
  at the same interval, so a rule whose zapret stops working moves to its
  servers sooner. A change goes to every router with zapret rules at once
  (sing-box reloads in place) and with every later zapret change (agent
  0.29.1, manager 0.42.1).

## 1.55.0 — 2026-11-20

zapret2 in front of a rule's servers (manager 0.42.0, agent 0.29.0).

- **Zapret first**, a switch on a rule's page (Routing → Rules, proxy rules):
  pick the rule's service (YouTube, Meta, X, Telegram, Discord or Other:
  each brings its own check addresses, which can be edited) and turn it on.
  The first time, nfqws2 is installed on the router from bol-van/zapret2's
  latest release (checked against GitHub's SHA-256 of the release file and
  the release's sha256sum.txt, the router's CPU only, about 0.4 MB of flash
  in /etc/podkop-zapret). zapret (the first version) is not used: it is no
  longer developed. No menu of its own: the rules list tags a rule "zapret"
  or "zapret → servers", Overview shows "direct, through zapret".
- **A zapret rule's web traffic goes out direct first**: sing-box gets a
  direct outbound with the rule's own mark and a URLTest of it whose
  fallback is the rule's usual servers; TCP 80/443 sniffed as TLS or HTTP
  (and QUIC, which is refused there so apps use TCP) goes to it, everything
  else the rule takes (other ports, other UDP: WhatsApp chat and calls,
  Discord voice) stays on the servers. When the rule's check address fails
  through zapret, the rule uses its servers until it passes again (on
  openwrt-reserv: servers after 41 s, back after 46 s). nfqws2 sees only the
  first packets of those connections; they then carry podkop's own mark, so
  addresses in podkop's subnet lists don't loop back into sing-box. After a
  podkop restart the daemon puts it back. Kill-switch and protected rules
  are left alone.
- **Strategy search** (turning it on, or "Find the best strategy again"):
  the router tries each check address without zapret, then each strategy
  of the list with a fresh nfqws2, and keeps the fastest of the first three
  that open every address (any other working one can be picked by hand); a
  rule where none works stays on its servers. The list is data (Settings →
  Zapret strategies, all routers; defaults from zapret2's docs and
  blockcheck2 and Flowseal's lists ported to nfqws2), not code. On
  openwrt-reserv's line YouTube, Meta and X open through zapret2; Telegram
  is blocked by address there and stays on its servers.
- **Alerts**: "zapret does not open its sites now, the rule uses its
  servers" and "back on zapret" (History, notifications), once a move has
  held for two daemon rounds. sing-box tests a URLTest only while
  connections use it, so the agent tests each zapret path itself about once
  a minute: a broken path is left before the next connection needs it.
- **`sections`** (the OpenVPN server's per-rule traffic) names a zapret
  rule's web traffic after the rule, not "<rule>-zapret".
- **Where does it go?** names the zapret rule and says whether it goes out
  through zapret now.

## Manager 0.41.1 — 2026-11-20

Only the manager (no new router files, so no release of its own).

- **A rule's servers from the probe** ("Matches now"): each server's exit
  country with its flag, and a click on "Median" sorts the table by ping
  (another click puts back the picked order, the one podkop gets). The
  servers left out show their flag too.

## 1.54.0 — 2026-11-19

Devices, "where does it go", things worth a look, services in the probe, a
support report (manager 0.41.0, agent 0.28.0).

- **Devices** (Routing → Devices): the router's devices (DHCP leases, fixed
  leases, neighbours on podkop's interfaces), each going by the rules,
  entirely through one rule, or never through podkop. The page edits
  podkop's own "Fully routed IPs" and "IPs never routed" in the draft; a
  device with an address from DHCP is flagged.
- **Where does it go?** (Rules): the router answers which rule takes a name
  or an address, by which list, the rule's server now and its exit, from
  the rules sing-box runs, in order, with sing-box's own `rule-set match`.
  Block and exclusion rules and device routes are read too. Community lists
  are downloaded to RAM once a day for this (no new download starts after
  40 s, so a first question can take about two minutes); a list that could
  not be read is named in the answer.
- **Worth a look**: Check and Review and apply list what podkop accepts but
  is probably not meant (a domain twice, a domain that an earlier rule
  takes, a community list in two rules, fallback servers that repeat the
  rule's own, an empty address list, a left-over outbound). Review and
  apply shows only those of the rules it changes; none stops an apply.
- **Services** in a rule's probe parameters (probe 1.54 `--unlock`): Netflix
  (its whole catalogue, or only its own titles), ChatGPT and Claude (the
  country their edge sees, against a short fixed list of countries where
  they are not offered: RU BY CN HK IR KP SY CU). Servers a rule cannot use
  for them are left out. Needs agent 0.28 and probe 1.54 on the router. Tried on openwrt-reserv's 13 servers: every fast one opened the
  whole Netflix catalogue; Claude saw one of them in another country than
  ChatGPT did.
- **Support report** (Logs → Support report): a text file with servers as
  host-N and ids, keys, passwords and subscription addresses taken out.
- **sing-box's open files** are in the status; above 60% of the limit an
  alert (podkop issue 460: DNS stops for the whole network when they run
  out), cleared below 40%.
- **Kill switch DNS in a crash loop**: the NXDOMAIN file of kill-switch
  rules came in and out with every crash of sing-box, and each change
  restarts dnsmasq, the DNS of the whole network. After a second fall within
  5 minutes it now stays until sing-box has run for a minute. The agent's
  own podkop restarts (an apply, its undo) do not count.
- **Subscription addresses** are no longer sent to the browser: the
  interface shows sub.example.com/s/…k9Q, and a masked address sent back is
  refused.
- **Kill switch and Protected switches** send one rule's change; the
  manager applies it to the router's own list, one request at a time, so
  two switches flipped quickly no longer undo each other.

## 1.53.0 — 2026-11-18

Podkop sections for a VPN server (agent 0.27.0; the manager is unchanged).

- **`sections`** (a new agent command): a VPN server in front of the router
  (its clients all reach the router from one address) asks which podkop
  section a domain or an address belongs to, and counts the traffic of each
  of its own clients by section itself. The agent learns the answers from
  sing-box's open connections on its usual 15 s traffic poll: domain →
  section, the FakeIP address the router's DNS gives that domain, and real
  addresses that reached a section through podkop's address lists. Kept in
  RAM (`/tmp/podkop-agent/sections/map.json`), up to 4096 names, 7 days.
- The answer holds only the names and addresses that were asked, the list of
  sections, the agent's version, podkop's address list on request
  (`"subnets": true`) and an id of the FakeIP addresses: they are given out
  again after a reboot or when sing-box starts a new cache file, so the id
  changes then, the agent asks the router's DNS for every known name again
  and the server forgets what it knew.
- **A key that can ask nothing else**: in `/etc/dropbear/authorized_keys`,
  `restrict,command="/usr/bin/podkop-agent --sections-only" <key>` runs
  `sections` whatever the client sends.
- The upgrade test for a running connection check no longer depends on the
  machine's speed.

## 1.52.0 — 2026-11-13

A rule stuck on a dead server, devices with their own DNS, the clock after
a power cut, and a watch over the manager itself (manager 0.40.0, agent
0.26.0).

- **A rule that keeps a dead server**: sing-box picks a server again only
  after a test round of the group, and a round that never ends (a known
  sing-box fault, podkop issue 428) keeps the dead choice for hours while a
  working server is there. When a group's choice does not answer and another
  of its servers does for 2 minutes, the agent asks sing-box to test the
  group; if the choice is still the dead server 2 minutes later, podkop is
  restarted, once, with an alert (at most 3 restarts an hour, as before).
- **Devices' own DNS** (Connection health, off by default): a rule by domain
  works through the router's DNS, and a device that asks another DNS server
  reaches the site directly, past the rule and its kill switch. The option
  writes three firewall sections: plain DNS from the LAN is answered by the
  router, DNS over TLS is refused, DNS over HTTPS to the well-known public
  resolvers is refused. IPv4, the lan zone; a resolver that is not in the
  list still gets through, and a phone with a fixed Private DNS name loses
  its internet until that is put back to automatic.
- **The clock after a power cut**: a router has no battery clock, and until
  NTP answers TLS to the servers fails and every rule looks dead. The agent
  and podkop-sentinel judge no server before NTP has set the clock (a small
  hotplug script leaves a mark in RAM), or 15 minutes after the boot where
  there is no NTP. The overview warns when the router's clock is more than 5
  minutes off this server's.
- **The manager watches itself**: a hung manager sent no alerts and nothing
  noticed. A stalled event loop now ends the process after 3 minutes and
  Docker starts it again (`PODKOP_MANAGER_WATCHDOG=0` switches that off);
  after a start a notification says for how long the manager was not
  running; `PODKOP_MANAGER_HEARTBEAT_URL` is requested every minute, for an
  uptime monitor that raises the alarm when the requests stop (the only one
  of the three that covers a server that is off).

Looked at in NetShift and vpnpool and left out: a QUIC block (not wanted);
leaving a pinned server that is dead (a pin is the user's choice here); a
support report with secrets removed, a "where does this request go" check
and per-server unlock tests (features of their own, not reliability).

## 1.51.0 — 2026-11-12

Kill switch: the first minute after a boot, and proof that it blocks
(manager 0.39.0, agent 0.25.0).

- **From the first second after a boot**: forwarding works as soon as the
  firewall starts, podkop and the agent start about a minute later, and
  until then a kill-switch rule's traffic went out direct. The agent now
  keeps a copy of the block list on the router and a small boot script
  (podkop-guard) loads it before the firewall and dnsmasq start. The copy
  is written only when the rules' settings or the sizes of their lists
  change, not at every hourly resolve (flash).
- **Counters**: Connection health shows how many packets the block list
  refused since it was last loaded.
- **Put back when changed**: the table was only checked for being there;
  now its contents are compared with what was loaded every 15 s, and a
  table that something emptied or changed is loaded again (an event says so).
- A warning when flow offloading is on in the router's firewall (offloaded
  connections go past the firewall's chains).
- Fixed: switching a kill switch on or off while the router was busy with
  an apply or a self-heal reload answered "busy" but had already saved the
  list; the next try then saw nothing to change and did not load it into
  sing-box. The list is now saved only once the change can be loaded.

Looked at and left out: blocking the router's own traffic to those
addresses (RouteGuard does): with podkop the router's DNS and list
downloads go out direct on purpose, and a kill switch on a rule with a CDN
list would cut the router's own DNS. A leak test from the router: the block
is on forwarded traffic, which the router cannot send to itself without an
extra kernel module; the counters show real blocks instead.

## 1.50.0 — 2026-11-12

A rule that keeps working when the agent does not: protected rules, safe
mode, podkop-sentinel (manager 0.38.0, agent 0.24.0).

- **Protected** (a switch on a rule, e.g. main): podkop-agent leaves the
  rule exactly as podkop builds it. Nothing is borrowed into it, none of
  its servers is kept out, and a sing-box config in which its servers, its
  routing, the final route or DNS differ from podkop's own build is not
  written, whatever part of the agent asked for it.
- **podkop-sentinel**: a small watcher of its own on the router, apart from
  the agent (an agent update replaces it only when its text changes). It
  restarts an agent daemon that gives no sign of life for 10 minutes (at
  most 3 times an hour). When a protected rule does not answer for 5 checks
  in a row while the provider's line is up, it tries **safe mode**.
- **Safe mode**: the router runs plain podkop. What the agent added at run
  time is taken out, podkop is restarted once, the agent's daemon idles
  and refuses applies, pins and probes; reading, logs, podkop's start and
  stop and an agent update still work. Safe mode entered by the sentinel
  stays only if the protected rules work with it within 6 minutes;
  otherwise the agent is back and the next try comes 6 hours later.
  Overview shows it with a "Leave safe mode" button; an alert and a
  notification are sent. While it is on, a kill switch does not close on
  dead servers and its DNS block is not raised (its firewall table stays).
- **An agent update is watched**: the installer waits 90 s after the
  restart; a daemon that stopped giving its sign of life, or a sing-box
  that stopped, puts the old files back.
- The update dialog says whether another router has run this agent version
  for a day without trouble.

## 1.49.0 — 2026-11-12

Less load on the router (manager 0.37.0, agent 0.23.0). Measured on a
dual-core router; what it does stays the same.

- **Status**: one call costs 0.43 s of the router's CPU instead of 0.73 s,
  and the manager asks every 2 minutes while nobody has the web interface
  open (every 30 s while someone does; events still arrive at once).
- **Connection checks** (each runs a sing-box of its own on the router, 3-6 s
  of its CPU): while a check finds nothing wrong the next one comes 5 times
  later (at most every 5 minutes); after any failure, at the set interval
  again.
- **The daemon**: podkop's settings are read again only after their file
  changed (not every 15 s), sing-box's API is asked directly (0.02 s
  instead of 0.14 s through podkop's script), "is podkop enabled" starts
  nothing, a round of server tests reads the groups from a small copy
  (0.78 s instead of 1.3 s with two groups down). Together: the daemon took
  4.2% of one core with two groups down, now 2.8%.
- The event stream starts a program only after an event, not every second.
- **Fixed**: Connection health always reported a DNS-over-HTTPS resolver as
  failing: the check of its answer used `od`, which OpenWrt does not have.

## 1.48.3 — 2026-11-12

The router's watchdog stood still (manager 0.36.1, agent 0.22.1).

- **Fixed: podkop-agent's daemon stopped about 35 s after every start**, on
  every router, since 1.38.0: its first check of the servers waited for the
  daemon's own service watcher, which never ends. Until its next restart
  nothing restarted podkop after a crash, no group was healed, a kill
  switch was not closed when its servers died, DNS was not blocked while
  podkop was down, and traffic per server was not counted. (The kill
  switch's block table stayed in place.) Update the agent on every router.
- **The manager says so** when a router's daemon gives no sign of life for
  5 minutes: an alert on the router, and a notification.
- Leftovers of killed commands are removed at the daemon's start and every
  hour: sing-box processes of connection checks (20 MB of RAM each) and the
  folders of checks and exit-IP lookups.

## 1.48.2 — 2026-11-11

VPN rules without a podkop restart, checked before apply (manager 0.36.0,
agent 0.22.0).

- **No podkop restart** for a rule that matches by its own domains only:
  adding it, removing it, its VPN interface and its DNS server now reload
  sing-box in place (before, every such change stopped everything for
  20-60 s).
- **Checked before apply**: the VPN page tries each VPN rule's first domain
  through the tunnel on the router, with the settings of your draft: "opens
  through the VPN", or why not (the name is not found, no answer). When the
  VPN's own DNS server would fix it, the page says so.
- A new VPN rule takes the VPN's DNS server at once when the VPN offers
  one.

## 1.48.1 — 2026-11-11

VPN fixes (manager 0.35.1, agent 0.21.1).

- A config without a login was never written to the router (the tunnel
  could not start).
- Routes and DNS options the server pushes are dropped quietly: no more
  dozens of "Options error: option 'route' cannot be used" lines at every
  connect (they were harmless; the routing table is the same).
- Logs: OpenVPN's lines have their own source, **vpn**, not "podkop".
- A new password alone did not restart the tunnel: it went on with the old
  one.
- A wrong password was tried again every 10 s for ever (an account lockout
  on a company VPN): OpenVPN now stops after a few failed starts and the
  VPN page says so. After other fatal errors the agent starts it again
  every 5 minutes.
- **Devices** are one list for the tunnel, not per rule: renaming a rule
  dropped its limit. Lists saved per rule still work and become the
  tunnel's at the next Save.
- **Alerts**: "VPN is down" after a minute without the tunnel and "VPN is up
  again" (events, the router's line, Telegram/webhook).
- A tunnel whose gateway answered and then is silent for 45 s reconnects at
  once, not after the server's own timeout (often 2 minutes of hanging
  sites).
- The VPN page shows the DNS server the VPN offers and sets it as the
  resolver of the VPN rules in one click (names that exist only inside that
  network).

## 1.48.0 — 2026-11-11

OpenVPN for some sites (manager 0.35.0, agent 0.21.0).

- **VPN page**: install OpenVPN on the router (through a working server when
  the package server is cut off), import a `.ovpn` config with login and
  password (the password is kept on the router only), turn the tunnel on and
  off, see its state and log.
- VPN rules (interface `pkvpn`) can be limited to some devices; the others
  go on as if the rule weren't there.
- The tunnel takes no routes from the server; while it is down the rules'
  sites fail instead of going out direct.

## 1.47.0 — 2026-11-11

Web interface and login (manager 0.34.0).

- Deleting the rule shown first left it in the list with a dead editor.
- Two quick clicks on a rule's keys saved only the second; clicks are shown
  at once and saved in order.
- History reloaded at every status of the router when scheduled changes
  were not in order of their numbers, and a reload closed "Show older".
- The apply dialog span for ever when the router never told how a change
  ended; a job that ends after "Close, keep going" no longer redraws the
  page you moved to (and wipes what you were typing).
- Logs: a slow answer to an older filter no longer replaces the newer one.
- Errors that showed nothing now show a message (Discard, removing a rule's
  probe settings, History, "Let the manager run it").
- An edit made in the last half second before a reload or closing the tab
  is saved on the way out.
- Lighter pages: Connection health no longer reloads 60 reports at every
  status of the router; Rules no longer fetches 50 versions and the whole
  probe result it does not show; the apply dialog asks for a change's state
  without its two full configs; Settings asks for the backups once.
- **Fonts are served by the manager**, not by Google: the page opens at once
  when the browser cannot reach it (a broken proxy, which is when you open
  the manager).
- **Login**: wrong tries sent at the same time each saw an empty count, so
  the limit of 10 per 10 minutes could be passed; behind the TLS proxy the
  limit is per client, not for everyone at once. Log out ends the session
  on the server, so a copied cookie stops working (everyone logs in once
  more after this update). `/health` tells only whether it is up.
- A draft with a malformed section is refused instead of breaking the
  router's pages until it is discarded.

## 1.46.0 — 2026-11-11

Speed (manager 0.33.0, agent 0.20.0).

- **Database**: every log line, traffic row and setting waited for the disk
  (`synchronous=FULL`); it is `NORMAL` now (safe in WAL mode). A backup
  copied the database while holding the lock every request needs, freezing
  the whole service for the copy; it reads through its own connection. The
  hourly clean-up of old log lines has an index. The start no longer reads
  every stored log line. Removing a router is one transaction.
- **Pages ask the router less**: the settings are served as the status poll
  last read them while the router's fingerprint is the same (an apply still
  reads them fresh), the probe's status at most every 20 s. Opening Overview
  made two ssh round trips each time, and again after every event.
- A connection check no longer reads the router's settings every minute.
- **A router that is away fails fast**: requests queued behind a failed
  connect (15 s each) get its answer at once for 5 s, and a half-open
  connection is dropped at the call's own timeout instead of after ssh's
  keepalive, so pages and the scheduler do not hang on it.
- Keys and usage: the last probe's rows are parsed once per run, not every
  minute per router; an incident's context is built only for a new incident,
  not for every failing log line.
- **Agent**: the Clash API's address and secret are read from sing-box's
  config once per config instead of twice per request (a self-heal round
  makes a dozen requests against a config of hundreds of kB), and the
  settings' fingerprint once per round.

## 1.45.0 — 2026-11-11

Failures nobody saw become alerts, and alerts can reach you (manager 0.32.0).

- **Notifications** (**Settings → Notifications**): a Telegram bot (token
  and chat id) and/or a webhook (JSON POST). Sent when a rule loses all its
  servers or gets them back, a kill switch closes or opens, podkop goes down
  or comes back, the watchdog acts, a change is reverted or not applied, a
  probe fails, or a router is out of reach for 5 minutes. Tried again after
  30 s, 2 min and 10 min; optional daily summary at 9:00; "Save and send a
  test" checks the channel.
- A change sent by the nightly probe or a schedule that the router
  rejected, or that never answered, raised nothing (it showed only in
  History): `change_not_applied`, shown next to the router's name.
- A failed nightly probe, and a night when the router was away for the whole
  hour, were silent: `probe_failed`, `probe_not_started`. A schedule at
  23:xx keeps its hour past midnight.
- An apply or a probe start the router did not answer within 30 s was
  recorded as failed although the router may have started it: the change is
  now "unknown" (look at the router), a started probe is followed.
- A scheduled change waits out its grace hour also when the router drops
  between reading its settings and the apply.
- A probe whose result the manager could not read stayed "running" for
  ever; it ends as failed with an event.
- Self-heal: a rule that went down while a self-heal probe ran was dropped
  from the next one and got the first one's result applied without its
  countries having been tested; it now gets a probe of its own. The rules of
  a run, and where it ran, are stored, so a manager restart applies the
  result to the right rules.
- The periodic connection check no longer replaces its last result with
  "busy" while a probe runs; one malformed message of a router no longer
  stalls its event stream; a wake during a status read is not lost.

## 1.44.0 — 2026-11-11

Agent bugs from the review (agent 0.19.0).

- **Exit IPs of several rules at once** shared one temp file, so rules could
  show each other's exit IP or "no answer"; each lookup has its own files
  now, two run at a time (each is a sing-box of its own).
- **podkop-probe's own apply** (cron or by hand, not through the manager)
  always ended with "podkop-agent is busy": BusyBox `flock` has no `-w`.
- An apply killed during a reload in place left its job in "reloading" for
  ever; it is finished like the other states now.
- **Self-heal state**: a check that had started before the manager lent
  servers wrote its older state over them, so the lent servers counted as
  the rule's own and never expired. State is read and written under the
  apply lock, through a temp file.
- **Pins**: a pin with a wrong tag, or with the Clash API silent, removed
  the pin there was. The file is written once, after the switch worked.
  A pin that cannot be set is tried 4 rounds, not every 15 s for ever.
- **Recovery after a power cut**: with the provider's line still down the
  watchdog replaced podkop's settings with the last-good ones (possibly
  older than the last probe or a LuCI change) and left them there. It no
  longer does with the line down, and puts the settings back when the
  last-good ones do not start either.
- An expired eligibility lease no longer reloads sing-box every minute
  while the provider's line is down.
- `status`, `probe-status` and the traffic counters passed servers and probe
  rows as one command argument (128 KB at most): with a few hundred servers
  the answer came back empty. They go through files now.
- `watch` cleans up its `logread` and readers when the ssh session drops.
- The list update self-heal starts no longer holds the apply lock.

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
