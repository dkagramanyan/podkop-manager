#!/bin/sh
# shellcheck shell=dash disable=SC2015
#
# Installs podkop-agent on the router (run as root on the router; podkop-manager
# → Add router shows the whole command):
#
#   sh install.sh --key 'ssh-ed25519 AAAA... podkop-manager' [--forward-key 'ssh-ed25519 AAAA...']
#                 [--allow-clash IP ...] [--keep-clash-open] [--release TAG | --src DIR]
#   sh install.sh --upgrade [--release TAG | --src DIR]
#   sh install.sh --uninstall
#
# At the end it prints a line to paste into podkop-manager: the router's
# address, SSH port and SSH host key, so the manager knows it is this router.
#
# --key          the manager's public key; it may run only podkop-agent
# --forward-key  the manager's second key; it may only open connections
#                through the router (for testing servers on the manager's
#                server), nothing else. Without it such a key is removed.
# --allow-clash  computers that may still open podkop's Clash API (port 9090,
#                e.g. for the dashboard in LuCI); the rest of the LAN is blocked
# --keep-clash-open  leave the firewall as it is (the Clash API stays open to the LAN)
# --release TAG  take podkop-agent, podkop-agent.init and probe.sh from this
#                release (the manager's own version) instead of the latest
# --src DIR      take them from DIR
# --upgrade      update an existing agent, preserving keys, firewall and settings

REPO_URL="https://github.com/dkagramanyan/podkop-manager"
RELEASE="$REPO_URL/releases/latest/download"
KEYS=/etc/dropbear/authorized_keys
MARK=podkop-agent
FWD_MARK=podkop-agent-forward
KEEP="/usr/bin/podkop-agent /etc/init.d/podkop-agent /etc/podkop-agent/ /usr/bin/podkop-probe /usr/share/podkop-agent/ /etc/init.d/podkop-vpn /usr/bin/podkop-sentinel /etc/init.d/podkop-sentinel /etc/init.d/podkop-guard /etc/hotplug.d/ntp/90-podkop-agent"

set -u
KEY=""; FWD_KEY=""; ALLOW=""; SRC=""; UNINSTALL=0; BLOCK=on; UPGRADE=0; PAIRING=0; PRINT_SENTINEL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --key) KEY=${2:-}; PAIRING=1; shift 2 ;;
    --forward-key) FWD_KEY=${2:-}; PAIRING=1; shift 2 ;;
    --release) RELEASE="$REPO_URL/releases/download/${2:-}"; shift 2 ;;
    --allow-clash) ALLOW="$ALLOW ${2:-}"; PAIRING=1; shift 2 ;;
    --keep-clash-open) BLOCK=off; PAIRING=1; shift ;;
    --src) SRC=${2:-}; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --upgrade) UPGRADE=1; shift ;;
    --print-sentinel) PRINT_SENTINEL=1; shift ;;
    --print-guard) PRINT_SENTINEL=2; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }

# --- podkop-sentinel ---------------------------------------------------------------
#
# A watcher of its own, apart from podkop-agent: a small script that an agent
# update replaces only when this text changes. It restarts an agent daemon
# that stands still, and for the protected rules ($ETC/protected) it falls
# back to plain podkop ("safe mode") when one of them stops working and
# plain podkop makes it work again. Written out by --print-sentinel too
# (the tests run it).
sentinel_text() {
  cat <<'SENTINEL'
#!/bin/sh
# shellcheck shell=dash
# podkop-sentinel: watches podkop-agent from outside. Installed by
# podkop-agent's install.sh; "safe mode" is described in podkop-manager.
SENTINEL_VERSION=2
ETC=/etc/podkop-agent
RUN=/tmp/podkop-agent
S=/tmp/podkop-sentinel
MARK=$ETC/safe-mode
TICK=${PODKOP_SENTINEL_TICK:-60}
BEAT_MAX=600      # s without the daemon's sign of life before it is restarted
FAILS=5           # checks in a row (minutes) a protected rule fails before plain podkop is tried
SETTLE=${PODKOP_SENTINEL_SETTLE:-90}     # s podkop gets to start before the rule is tested again
TRIAL=${PODKOP_SENTINEL_TRIAL:-360}      # s plain podkop gets to show that the rule works with it
BACKOFF=21600     # s before the next try after one that did not help
TEST_URL=https://www.gstatic.com/generate_204
TAB=$(printf '\t')

log() { logger -t podkop-sentinel "$*"; }
now() { date +%s; }
# the manager learns of it through the agent's events (when the agent can still do that)
event() { /usr/bin/podkop-agent _event "$1" "$2" </dev/null >/dev/null 2>&1 || :; }
enabled() { for _f in /etc/rc.d/S[0-9][0-9]"$1"; do [ -e "$_f" ] && return 0; done; return 1; }

# api <path> [curl -G arguments]: sing-box's Clash API
api() {
  _c=$(uci -q get podkop.settings.config_path); [ -n "$_c" ] || _c=/etc/sing-box/config.json
  [ -s "$_c" ] || return 1
  if [ ! -s "$S/api" ] || [ "$_c" -nt "$S/api" ]; then
    jq -r '.experimental.clash_api | [(.external_controller // ""), (.secret // "")] | join("\t")' "$_c" > "$S/api.t" 2>/dev/null &&
      mv "$S/api.t" "$S/api" || return 1
  fi
  IFS="$TAB" read -r _a _s < "$S/api"
  [ -n "$_a" ] || return 1
  _p=$1; shift
  if [ -n "$_s" ]; then curl -s --max-time 8 -G -H "Authorization: Bearer $_s" "$@" "http://$_a$_p"
  else curl -s --max-time 8 -G "$@" "http://$_a$_p"; fi
}

# rule_ok <section>: a request through the rule's outbound gets an answer
rule_ok() { api "/proxies/$1-out/delay" --data-urlencode "url=$TEST_URL" --data "timeout=5000" 2>/dev/null | grep -q '"delay":[1-9]'; }

# the provider's line answers (a rule that fails without it is nobody's fault here)
wan_ok() {
  _g=$(ip -4 route show default 2>/dev/null | awk 'NR == 1 { print $3 }')
  for _h in $_g 77.88.8.8 8.8.8.8; do
    case "$_h" in ""|*[!0-9.]*) continue ;; esac
    ping -c 1 -W 2 "$_h" >/dev/null 2>&1 && return 0
  done
  return 1
}

protected() { [ -s "$ETC/protected" ] && grep -v '[^A-Za-z0-9_]' "$ETC/protected"; }

# nothing is judged while podkop is off on purpose, the router just started,
# or an apply, an agent update or the probe runs
busy() {
  [ -f "$ETC/paused" ] && return 0
  enabled podkop || return 0
  [ "$(cut -d . -f 1 /proc/uptime)" -lt "${PODKOP_SENTINEL_BOOT:-300}" ] && return 0
  # the clock is not set yet (no battery clock: TLS to the servers fails until NTP answers)
  [ -f "${PODKOP_CLOCK_MARK:-/tmp/podkop-clock}" ] || [ "$(cut -d . -f 1 /proc/uptime)" -ge "${PODKOP_SENTINEL_CLOCK:-900}" ] || return 0
  for _l in install.lock apply.lock; do
    [ -e "$RUN/$_l" ] && ! flock -n "$RUN/$_l" true 2>/dev/null && return 0
  done
  [ "$(cat "$RUN/probe/state" 2>/dev/null)" = running ] && return 0
  return 1
}

# the agent's daemon writes $RUN/beat every round; one that stands still is
# restarted, at most 3 times an hour
check_daemon() {
  enabled podkop-agent || return 0
  grep -q 'RUN/beat' /usr/bin/podkop-agent 2>/dev/null || return 0      # an agent without the sign of life
  _b=$(date -r "$RUN/beat" +%s 2>/dev/null) || _b=$START
  [ "$_b" -ge "$START" ] || _b=$START
  [ $(( $(now) - _b )) -gt "$BEAT_MAX" ] || return 0
  _t=$(now)
  awk -v t="$_t" '$1 > t - 3600' "$S/restarts" 2>/dev/null > "$S/restarts.t"; mv "$S/restarts.t" "$S/restarts"
  [ "$(wc -l < "$S/restarts")" -lt 3 ] || return 0
  echo "$_t" >> "$S/restarts"
  log "podkop-agent's daemon gave no sign of life for more than $BEAT_MAX s: restarted"
  /etc/init.d/podkop-agent restart >/dev/null 2>&1
  START=$(now)
  event sentinel_restart '{}'
}

check_rules() {
  _bad=""
  for _r in $(protected); do
    rule_ok "$_r" || { sleep "${PODKOP_SENTINEL_RETRY:-2}"; rule_ok "$_r"; } || _bad="$_bad $_r"
  done
  if [ -z "$_bad" ] || ! wan_ok; then echo 0 > "$S/fails"; return 0; fi
  _n=$(( $(cat "$S/fails" 2>/dev/null || echo 0) + 1 )); echo "$_n" > "$S/fails"
  [ "$_n" -ge "$FAILS" ] || return 0
  [ "$(now)" -ge "$(cat "$S/backoff" 2>/dev/null || echo 0)" ] || return 0
  enter "${_bad# } did not answer for $FAILS checks in a row" trial
}

# enter <reason> [trial]: plain podkop. The agent's daemon idles while the
# mark is there; what it added at run time (all in RAM) goes: borrowed
# servers, servers kept out of their groups, the DNS block of kill switches.
enter() {
  mkdir -p "$ETC" "$S"
  printf '%s\t%s\n' "$(now)" "$1" > "$MARK.t" && mv "$MARK.t" "$MARK" || return 1
  log "safe mode: $1; podkop runs without podkop-agent's additions"
  /etc/init.d/podkop-agent stop >/dev/null 2>&1
  rm -f "$RUN/heal/state.json" "$RUN/heal/extra.json" "$RUN/eligibility.json"
  _dns=""
  for _f in /tmp/dnsmasq.d/podkop-agent-strict.conf /tmp/dnsmasq.*.d/podkop-agent-strict.conf; do
    [ -f "$_f" ] && { rm -f "$_f"; _dns=1; }
  done
  if enabled podkop && [ ! -f "$ETC/paused" ]; then /etc/init.d/podkop restart >/dev/null 2>&1
  elif [ -n "$_dns" ]; then /etc/init.d/dnsmasq restart >/dev/null 2>&1; fi
  if [ "${2:-}" = trial ]; then now > "$S/trial"; echo 0 > "$S/good"; else rm -f "$S/trial"; fi
  enabled podkop-agent && /etc/init.d/podkop-agent start >/dev/null 2>&1
  event safe_mode "$(jq -nc --arg r "$1" --arg t "${2:-}" '{reason: $r, trial: ($t == "trial")}')"
}

# leave <why> [seconds before the next automatic try]
leave() {
  rm -f "$MARK" "$S/trial" "$S/good"; mkdir -p "$S"; echo 0 > "$S/fails"
  [ -z "${2:-}" ] || echo $(( $(now) + $2 )) > "$S/backoff"
  log "safe mode off: $1"
  enabled podkop-agent && /etc/init.d/podkop-agent restart >/dev/null 2>&1
  event safe_mode_off "$(jq -nc --arg r "$1" '{reason: $r}')"
}

# follow: a safe mode entered by ourselves stays only if it helped
follow() {
  [ -f "$S/trial" ] || return 0
  _el=$(( $(now) - $(cat "$S/trial") ))
  [ "$_el" -ge "$SETTLE" ] || return 0
  _bad=""
  for _r in $(protected); do rule_ok "$_r" || _bad=1; done
  if [ -z "$_bad" ]; then
    _g=$(( $(cat "$S/good" 2>/dev/null || echo 0) + 1 )); echo "$_g" > "$S/good"
    [ "$_g" -ge 2 ] || return 0
    rm -f "$S/trial"
    log "safe mode stays: the protected rules work with plain podkop"
    event safe_mode_confirmed '{}'
  else
    echo 0 > "$S/good"
    [ "$_el" -ge "$TRIAL" ] || return 0
    leave "plain podkop did not help: the rule's servers do not answer" "$BACKOFF"
  fi
}

round() {
  [ -x /usr/bin/podkop-agent ] || return 0
  if [ -f "$MARK" ]; then follow; return 0; fi
  busy && return 0
  check_daemon
  check_rules
}

case "${1:-run}" in
  enter) enter "${2:-switched on by hand}" ;;
  leave) leave "${2:-switched off by hand}" 3600 ;;
  version) echo "$SENTINEL_VERSION" ;;
  run) mkdir -p "$S"; START=$(now); while :; do sleep "$TICK"; round; done ;;
  once) mkdir -p "$S"; START=${PODKOP_SENTINEL_START:-$(now)}; round ;;      # one round (the tests)
  *) echo "usage: podkop-sentinel run|enter [reason]|leave [reason]|version" >&2; exit 1 ;;
esac
# end of podkop-sentinel
SENTINEL
}
sentinel_init_text() {
  cat <<'SENTINEL'
#!/bin/sh /etc/rc.common
# podkop-sentinel: watches podkop-agent from outside (written by podkop-agent's install.sh)
# shellcheck disable=SC2034
START=99
USE_PROCD=1
start_service() {
  procd_open_instance
  procd_set_param command /usr/bin/podkop-sentinel run
  procd_set_param respawn 3600 5 0
  procd_close_instance
}
SENTINEL
}
# podkop-guard: at boot, before the firewall and dnsmasq start, the copy of
# the kill switch's block list that the agent keeps on flash (guard_boot_save
# in reliability.sh) goes in: until podkop and the agent are up, about a
# minute later, a kill-switch rule's sites are refused instead of going out
# direct. The agent's daemon takes over from there.
guard_init_text() {
  cat <<'GUARD'
#!/bin/sh /etc/rc.common
# podkop-guard: the kill switch's block list from the first second after a boot
# (written by podkop-agent's install.sh)
# shellcheck disable=SC2034
START=18
boot() {
  E=/etc/podkop-agent
  [ -s "$E/strict" ] && [ -s "$E/guard.nft" ] && [ ! -f "$E/paused" ] || return 0
  _on=0; for _f in /etc/rc.d/S[0-9][0-9]podkop; do [ -e "$_f" ] && _on=1; done
  [ "$_on" = 1 ] || return 0
  nft -f "$E/guard.nft" 2>/dev/null && logger -t podkop-agent "strict guard: block list loaded at boot"
  # the names (NXDOMAIN) only when the daemon will take them out again
  if [ -f "$E/safe-mode" ] || [ ! -s "$E/guard.dnsmasq" ]; then return 0; fi
  _c=$(uci -X show dhcp 2>/dev/null | sed -n 's/^dhcp\.\([^.=]*\)=dnsmasq$/\1/p' | head -n 1)
  _d=$(uci -q get "dhcp.$_c.confdir" | cut -d , -f 1); [ -n "$_d" ] || _d="/tmp/dnsmasq${_c:+.$_c}.d"
  mkdir -p "$_d" && cp "$E/guard.dnsmasq" "$_d/podkop-agent-strict.conf"
}
GUARD
}
guard_init_install() {
  guard_init_text > "$TMP/podkop-guard.init" && sh -n "$TMP/podkop-guard.init" 2>/dev/null || return 0
  put "$TMP/podkop-guard.init" /etc/init.d/podkop-guard
  /etc/init.d/podkop-guard enabled 2>/dev/null || /etc/init.d/podkop-guard enable 2>/dev/null
}

# clock_hook_install: NTP's hotplug event leaves a mark in RAM once the clock
# is set; the agent and the sentinel judge no server before that
clock_hook_install() {
  # shellcheck disable=SC2016
  printf '%s\n' '#!/bin/sh' "# podkop-agent: NTP has set the clock (written by podkop-agent's install.sh)" \
    '[ "$ACTION" = unsync ] || : > /tmp/podkop-clock' > "$TMP/clock-hook"
  mkdir -p /etc/hotplug.d/ntp && put "$TMP/clock-hook" /etc/hotplug.d/ntp/90-podkop-agent
  : > /tmp/podkop-clock      # (an install got here with the clock it has: the next boot waits for NTP)
}

# sentinel_install: written only when its text changed (flash), started then
sentinel_install() {
  sentinel_text > "$TMP/podkop-sentinel" && sentinel_init_text > "$TMP/podkop-sentinel.init" &&
    sh -n "$TMP/podkop-sentinel" 2>/dev/null || { echo "warning: podkop-sentinel was not installed" >&2; return 0; }
  _sch=0
  cmp -s "$TMP/podkop-sentinel" /usr/bin/podkop-sentinel || _sch=1
  cmp -s "$TMP/podkop-sentinel.init" /etc/init.d/podkop-sentinel || _sch=1
  put "$TMP/podkop-sentinel" /usr/bin/podkop-sentinel
  put "$TMP/podkop-sentinel.init" /etc/init.d/podkop-sentinel
  /etc/init.d/podkop-sentinel enabled 2>/dev/null || /etc/init.d/podkop-sentinel enable 2>/dev/null
  if [ "$_sch" = 1 ] || ! /etc/init.d/podkop-sentinel running >/dev/null 2>&1; then
    /etc/init.d/podkop-sentinel restart >/dev/null 2>&1 7>&- 8>&- 9>&- || echo "warning: podkop-sentinel did not start" >&2
  fi
}

if [ "$PRINT_SENTINEL" = 1 ]; then sentinel_text; exit 0; fi
if [ "$PRINT_SENTINEL" = 2 ]; then guard_init_text; exit 0; fi

if [ "$UPGRADE" = 1 ] && { [ "$PAIRING" = 1 ] || [ "$UNINSTALL" = 1 ]; }; then
  die "--upgrade cannot be combined with key, firewall or uninstall options"
fi

fetch() { # <name> <to>
  if [ -n "$SRC" ]; then cp "$SRC/$1" "$2"
  else wget -q -O "$2" "$RELEASE/$1" || uclient-fetch -q -O "$2" "$RELEASE/$1"; fi
}

firewall_rules() {
  uci -q delete firewall.podkop_agent_clash_allow
  uci -q delete firewall.podkop_agent_clash_block
  if [ -n "$1" ]; then
    uci set firewall.podkop_agent_clash_allow=rule
    uci set firewall.podkop_agent_clash_allow.name='podkop-agent: Clash API for admins'
    uci set firewall.podkop_agent_clash_allow.src=lan
    uci set firewall.podkop_agent_clash_allow.proto=tcp
    uci set firewall.podkop_agent_clash_allow.dest_port=9090
    uci set firewall.podkop_agent_clash_allow.target=ACCEPT
    for _ip in $1; do uci add_list firewall.podkop_agent_clash_allow.src_ip="$_ip"; done
  fi
  if [ "$2" = on ]; then
    uci set firewall.podkop_agent_clash_block=rule
    uci set firewall.podkop_agent_clash_block.name='podkop-agent: no Clash API from the LAN'
    uci set firewall.podkop_agent_clash_block.src=lan
    uci set firewall.podkop_agent_clash_block.proto=tcp
    uci set firewall.podkop_agent_clash_block.dest_port=9090
    uci set firewall.podkop_agent_clash_block.target=REJECT
  fi
  uci commit firewall && /etc/init.d/firewall reload >/dev/null 2>&1
}

if [ "$UNINSTALL" = 1 ]; then
  [ -x /etc/init.d/podkop-sentinel ] && { /etc/init.d/podkop-sentinel stop 2>/dev/null; /etc/init.d/podkop-sentinel disable 2>/dev/null; }
  rm -rf /usr/bin/podkop-sentinel /etc/init.d/podkop-sentinel /tmp/podkop-sentinel
  [ -x /etc/init.d/podkop-guard ] && /etc/init.d/podkop-guard disable 2>/dev/null
  rm -f /etc/init.d/podkop-guard /etc/hotplug.d/ntp/90-podkop-agent /tmp/podkop-clock
  /etc/init.d/podkop-agent stop 2>/dev/null; /etc/init.d/podkop-agent disable 2>/dev/null
  # the OpenVPN client (VPN page); the openvpn package stays
  [ -x /etc/init.d/podkop-vpn ] && { /etc/init.d/podkop-vpn stop 2>/dev/null; /etc/init.d/podkop-vpn disable 2>/dev/null; rm -f /etc/init.d/podkop-vpn; }
  rm -rf /usr/bin/podkop-agent /etc/init.d/podkop-agent /etc/podkop-agent /tmp/podkop-agent /usr/share/podkop-agent
  [ -f "$KEYS" ] && sed -i "/ $MARK\$/d; / $FWD_MARK\$/d" "$KEYS"
  firewall_rules "" off
  if uci -q get firewall.podkop_agent_ipv6_block >/dev/null; then
    uci -q delete firewall.podkop_agent_ipv6_block; uci commit firewall && /etc/init.d/firewall reload >/dev/null 2>&1
  fi
  nft delete table inet podkop_agent_guard 2>/dev/null
  for _f in /tmp/dnsmasq.d/podkop-agent-strict.conf /tmp/dnsmasq.*.d/podkop-agent-strict.conf; do
    [ -f "$_f" ] && { rm -f "$_f"; /etc/init.d/dnsmasq restart >/dev/null 2>&1; }
  done
  for _f in $KEEP; do
    [ "$_f" = /usr/bin/podkop-probe ] && continue      # podkop-probe stays
    sed -i "\\|^$_f\$|d" /etc/sysupgrade.conf 2>/dev/null
  done
  echo "podkop-agent removed (podkop-probe is kept: podkop-probe --uninstall removes it)"
  exit 0
fi

if [ "$UPGRADE" = 1 ]; then
  [ -x /usr/bin/podkop-agent ] && [ -x /etc/init.d/podkop-agent ] || die "no existing agent; use the Add router command first"
else
  [ -n "$KEY" ] || die "--key 'ssh-ed25519 AAAA...' is required"
  case "$KEY" in ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;; *) die "--key must be an SSH public key" ;; esac
  case "$FWD_KEY" in ""|ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;; *) die "--forward-key must be an SSH public key" ;; esac
fi
[ -x /usr/bin/podkop ] && [ -f /usr/lib/podkop/sing_box_config_facade.sh ] || die "podkop is not installed"
# without podkop-engine the agent works (reading, applying, the watchdog), podkop-probe does not
sing-box version 2>/dev/null | grep -q 'tools.decode-link' ||
  echo "warning: podkop-engine is not installed, so podkop-probe can't test servers here (https://github.com/FiyeroT/podkop-engine)" >&2
command -v jq >/dev/null 2>&1 || die "jq is not installed"
for _t in flock setsid; do command -v "$_t" >/dev/null 2>&1 || die "$_t is not available (BusyBox without it?)"; done

TMP=/tmp/podkop-agent-install.$$
umask 077
mkdir "$TMP" || die "cannot create $TMP"
ROLLBACK=0; WAS_RUNNING=0
FILES="/usr/bin/podkop-agent /etc/init.d/podkop-agent /usr/bin/podkop-probe /usr/share/podkop-agent/reliability.sh /usr/share/podkop-agent/install.sh"
cleanup() {
  _rc=$?
  trap - EXIT HUP INT TERM
  if [ "$ROLLBACK" = 1 ]; then
    echo "Upgrade failed; restoring the previous agent files." >&2
    for _f in $FILES; do
      if [ -f "$TMP/backup$_f" ]; then
        cp -p "$TMP/backup$_f" "$_f.new" && mv "$_f.new" "$_f" || echo "error: could not restore $_f" >&2
      else rm -f "$_f"; fi
    done
    [ "$WAS_RUNNING" = 0 ] || /etc/init.d/podkop-agent restart 7>&- 8>&- 9>&- || echo "error: could not restart the previous agent" >&2
  fi
  rm -rf "$TMP"
  exit "$_rc"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
# whole <file> <its last line>: a cut-off download is not installed
whole() { [ "$(tail -n 1 "$1")" = "$2" ] && sh -n "$1" 2>/dev/null; }
fetch podkop-agent "$TMP/podkop-agent" && whole "$TMP/podkop-agent" "# end of podkop-agent" || die "could not get podkop-agent"
fetch podkop-agent.init "$TMP/podkop-agent.init" && grep -q 'podkop-agent daemon' "$TMP/podkop-agent.init" &&
  sh -n "$TMP/podkop-agent.init" 2>/dev/null || die "could not get podkop-agent.init"
fetch probe.sh "$TMP/probe.sh" && whole "$TMP/probe.sh" "# end of podkop-probe" || die "could not get probe.sh"

fetch reliability.sh "$TMP/reliability.sh" && whole "$TMP/reliability.sh" "# end of podkop reliability" || die "could not get reliability.sh"

if [ "$UPGRADE" = 1 ]; then
  mkdir -p /tmp/podkop-agent || die "cannot create agent runtime directory"
  exec 8> /tmp/podkop-agent/install.lock
  flock -n 8 || die "another agent upgrade is running"
  # an apply or a connection check (the manager runs them every minute or so)
  # finishes within seconds: wait for it, up to 90 s each (BusyBox flock has no -w)
  WAIT=${PODKOP_INSTALL_WAIT:-90}
  wait_lock() {
    _wl=0
    until flock -n "$1"; do
      [ "$_wl" = 0 ] && echo "waiting for $2 to finish..."
      [ "$_wl" -ge "$WAIT" ] && return 1
      sleep 1; _wl=$((_wl + 1))
    done
  }
  exec 9> /tmp/podkop-agent/apply.lock
  wait_lock 9 "an apply or recovery" || die "an apply or recovery is running for more than $WAIT s; try again in a minute"
  # a connection check cut off before agent 0.9.1 could leave its test
  # sing-box holding the check's lock for good: end it (never podkop's own)
  for _p in /proc/[0-9]*; do
    _held=0
    for _fd in "$_p"/fd/*; do
      case "$(readlink "$_fd" 2>/dev/null)" in *podkop-agent/diagnostics.lock) _held=1 ;; esac
    done
    [ "$_held" = 1 ] || continue
    case "$(tr '\0' ' ' < "$_p/cmdline" 2>/dev/null)" in
      *"sing-box run -c /tmp/podkop-agent/check."*) kill "${_p#/proc/}" 2>/dev/null && echo "ended a leftover connection check" ;;
    esac
  done
  exec 7> /tmp/podkop-agent/diagnostics.lock
  wait_lock 7 "a connection check" || die "a connection check is running for more than $WAIT s; see who holds /tmp/podkop-agent/diagnostics.lock"
  /etc/init.d/podkop-agent running >/dev/null 2>&1 && WAS_RUNNING=1
  for _f in $FILES; do
    if [ -e "$_f" ]; then
      mkdir -p "$TMP/backup$(dirname "$_f")" && cp -p "$_f" "$TMP/backup$_f" || die "could not back up $_f"
    fi
  done
  ROLLBACK=1
fi

# flash: a file that is already the same is not written again
put() { cmp -s "$1" "$2" || { cp "$1" "$2.new" && chmod 755 "$2.new" && mv "$2.new" "$2"; } || die "could not write $2"; }
mkdir -p /etc/podkop-agent /usr/share/podkop-agent || die "cannot create agent directories"
# Install the helper before the executable that advertises its commands.
put "$TMP/reliability.sh" /usr/share/podkop-agent/reliability.sh
put "$TMP/podkop-agent" /usr/bin/podkop-agent
put "$TMP/podkop-agent.init" /etc/init.d/podkop-agent
put "$TMP/probe.sh" /usr/bin/podkop-probe
# kept for "podkop-agent uninstall"
[ "$0" -ef /usr/share/podkop-agent/install.sh ] || put "$0" /usr/share/podkop-agent/install.sh

if [ "$UPGRADE" = 1 ]; then
  env -u SSH_ORIGINAL_COMMAND /usr/bin/podkop-agent version > "$TMP/version.json" || die "updated agent could not run"
  jq -e '.ok == true and (.data.capabilities | index("diagnostics") != null and index("eligibility") != null)' \
    "$TMP/version.json" >/dev/null || die "updated agent is missing connection health commands"
  if [ "$WAS_RUNNING" = 1 ]; then
    _sb=$(ubus call service list '{"name":"sing-box"}' 2>/dev/null | jq -r '[.["sing-box"].instances[]? | select(.running)] | length' 2>/dev/null)
    rm -f /tmp/podkop-agent/beat
    /etc/init.d/podkop-agent restart 7>&- 8>&- 9>&- || die "could not restart the updated agent"
    # the updated daemon is watched for a while before the old files go: it
    # must keep giving its sign of life (an agent once stood still 35 s after
    # every start), and a sing-box that ran must still run. Our locks are
    # let go first: the daemon waits for them.
    SOAK=${PODKOP_INSTALL_SOAK:-90}
    if [ "$SOAK" -gt 0 ]; then
      exec 7>&- 9>&-
      echo "watching the updated agent for $SOAK s..."
      sleep "$SOAK"
      beat_age() { echo $(( $(date +%s) - $(date -r /tmp/podkop-agent/beat +%s 2>/dev/null || echo 0) )); }
      # (one long round, e.g. a restart of podkop, is not a daemon that stands still)
      _again=60; [ "$SOAK" -ge 60 ] || _again=$SOAK
      [ "$(beat_age)" -le 60 ] || { sleep "$_again"; [ "$(beat_age)" -le 60 ]; } ||
        die "the updated agent's daemon stopped giving its sign of life ($(beat_age) s ago)"
      if [ "${_sb:-0}" -gt 0 ] && [ ! -f /etc/podkop-agent/safe-mode ]; then
        _sa=$(ubus call service list '{"name":"sing-box"}' 2>/dev/null | jq -r '[.["sing-box"].instances[]? | select(.running)] | length' 2>/dev/null)
        [ "${_sa:-0}" -gt 0 ] || die "sing-box stopped after the agent was updated"
      fi
    fi
  fi
  ROLLBACK=0
  sentinel_install; guard_init_install; clock_hook_install
  for _f in $KEEP; do grep -qxF "$_f" /etc/sysupgrade.conf 2>/dev/null || echo "$_f" >> /etc/sysupgrade.conf; done
  echo "podkop-agent $(jq -r '.data.version' "$TMP/version.json") upgraded; keys, firewall and Podkop settings preserved"
  exit 0
fi

chmod 700 /etc/podkop-agent

# the manager's key: only podkop-agent, no shell, no forwarding
touch "$KEYS" && chmod 600 "$KEYS"
sed -i "/ $MARK\$/d" "$KEYS"
set -f
# shellcheck disable=SC2086
set -- $KEY
set +f
printf 'restrict,command="/usr/bin/podkop-agent" %s %s %s\n' "$1" "$2" "$MARK" >> "$KEYS"
# the second key, only if asked for: connections through the router, no
# shell or command (it ends at once), no terminal, no agent or X11 forwarding
sed -i "/ $FWD_MARK\$/d" "$KEYS"
if [ -n "$FWD_KEY" ]; then
  set -f
  # shellcheck disable=SC2086
  set -- $FWD_KEY
  set +f
  printf 'command="/bin/false",no-agent-forwarding,no-X11-forwarding,no-pty %s %s %s\n' "$1" "$2" "$FWD_MARK" >> "$KEYS"
fi

if [ "$BLOCK" = on ]; then firewall_rules "$ALLOW" on
# nothing to change: only remove rules an earlier install of ours added
elif uci -q get firewall.podkop_agent_clash_block >/dev/null || uci -q get firewall.podkop_agent_clash_allow >/dev/null; then
  firewall_rules "" off
fi

for _f in $KEEP; do grep -qxF "$_f" /etc/sysupgrade.conf 2>/dev/null || echo "$_f" >> /etc/sysupgrade.conf; done

/etc/init.d/podkop-agent enable && /etc/init.d/podkop-agent restart 2>/dev/null || die "could not start podkop-agent"
sentinel_install; guard_init_install; clock_hook_install
echo "podkop-agent $(sed -n 's/^AGENT_VERSION="\(.*\)"/\1/p' /usr/bin/podkop-agent) installed"

# the line for podkop-manager: LAN address, SSH port and host key (ed25519
# when the router has one)
_hk=""
for _t in ed25519 ecdsa rsa; do
  [ -f "/etc/dropbear/dropbear_${_t}_host_key" ] || continue
  _hk=$(dropbearkey -y -f "/etc/dropbear/dropbear_${_t}_host_key" 2>/dev/null | grep -E '^(ssh|ecdsa)-' | cut -d ' ' -f 1-2)
  [ -n "$_hk" ] && break
done
_ip=$(uci -q get network.lan.ipaddr | cut -d / -f 1)
_port=$(uci -q get 'dropbear.@dropbear[0].Port')
echo
echo "Paste this line into podkop-manager (Add router):"
echo
echo "podkop-router ${_ip:-192.168.1.1} ${_port:-22} ${_hk:-unknown-host-key}"
