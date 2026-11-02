# podkop-manager

A web interface on a Linux server for [podkop](https://github.com/itdoginfo/podkop)
on one or more OpenWrt routers: rules and settings, a nightly test of your VPN
subscriptions ("keys"), saved configs, backups, logs and a history of every
change. The probe needs
[podkop-engine](https://github.com/FiyeroT/podkop-engine) on the router.

```
server: podkop-manager ── SSH, a key that may run only podkop-agent ──▶ router: podkop-agent
  web UI, schedule, history                                            checks, applies, undoes,
                                                                       watchdog, pins, probe
```

Every change is checked before podkop is touched. If podkop doesn't come up
with it, the router puts the old settings back within about 30 s, even when
the manager is offline.

## Install on the server

Needs Docker with the compose plugin.

```sh
git clone https://github.com/dkagramanyan/podkop-manager
cd podkop-manager
cp .env.example .env && chmod 600 .env   # set your login and password in it
docker compose up -d --build
```

Open `http://<server>:8080`. Everything you set lives in `.env`: the port,
the web login and password. After changing
it: `docker compose up -d`. Update: `git pull && docker compose up -d --build`.
Data (database, SSH keys) is in the `podkop-manager_data` volume. Logs:
`docker compose logs -f`.

## Add a router

**Settings → Add router** shows a command: run it on the router as root
over SSH. It installs podkop-agent with a watchdog,
podkop-probe and the manager's key (allowed to run only podkop-agent), and
optionally closes podkop's Clash API (port 9090) to the LAN, except the
computers you list, and adds the manager's forwarding-only key (to test
servers on the manager's server). At the end it prints a line
`podkop-router <address> <port> <host key>`: paste it into Add router, which
checks that the manager reaches that router with its key. No password is
needed; **Reinstall agent** shows the command again. podkop itself is not changed: its
settings are saved in **Configs**, and the keys and lists of a podkop-probe
that already runs there are taken into **Keys and usage** and the rules.
**Settings → Remove** undoes all of it.

## Good to know

- Changes collect in a draft until **Review and apply**.
- A **Fastest (URLTest)** rule can take **Servers from keys**: every
  server of the subscriptions you pick (e.g. one provider), plus your own
  servers if you add some. When a provider changes its servers, the manager
  applies the new list by itself and keeps your own.
- A **Fastest (URLTest)** rule can **Take links from probe**: region,
  protocol, exit IP, name, ping, speed, YouTube and how many to keep, from
  the rule's keys if it has some. Its
  servers are refreshed after every probe run; **Run probe** is in the rule.
- **Nightly probe** (time, apply by itself, TLS fingerprint, where servers
  are tested): in every rule that takes links from the probe, under its
  parameters. It is one run for all those rules, so the settings are shared.
- **Test servers on this server, through the router** (a rule's Nightly
  probe): the router only reads the keys and builds the servers with
  podkop's own parser; the manager's container tests them, and every test
  connection goes out through the router over SSH, so the servers still see
  the router's address and the router's processor stays free. Add the line
  it shows to the router's `/etc/dropbear/authorized_keys` once: it lets the
  manager's second key forward connections and nothing else (no shell, no
  commands). Servers that use UDP (hysteria2, TUIC) can't be tested this way.
  The manager downloads podkop-engine for this by itself once, after it
  starts (~13 MB, kept in the data volume).
- **Overview → Now routing**: each rule's server now and where its traffic
  leaves to the internet (country and exit IP).
- **Configs**: save a router's config (podkop plus each rule's probe
  parameters), download it, import a file (one from here or podkop's
  `/etc/config/podkop`) and load any saved config into a router's draft.
- **Settings → Backup**: the whole manager in one file (routers, keys, rules,
  configs, history, SSH keys), and **Restore…** on a new server. Keep the file
  private.
- **Keys and usage**: add subscriptions and single links here, and see each
  key's traffic against its limit (from the provider or your own). At 90% it warns, or moves the probe's rules to other keys
  until the reset.
- **Connection stability:** the agent adds HTTP/2 pings to gRPC servers
  (15 s idle, 15 s ping timeout) and preserves established connections when
  URLTest changes its selected server. A latency improvement should not abort
  a healthy signup, upload or stream. These pings detect unresponsive gRPC
  transports; they do not guarantee a timeout for every stalled request.
- **Probe selection:** eligible servers are ranked GOOD, OK, then FLAKY.
  Within each class, picks spread across subscription keys, taking the lowest
  median latency within each key. Counts continue into fallback selection.
  Different keys may still belong to one provider: this is key diversity,
  not a guarantee of independent infrastructure. Explicit key filters remain
  in force. Duplicate links differing only in their display name count once.
- **Request errors:** Logs stores transport failures. With the router's Log
  level set to `info`, the manager can attach destination and outbound context
  from earlier lines with the same process and connection IDs. Context is
  bounded to 4096 connections per router and expires after 10 minutes without
  an update. Search Logs for a domain or connection ID. Enable info while
  reproducing a problem, then return to `warn` to reduce logging volume.
  HTTPS paths, response bodies and browser-only failures are not visible to
  the router. Use browser DevTools Network alongside Logs for those errors.
- To stop podkop, use **Stop**: otherwise the watchdog starts it again within
  about 30 s (**Disable** in LuCI turns that off).

## Connection health (manager 0.19, agent 0.8)

**Connection health** stores router errors and Kuma notifications alongside
the selected servers, configuration fingerprint, last DNS results, WAN state,
load and available memory. Network failures received within 30 seconds are
grouped into an incident; every observation remains available. An incident's
end is its last observed failure, not a measured recovery time. Router log
timestamps and Kuma's reported timestamps are retained, while grouping uses
Manager receipt time. HTTP 401/403/429 responses are access failures and never
trigger automatic proxy exclusion.

Start with **Check now**, then enable monitoring. Each round checks two
independent HTTPS destinations directly and through up to four server
configurations. Selected groups, backups and excluded nodes rotate through
the checks. Every five minutes, bounded 64 KiB downloads from two destinations
also test for stalled transfers; a detected stall is checked again next round.
This is a small availability test, not a bandwidth benchmark. Full speed tests
remain in the nightly probe.

The checks run in one temporary sing-box process on the router, with local-only
listeners, bounded request times and automatic cleanup. They exercise the
router's network and the real server configuration, including UDP transports,
but do not exercise LAN interception or an application's existing connection.
Keep Kuma's application checks for that view. Diagnostics require curl, jq,
BusyBox timeout, and at least 48 MiB of available memory; unsupported or failed
collectors are reported as such, never counted as failed proxies.

DNS is shown separately: resolution through Podkop, a direct primary-resolver
probe (UDP or DoH with an ordinary direct route), and the DNS offered by the
WAN provider. Other primary DNS transports/routes show **Not checked**. These
tests do not change DNS settings or bypass certificate verification. A cached
answer through Podkop alone is not proof that its upstream resolver works.

**Temporarily exclude repeatedly failing proxies** is optional and off by
default. It requires two consecutive paired failures, working direct controls
and router DNS, and a recently verified replacement in the same URLTest group.
The default cooldown is ten minutes, followed by two successful recovery
checks. A failed download needs successful download checks to recover. Unknown
results and access rejections do not count as transport failures. User pins,
pending drafts and configuration changes are respected.

Exclusions affect runtime URLTest membership; the original UCI server lists
stay intact. The agent checks and reloads the generated configuration, restores
the previous file on failure, and never restarts Podkop for these decisions.
If Manager disappears, a three-minute lease expires and the router rebuilds
the original memberships. Restoring a lease is retried at most once a minute
if it fails. Changes that require a reload can affect active sessions; retain
the existing connection-preservation settings and validate on one group first.

Probe parameters now include **Selection priorities**: browsing favors
latency, interactive favors response consistency (p90), and streaming favors
download speed. Reliability class remains first, with recent confirmed
failures considered within a class, and subscription-key diversity retained.
Independent infrastructure is still something to verify with the provider.

To correlate Kuma events, create a webhook URL in Connection health. In Kuma,
add a **Webhook** notification using POST and the default JSON body, then
attach it to the relevant monitors. Token replacement revokes the old URL.
Webhook events are observations only: even a replayed notification cannot
change routing. Notifications and checks are retained for 14 days; recent
per-server scores cover at most 120 samples in the last 24 hours.

For a useful baseline, keep separate Kuma monitors for the router's LAN
address, two independent internet destinations verified to take the direct
route, DNS, and the actual applications through Podkop. Start with two retries
before notifying and keep raw failures/latency visible; retries delay alerts.
Keep LinkedIn's 403 as an application-access failure rather than treating it
as a dead proxy. Use the existing draft/apply flow to trial one-minute URLTest
checks in one section, then compare failures, response times and router load
over 24–48 hours before broadening the change.

When upgrading from a source checkout, install both `router/podkop-agent` and
`router/reliability.sh` (the latter at
`/usr/share/podkop-agent/reliability.sh`), then restart **podkop-agent**. The
packaged installer and release include both. Preserve the existing agent's
keys and firewall rules. An older agent gives an explicit unsupported-command
error and continues its existing watchdog behavior.

## podkop-probe without the manager

Tests every server of your keys on the router and puts the best into podkop:
10 fastest into section `main`, 10 fastest where YouTube sees Russia (no ads)
into section `youtube`, each with 5 fallback servers. On the router:

```sh
sh <(wget -O - https://github.com/dkagramanyan/podkop-manager/releases/latest/download/probe.sh)
```

1. **Enter**, paste your keys (one per line), then an empty line.
2. **Enter** for "Europe only". Wait 2–7 minutes.
3. Tables: `*` = main, `y` = YouTube, `f` = fallback. **Enter** puts both
   lists into podkop and restarts it (`1` only main, `2` only YouTube,
   `3` other sections, `n` nothing).
4. "Repeat this every night?" **Enter** installs `podkop-probe` with your
   keys. Then in LuCI → **System → Scheduled Tasks** add
   `30 4 * * * podkop-probe --cron`.

Each night it updates itself, tests the keys and updates podkop; if podkop
doesn't start with the new lists, the old settings stay. Same servers as
before: nothing is written to the router's flash and podkop isn't restarted. Log:
`/tmp/podkop-probe.log`. Remove: `podkop-probe --uninstall`.

- Never picked: ping 700 ms or more, or 40 Mbit/s or less
  (`--max-ping`, `--min-speed`).
- Servers are built by podkop itself: vless, trojan, ss, hysteria2 and socks
  over tcp, ws, grpc or xhttp. Others (vmess, ...) are skipped.
- "Europe only" checks the name, the server IP and the exit IP, so relays
  "через 🇷🇺" are skipped.
- "Device limit reached": free a device in your provider's bot.
- Backup before every change: `/etc/config/podkop.probe-backup.<date>`.
- `-v` shows every server and why it failed; `-h` lists all options.

## Layout

- `podkop_manager/`: the server (Python, FastAPI) and its web interface
- `router/`: files installed on the router: `podkop-agent`, its init script,
  `install.sh`, `probe.sh`
- `tools/sub-check.sh`: what a subscription returns to each client app
- [CHANGELOG.md](CHANGELOG.md)

## Коротко по-русски

Веб-интерфейс для podkop на сервере: `git clone`, `cp .env.example .env`,
впишите порт и пароль в `.env`, `docker compose up -d --build`, откройте
`http://<сервер>:<порт>` и добавьте роутер: выполните на роутере команду
из **Add router** и вставьте строку, которую она напечатает. Без сервера: выполните
команду из **podkop-probe without the manager** на роутере, вставьте ключи,
Enter — лучшие серверы попадут в podkop (`main`, `youtube` и по 5 резервных).
Каждую ночь: **Система → Планировщик**, строка `30 4 * * * podkop-probe --cron`.
