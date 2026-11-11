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
KEEP="/usr/bin/podkop-agent /etc/init.d/podkop-agent /etc/podkop-agent/ /usr/bin/podkop-probe /usr/share/podkop-agent/ /etc/init.d/podkop-vpn"

set -u
KEY=""; FWD_KEY=""; ALLOW=""; SRC=""; UNINSTALL=0; BLOCK=on; UPGRADE=0; PAIRING=0
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
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }

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
    /etc/init.d/podkop-agent restart 7>&- 8>&- 9>&- || die "could not restart the updated agent"
  fi
  ROLLBACK=0
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
