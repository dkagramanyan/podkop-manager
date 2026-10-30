#!/bin/sh
# shellcheck shell=dash disable=SC1090,SC2012,SC2016,SC2018,SC2019,SC2046,SC2154,SC2317
#
# podkop-probe — measure how reliable every proxy node is, right on the
# OpenWrt router that runs podkop (https://github.com/itdoginfo/podkop).
#
# For each node: start a throwaway sing-box on a local SOCKS port, confirm the
# exit IP through the node, fire N requests, count failures, report latency.
#
# One-liner (on the router):
#   sh <(wget -O - https://github.com/dkagramanyan/podkop-manager/releases/latest/download/probe.sh)
#
# https://github.com/dkagramanyan/podkop-manager

VERSION="1.29.0"
REPO_URL="https://github.com/dkagramanyan/podkop-manager"
RELEASE_URL="$REPO_URL/releases/latest/download/probe.sh"
BIN=/usr/bin/podkop-probe            # installed copy, used by the nightly run
CONF=/etc/podkop-probe.conf          # saved keys and settings
LOG=/tmp/podkop-probe.log            # log of the last --cron run
SAVED_BEST=/tmp/podkop-probe-best.txt     # the lists of the last run
SAVED_YT=/tmp/podkop-probe-youtube.txt
SAVED_FB=/tmp/podkop-probe-fallback.txt  # their fallback servers
SAVED_YTFB=/tmp/podkop-probe-youtube-fallback.txt
CRONTAB=/etc/crontabs/root
PODKOP_INSTALL="sh <(wget -O - https://raw.githubusercontent.com/itdoginfo/podkop/refs/heads/main/install.sh)"
ENGINE_URL="https://github.com/FiyeroT/podkop-engine/releases"
PODKOP_LIB=/usr/lib/podkop

set -u
export LC_ALL=C

N=20; C=4; J=""; T=8
URL="https://www.gstatic.com/generate_204"
BASEPORT=20000                       # local ports for the test: below the ones Linux gives outgoing connections (32768-60999)
FILE=""; SUBS=""; CONFIG=""; CSV=""
VERBOSE=0; ASSUME_YES=0; COLOR=auto; SOURCE=""
TOP=10; APPLY=ask; SECTION=main
FALLBACK=5                           # servers after the top ones: podkop's fallback links
SPEED=1; SPEED_URL=""; SPEED_T=3; SPEED_J=""; SPEED_J_USER=""; ENTRY_CHECK=1; FIX_FP=0
# direct download used to measure the router's own line (not Cloudflare,
# which some providers block without a VPN)
LINE_URL="https://fsn1-speed.hetzner.com/100MB.bin"
# speed test files, tried in order; a file that ends early is fetched again
# until SPEED_T is used up (Cloudflare refuses single requests of 100 MB)
SPEED_URLS="https://speed.cloudflare.com/__down?bytes=25000000
https://fsn1-speed.hetzner.com/100MB.bin"
# servers slower than this are never picked
MAX_PING=700                         # ms, median
MIN_SPEED=40                         # Mbit/s: this or less is too slow
# YouTube part: the servers that pass are checked with YouTube (does a video
# play, which country YouTube thinks it is in, ping to YouTube itself) and
# the best go into their own podkop section YT_SECTION (the "youtube" list,
# placed before the main section). No ads are sold in YT_NOADS countries, so
# servers YouTube places there are preferred.
YOUTUBE=1; YT_NOADS=RU; YT_SECTION=youtube
APPLY_TO=both                        # both, main or youtube: which lists go into podkop
YT_PING_URL="https://www.youtube.com/generate_204"
YT_VIDEO=jNQXAC9IVRw                 # "Me at the zoo": public everywhere, never removed
YT_UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
APPLY_SAVED=0
SUB_INFO=0
# podkop-manager tests on its own server: the router turns the keys into
# sing-box outbounds with podkop's parser (--build-nodes), the manager tests
# them (--nodes) with every connection going out through the router over SSH
# (--via), so the servers see the router's address
BUILD_NODES=0; NODES_FILE=""; VIA=""; VIA_KEY=""; VIA_HOST_KEY=""
CRON=0; CRON_TIME=""; INSTALL=0; UNINSTALL=0; UPDATE=0; NO_UPDATE=0
COUNTRIES=""                         # "" = all; europe; or DE,NL,...
# Subscription panels (Remnawave, Marzban, 3x-ui...) only hand the real server
# list to client apps they know, so we introduce ourselves like those apps.
UA="Happ/3.9.0"
SUB_UAS="Happ/3.9.0
Streisand/2.3.1
INCY/1.2.0
v2RayTun/5.12.0
Hiddify/2.5.7
sing-box/1.12.0
v2rayNG/1.10.2"
EUROPE="EU AD AL AT BA BE BG CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM UA VA XK"
ARGLINKS=""
WORK="/tmp/podkop-probe.$$"
TAB=$(printf '\t')
LINK_RE='(vless|vmess|trojan|ss|hysteria2|hy2|socks4|socks4a|socks5)://[^[:space:]"<>'"'"']+'

usage() {
  cat <<EOF
podkop-probe $VERSION — per-node reliability test for podkop / sing-box

Usage: sh probe.sh [options] [link ...]

With no arguments you get a menu: test podkop's current nodes, or enter
one or more subscription URLs ("main keys"), test every node in them, pick
the best by ping and speed and put them into podkop as a URLTest group,
optionally every night. Without a terminal the nodes are taken from podkop
automatically (its sing-box config or /etc/config/podkop).

Sources:
  link ...          vless:// trojan:// ss:// hysteria2:// hy2:// socks5:// links
  -f FILE           file with links, one per line ('-' = stdin, base64 ok)
  -s URL            subscription URL / "main key" (plain or base64 list of
                    links); repeat -s for several
  -C FILE           sing-box config.json to take the outbounds from
  --countries LIST  test/choose only servers in these countries:
                    europe, all (default), or codes like DE,NL,FI; checked
                    by name, by the server's IP and by the exit IP
  --no-entry-check  don't look up the server's IP country (keeps relays
                    whose entry point is elsewhere, e.g. "via RU")
  --fix-fingerprint REALITY links with a TLS fingerprint new servers reject
                    (fp=qq, 360, ios, android, edge) are tested and saved
                    with fp=chrome

Test:
  -n N              requests per node               (default $N)
  -c N              requests in flight per node     (default $C)
  -j N              nodes tested at the same time   (default: one per CPU
                    core; more makes pings look slower; speed tests use
                    --speed-jobs)
  -t SEC            timeout per request, seconds    (default $T)
  -u URL            target URL                      (default $URL)
  -q                quick run:    -n 12, 2 s speed test
  -F                thorough run: -n 100, 15 s speed test
  -p PORT           first local SOCKS port          (default $BASEPORT)
  --no-speed        skip the download speed test
  --speed-jobs N    speed tests at the same time (default: from the line
                    speed, 1 per ~200 Mbit/s, max 3)
  --speed-url URL   file for the speed test (default: Cloudflare, then Hetzner)
  --max-ping MS     never pick servers with a higher median ping (default $MAX_PING)
  --min-speed MBIT  never pick servers at or below this speed (default $MIN_SPEED)

YouTube (a separate podkop section for YouTube traffic):
  --no-youtube      skip the YouTube part, leave that section alone
  --youtube-noads LIST  countries where YouTube shows no ads (default $YT_NOADS);
                    servers YouTube places there are picked first ('none' = off)
  --yt-section NAME podkop section for YouTube (default $YT_SECTION; created
                    with the YouTube list and placed before --section)
  --apply-to WHAT   both (default), main or youtube: which list goes into
                    podkop with --apply and in the nightly run
  --apply-saved     put the lists of the last run into podkop, no new test
  --sub-info        for podkop-manager: each -s key's usage header and links
                    (they are kept in /tmp until the router restarts)
  --build-nodes     for podkop-manager: print each server as "link<TAB>sing-box
                    outbound" (or "link<TAB>!why podkop can't use it"), no test
  --nodes FILE      test the servers of such a file (no podkop needed)
  --via USER@HOST:PORT  send every test connection through this SSH server
                    (the router), so the servers see its address; TCP only
  --via-key FILE    SSH private key for --via
  --via-host-key KEY  the SSH server's host key ("ssh-ed25519 AAAA...")

Podkop (for link sources: -s, -f, links):
  --top N           how many best nodes to offer for podkop   (default $TOP);
                    the next $FALLBACK become podkop's fallback links
  --apply           write the best nodes to podkop as URLTest and restart it
                    without asking
  --no-apply        never offer to change podkop settings
  --section NAME    podkop section to write to                (default $SECTION)

Nightly run:
  --install         install as $BIN (asks for the keys,
                    or saves the -s keys and options in $CONF)
  --cron            run with the saved settings, no questions: update podkop
                    and restart it (log: $LOG). In LuCI ->
                    System -> Scheduled Tasks:  30 4 * * * podkop-probe --cron
  --cron-line HH:MM print the Scheduled Tasks line for another time
  --update          update the installed copy to the latest release
  --uninstall       remove the installed copy and the saved settings

Output:
  -o FILE           also save results as CSV
  -v                verbose: per-request timings and sing-box errors
  -y                answer "yes" to questions (install missing packages)
  --no-color        plain output
  -V, --version     print version
  -h, --help        this help

Examples:
  sh probe.sh                              # test podkop's own nodes
  sh probe.sh -q                           # fast check, 10 requests per node
  sh probe.sh -s https://example.com/sub   # test a subscription, offer best 10
  sh probe.sh -s https://a.example/sub -s https://b.example/sub --top 5 --apply -y
  sh probe.sh -s https://a.example/sub -s https://b.example/sub --countries europe --install
  sh probe.sh 'vless://...#de' 'trojan://...#nl'
EOF
  exit "${1:-0}"
}

# --- output helpers ----------------------------------------------------------

setup_color() {
  if [ "$COLOR" = auto ]; then [ -t 1 ] && COLOR=1 || COLOR=0; fi
  if [ "$COLOR" = 1 ]; then
    E=$(printf '\033'); R0="${E}[0m"; BD="${E}[1m"; DM="${E}[2m"
    RD="${E}[31m"; GR="${E}[32m"; YL="${E}[33m"; CY="${E}[36m"
  else
    R0=""; BD=""; DM=""; RD=""; GR=""; YL=""; CY=""
  fi
}
R0=""; BD=""; DM=""; RD=""; GR=""; YL=""; CY=""

info() { printf '%s==>%s %s\n' "$CY$BD" "$R0" "$*"; }
warn() { printf '%s[!]%s %s\n' "$YL$BD" "$R0" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$RD$BD" "$R0" "$*" >&2; exit 1; }

TTY=""
if (exec </dev/tty) 2>/dev/null; then TTY=/dev/tty; fi

ask_yn() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -n "$TTY" ] || return 1
  printf '%s?%s %s [Y/n] ' "$YL$BD" "$R0" "$1" >&2
  read -r _a < "$TTY" || return 1
  case "$_a" in ""|y|Y|yes|Yes|д|Д|да) return 0 ;; *) return 1 ;; esac
}

is_uint() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; [ "$1" -gt 0 ]; }

# --- arguments ---------------------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
    -f|-s|-C|--config|-n|-c|-j|-t|-u|-p|-o|--top|--section|--speed-url|--cron-line|--countries|--speed-jobs|--youtube-noads|--max-ping|--min-speed|--yt-section|--apply-to|--nodes|--via|--via-key|--via-host-key)
      [ $# -ge 2 ] || { echo "option $1 needs a value" >&2; usage 1; }
      case "$1" in
        -f) FILE="$2" ;; -s) SUBS="$SUBS$2
" ;; -C|--config) CONFIG="$2" ;;
        -n) N="$2" ;; -c) C="$2" ;; -j) J="$2" ;; -t) T="$2" ;;
        -u) URL="$2" ;; -p) BASEPORT="$2" ;; -o) CSV="$2" ;;
        --top) TOP="$2" ;; --section) SECTION="$2" ;;
        --speed-url) SPEED_URL="$2" ;; --cron-line) CRON_TIME="$2" ;;
        --speed-jobs) SPEED_J="$2"; SPEED_J_USER=$2 ;;
        --countries) COUNTRIES="$2" ;;
        --max-ping) MAX_PING="$2" ;; --min-speed) MIN_SPEED="$2" ;; --yt-section) YT_SECTION="$2" ;;
        --apply-to) APPLY_TO="$2" ;;
        --nodes) NODES_FILE="$2" ;; --via) VIA="$2" ;; --via-key) VIA_KEY="$2" ;; --via-host-key) VIA_HOST_KEY="$2" ;;
        --youtube-noads) YT_NOADS=$(printf '%s' "$2" | tr 'a-z,' 'A-Z ' | sed 's/\bNONE\b//g; s/^ *//; s/ *$//') ;;
      esac
      shift 2 ;;
    -q) N=12; SPEED_T=2; shift ;;
    -F) N=100; SPEED_T=15; shift ;;
    -v) VERBOSE=1; shift ;;
    -y) ASSUME_YES=1; shift ;;
    --apply) APPLY=yes; shift ;;
    --no-apply) APPLY=no; shift ;;
    --no-speed) SPEED=0; shift ;;
    --no-entry-check) ENTRY_CHECK=0; shift ;;
    --fix-fingerprint) FIX_FP=1; shift ;;
    --no-youtube) YOUTUBE=0; shift ;;
    --install) INSTALL=1; shift ;;
    --uninstall|--nightly-off) UNINSTALL=1; shift ;;
    --update) UPDATE=1; shift ;;
    --apply-saved) APPLY_SAVED=1; shift ;;
    --sub-info) SUB_INFO=1; shift ;;
    --build-nodes) BUILD_NODES=1; APPLY=no; shift ;;
    --no-update) NO_UPDATE=1; shift ;;
    --cron) CRON=1; shift ;;
    --no-color) COLOR=0; shift ;;
    -V|--version) echo "podkop-probe $VERSION"; exit 0 ;;
    -h|--help) usage 0 ;;
    *://*) ARGLINKS="$ARGLINKS$1
"; shift ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

# unattended run from cron: saved settings, no questions, log to $LOG
if [ "$CRON" = 1 ]; then
  [ -t 1 ] || exec > "$LOG" 2>&1
  echo "=== $(date)"
  if [ -z "$SUBS$ARGLINKS$FILE" ] && [ -f "$CONF" ]; then . "$CONF"; fi
  [ -n "$SUBS$ARGLINKS$FILE" ] || { echo "no keys: run 'podkop-probe' and set up the nightly run first" >&2; exit 1; }
  ASSUME_YES=1; APPLY=yes; COLOR=0; TTY=""
fi

case "$APPLY_TO" in both|main|youtube) ;; *) die "--apply-to must be both, main or youtube" ;; esac
case "$MIN_SPEED" in ''|*[!0-9]*) die "--min-speed must be a number, got '$MIN_SPEED'" ;; esac
for _v in N C T BASEPORT TOP MAX_PING; do
  eval "_x=\$$_v"; is_uint "$_x" || die "$_v must be a positive number, got '$_x'"
done
[ -z "$J" ] || is_uint "$J" || die "-j must be a positive number"
[ -z "$SPEED_J" ] || is_uint "$SPEED_J" || die "--speed-jobs must be a positive number"
[ "$C" -le "$N" ] || C=$N
if [ -n "$VIA" ]; then
  printf '%s' "$VIA" | grep -qE '^[a-z_][a-z0-9_-]*@[0-9A-Za-z.:-]+:[0-9]+$' || die "--via must be USER@HOST:PORT"
  [ -r "$VIA_KEY" ] || die "--via needs --via-key FILE"
  printf '%s' "$VIA_HOST_KEY" | grep -qE '^(ssh-ed25519|ecdsa-sha2-nistp[0-9]+|ssh-rsa) [A-Za-z0-9+/=]+$' ||
    die "--via needs --via-host-key \"ssh-ed25519 AAAA...\""
fi

setup_color

# --- dependencies ------------------------------------------------------------

pkg_install() {
  if command -v apk >/dev/null 2>&1; then
    apk update >/dev/null 2>&1; apk add "$@"
  elif command -v opkg >/dev/null 2>&1; then
    opkg update >/dev/null 2>&1; opkg install "$@"
  else
    return 1
  fi
}

# need_tool <cmd> <package> <why>
need_tool() {
  command -v "$1" >/dev/null 2>&1 && return 0
  if ask_yn "'$1' is not installed ($3). Install package '$2' now"; then
    info "installing $2 ..."
    pkg_install "$2" >&2
    command -v "$1" >/dev/null 2>&1 && return 0
    warn "could not install $2"
  fi
  return 1
}

# --- small utilities ---------------------------------------------------------

# base64 decode from stdin; url-safe alphabet and missing padding are fine
b64d() {
  tr -d ' \r\n\t' | tr '_-' '/+' |
    awk '{ n = length($0) % 4; if (n == 2) $0 = $0 "=="; else if (n == 3) $0 = $0 "="; printf "%s", $0 }' |
    { base64 -d 2>/dev/null || openssl base64 -d -A 2>/dev/null; }
}

fetch() {
  # only web addresses: anything else could be taken for a curl option or a file
  case "$1" in http://*|https://*) ;; *) echo "not an http(s) address: $1" >&2; return 1 ;; esac
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --max-time 30 -A "$UA" "$1"
  else
    wget -q -U "$UA" -O - "$1"
  fi
}

# --- subscriptions -----------------------------------------------------------

# xray / sing-box JSON subscription -> share links (needs jq)
JSON2LINKS='
def enc: tostring | @uri;
def kv(k; v): if (v // "" | tostring) == "" then empty else "\(k)=\(v | enc)" end;
def hp(a; p): (if (a | tostring | contains(":")) then "[\(a)]" else "\(a)" end) + ":\(p)";
def xq(s): [kv("type"; s.network // "tcp"), kv("security"; s.security // "none"),
  kv("sni"; s.realitySettings.serverName // s.tlsSettings.serverName),
  kv("fp"; s.realitySettings.fingerprint // s.tlsSettings.fingerprint),
  kv("pbk"; s.realitySettings.publicKey), kv("sid"; s.realitySettings.shortId),
  kv("alpn"; (s.tlsSettings.alpn // []) | join(",")),
  kv("path"; s.wsSettings.path // s.httpupgradeSettings.path // s.xhttpSettings.path),
  kv("host"; s.wsSettings.headers.Host // s.wsSettings.host // s.httpupgradeSettings.host // s.xhttpSettings.host),
  kv("serviceName"; s.grpcSettings.serviceName),
  kv("mode"; s.xhttpSettings.mode), kv("extra"; s.xhttpSettings.extra)];
def sq(o): [kv("type"; o.transport.type // "tcp"),
  kv("security"; if o.tls.reality.enabled then "reality" elif o.tls.enabled then "tls" else "none" end),
  kv("sni"; o.tls.server_name), kv("fp"; o.tls.utls.fingerprint),
  kv("pbk"; o.tls.reality.public_key), kv("sid"; o.tls.reality.short_id),
  kv("alpn"; (o.tls.alpn // []) | join(",")),
  kv("path"; o.transport.path), kv("host"; o.transport.headers.Host // o.transport.host),
  kv("serviceName"; o.transport.service_name)];
(if type == "array" then .[] else . end) | select(type == "object") | (.remarks // "") as $rem
| ([.outbounds[]? | select(type == "object" and ((.protocol // .type) | IN("vless", "trojan", "shadowsocks", "hysteria2")))] | length) as $np
| .outbounds[]? | select(type == "object")
| ($rem + (if $np > 1 and (.tag // "proxy") != "proxy" then " [\(.tag)]" else "" end)) as $name
| if .protocol == "vless" then .settings.vnext[0] as $v | $v.users[0] as $u
    | "vless://\($u.id)@\(hp($v.address; $v.port))?" + (xq(.streamSettings // {}) + [kv("flow"; $u.flow), kv("encryption"; $u.encryption // "none")] | join("&")) + "#" + ($name | enc)
  elif .protocol == "trojan" then .settings.servers[0] as $v
    | "trojan://\($v.password | enc)@\(hp($v.address; $v.port))?" + (xq(.streamSettings // {}) | join("&")) + "#" + ($name | enc)
  elif .protocol == "shadowsocks" then .settings.servers[0] as $v
    | "ss://" + ("\($v.method):\($v.password)" | @base64) + "@\(hp($v.address; $v.port))#" + ($name | enc)
  elif .type == "vless" then
    "vless://\(.uuid)@\(hp(.server; .server_port))?" + (sq(.) + [kv("flow"; .flow), "encryption=none"] | join("&")) + "#" + ((.tag // "") | enc)
  elif .type == "trojan" then
    "trojan://\(.password | enc)@\(hp(.server; .server_port))?" + (sq(.) | join("&")) + "#" + ((.tag // "") | enc)
  elif .type == "shadowsocks" then
    "ss://" + ("\(.method):\(.password)" | @base64) + "@\(hp(.server; .server_port))#" + ((.tag // "") | enc)
  elif .type == "hysteria2" then
    "hysteria2://\(.password | enc)@\(hp(.server; .server_port))?" + ([kv("sni"; .tls.server_name), kv("obfs"; .obfs.type), kv("obfs-password"; .obfs.password)] | join("&")) + "#" + ((.tag // "") | enc)
  else empty end'

URLDEC_AWK='
function hv(c) { return index("0123456789abcdef", tolower(c)) - 1 }
function dec(s,   o, i, c, a, b) {
  o = ""
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c == "%" && i + 2 <= length(s)) {
      a = hv(substr(s, i + 1, 1)); b = hv(substr(s, i + 2, 1))
      if (a >= 0 && b >= 0) { o = o sprintf("%c", a * 16 + b); i += 2; continue }
    }
    o = o (c == "+" ? " " : c)
  }
  return o
}'

# drop the fake entries panels put into subscriptions ("App not supported",
# traffic left, expiry date, device limit...). stdin/stdout: links
REAL_AWK="$URLDEC_AWK"'
{
  l = $0; name = ""; p = index(l, "#"); if (p) { name = tolower(dec(substr(l, p + 1))); l = substr(l, 1, p - 1) }
  sub(/\?.*/, "", l); sub(/^[a-z0-9]+:\/\//, "", l); sub(/^.*@/, "", l); sub(/\/.*$/, "", l)
  host = l; sub(/:[0-9]+$/, "", host); gsub(/[][]/, "", host)
  if (host == "" || host ~ /^(0\.0\.0\.0|127\.|localhost$|::1?$|example\.)/) next
  if ($0 ~ /00000000-0000-0000-0000-000000000000/) next
  if (name ~ /not supported|unsupported|please update|update (the |your )?app|expired|traffic limit|device limit|hwid|renew/) next
  # tolower does not lower Cyrillic here (LC_ALL=C); not "Безлимит" or "Для всех устройств"
  if (name ~ /не поддерж|обнови|истек|истёк|(Л|л)имит (устройств|трафика)|(П|п)ревышен лимит|(Д|д)остигнут лимит|(З|з)акончил|исчерпан|продли|осталось/) next
  print
}'

HWID=""
ROUTER_MODEL=$( { tr -d '\n' < /tmp/sysinfo/model; } 2>/dev/null)

# sub_fetch <url> <user-agent> <out>: download the way the client app would.
# Panels with a device limit want a stable x-hwid; the router gets one.
sub_fetch() {
  case "$1" in http://*|https://*) ;; *) echo "not an http(s) address" > "$3"; return 1 ;; esac
  if [ -z "$HWID" ]; then
    HWID=$( { cat /sys/class/net/br-lan/address /sys/class/net/eth0/address 2>/dev/null
              cat /proc/sys/kernel/hostname 2>/dev/null; } | md5sum 2>/dev/null | cut -c 1-16)
    [ -n "$HWID" ] || HWID=0000000000000000
  fi
  curl -sL --max-time 20 -A "$2" -H "Accept: */*" -H "x-hwid: $HWID" -H "x-device-os: Linux" \
    -H "x-ver-os: OpenWrt" -H "x-device-model: ${ROUTER_MODEL:-OpenWrt}" -D "$3.h" "$1" > "$3" 2>/dev/null
}

# sub_info <headers>: "title, used X of Y GB, expires DATE" from the panel's headers
sub_info() {
  _t=$(sed -n 's/^[Pp]rofile-[Tt]itle: *//p' "$1" | tr -d '\r' | tail -n 1)
  case "$_t" in base64:*) _t=$(printf '%s' "${_t#base64:}" | b64d) ;; esac
  _ui=$(sed -n 's/^[Ss]ubscription-[Uu]serinfo: *//p' "$1" | tr -d '\r' | tail -n 1)
  _uiv() { printf '%s' "$_ui" | tr ';' '\n' | sed -n "s/^ *$1=\([0-9]*\).*/\1/p"; }
  _up=$(_uiv upload); _dn=$(_uiv download); _tot=$(_uiv total); _exp=$(_uiv expire)
  _out=${_t:+$_t}
  if [ -n "$_dn$_up" ]; then
    _out="${_out:+$_out, }$(awk -v u="${_up:-0}" -v d="${_dn:-0}" -v t="${_tot:-0}" 'BEGIN {
      printf "used %.1f GB", (u + d) / 1073741824; if (t > 0) printf " of %.0f GB", t / 1073741824 }')"
  fi
  if [ -n "$_exp" ] && [ "$_exp" -gt 0 ] 2>/dev/null; then
    _out="${_out:+$_out, }expires $(date -d "@$_exp" +%Y-%m-%d 2>/dev/null || echo "$_exp")"
  fi
  printf '%s' "$_out"
}

# sub_parse <raw> -> links on stdout (base64 / plain list / xray or sing-box JSON)
sub_parse() {
  case "$(tr -d ' \r\n\t' < "$1" | cut -c 1)" in
    "{"|"[") command -v jq >/dev/null 2>&1 && jq -r "$JSON2LINKS" "$1" 2>/dev/null | grep -oE "$LINK_RE" ;;
    *) extract_links < "$1" ;;
  esac
}

# sub_why <raw>: short human reason why nothing usable came back
sub_why() {
  if [ ! -s "$1" ]; then echo "empty answer (wrong URL, expired key, or the site is blocked)"; return; fi
  _h=$(head -c 400 "$1" | tr 'A-Z' 'a-z')
  case "$_h" in
    *"<html"*|*"<!doctype"*) echo "got a web page instead of a server list" ;;
    *happ://crypt*) echo "only encrypted happ://crypt links (Happ-only format, can't be decoded)" ;;
    *"proxies:"*|*"proxy-groups"*) echo "got a Clash config (not supported)" ;;
    *) if [ -s "$1.links" ]; then
         _ph=$(sed -n 's/.*#//p' "$1.links" | head -n 2 | awk "$URLDEC_AWK"'{ print dec($0) }' | tr '\n' ';')
         case "$(printf '%s' "$_ph" | tr 'A-Z' 'a-z')" in
           *"device limit"*|*"limit of devices"*|*"лимит устройств"*)
             echo "the key's device limit is reached ($_ph): remove an old device in your provider's bot, the router shows up there as '${ROUTER_MODEL:-OpenWrt}'" ;;
           *) echo "only placeholder entries: $_ph" ;;
         esac
       else
         echo "no vless/vmess/trojan/ss/hysteria2 links inside"
       fi ;;
  esac
}

# --- countries ---------------------------------------------------------------

# node name -> ISO country code from its flag emoji or a country/city name
CC_AWK='
BEGIN {
  pre = sprintf("%c%c%c", 240, 159, 135)
  for (i = 0; i < 26; i++) RI[sprintf("%c", 166 + i)] = sprintf("%c", 65 + i)
  n = split("Germany=DE Германия=DE Frankfurt=DE Франкфурт=DE Netherlands=NL Нидерланды=NL Holland=NL Голландия=NL Amsterdam=NL Амстердам=NL Finland=FI Финляндия=FI Helsinki=FI Хельсинки=FI France=FR Франция=FR Paris=FR Париж=FR Poland=PL Польша=PL Warsaw=PL Варшава=PL Sweden=SE Швеция=SE Stockholm=SE Стокгольм=SE Britain=GB Британия=GB Великобритания=GB Англия=GB London=GB Лондон=GB Latvia=LV Латвия=LV Riga=LV Рига=LV Estonia=EE Эстония=EE Tallinn=EE Таллин=EE Lithuania=LT Литва=LT Austria=AT Австрия=AT Vienna=AT Вена=AT Switzerland=CH Швейцария=CH Italy=IT Италия=IT Milan=IT Милан=IT Spain=ES Испания=ES Madrid=ES Мадрид=ES Norway=NO Норвегия=NO Czech=CZ Чехия=CZ Prague=CZ Прага=CZ Romania=RO Румыния=RO Bulgaria=BG Болгария=BG Moldova=MD Молдова=MD Serbia=RS Сербия=RS Hungary=HU Венгрия=HU Portugal=PT Португалия=PT Ireland=IE Ирландия=IE Denmark=DK Дания=DK Belgium=BE Бельгия=BE Greece=GR Греция=GR Slovakia=SK Словакия=SK Slovenia=SI Словения=SI Croatia=HR Хорватия=HR Luxembourg=LU Люксембург=LU Iceland=IS Исландия=IS Cyprus=CY Кипр=CY Ukraine=UA Украина=UA Russia=RU Россия=RU Moscow=RU Москва=RU Belarus=BY Беларусь=BY Turkey=TR Турция=TR Istanbul=TR Стамбул=TR Kazakhstan=KZ Казахстан=KZ USA=US США=US America=US Америка=US Canada=CA Канада=CA Japan=JP Япония=JP Singapore=SG Сингапур=SG Kong=HK Гонконг=HK Israel=IL Израиль=IL UAE=AE ОАЭ=AE Dubai=AE Дубай=AE Armenia=AM Армения=AM Georgia=GE Грузия=GE India=IN Индия=IN", W, " ")
  for (i = 1; i <= n; i++) { p = index(W[i], "="); K[i] = substr(W[i], 1, p - 1); V[i] = substr(W[i], p + 1) }
}
{
  s = $0; cc = "-"; p = index(s, pre)
  while (p) {
    a = substr(s, p + 3, 1); b = substr(s, p + 7, 1)
    if (substr(s, p + 4, 3) == pre && (a in RI) && (b in RI)) { cc = RI[a] RI[b]; break }
    s = substr(s, p + 4); p = index(s, pre)
  }
  if (cc == "-") for (i = 1; i <= n; i++) if (index($0, K[i])) { cc = V[i]; break }
  if (cc == "-") {
    s = $0; gsub(/[^A-Za-z]+/, " ", s); m = split(s, T, " ")
    for (i = 1; i <= m; i++) if (T[i] ~ /^[A-Z][A-Z]$/ && index(" " cl " ", " " T[i] " ")) { cc = T[i]; break }
  }
  print cc
}'
KNOWN_CC="$EUROPE RU BY TR KZ US CA JP SG HK IL AE AM GE IN"

name_cc() { printf '%s\n' "$1" | awk -v cl="$KNOWN_CC" "$CC_AWK"; }

# cc_allowed <cc>: unknown ("-") passes; the exit IP check decides later
cc_allowed() {
  [ "$1" = "-" ] && return 0
  case "$COUNTRIES" in
    all|"") return 0 ;;
    europe|Europe|EU|eu) case " $EUROPE " in *" $1 "*) return 0 ;; esac; return 1 ;;
    *) case ",$(echo "$COUNTRIES" | tr 'a-z ' 'A-Z,')," in *",$1,"*) return 0 ;; esac; return 1 ;;
  esac
}

countries_label() {
  case "$COUNTRIES" in all|"") echo "all countries" ;; europe|Europe|EU|eu) echo "Europe only" ;; *) echo "$COUNTRIES" ;; esac
}

# stdin: any text (plain or base64) -> supported links, one per line
extract_links() {
  _t=$(cat)
  case "$_t" in *://*) ;; *) _t=$(printf '%s' "$_t" | b64d) ;; esac
  printf '%s\n' "$_t" | tr -d '\r' | grep -oE "$LINK_RE"
}

# link_name <link>: the server name from the link's #fragment, else host:port;
# control characters (a %0A newline...) become spaces, so it stays one line
# fix_fp <link>: fp=chrome instead of a fingerprint REALITY servers since
# Xray-core v26.9.8 reject (no X25519MLKEM768 key share), with --fix-fingerprint
fix_fp() {
  case "$FIX_FP:$1" in
    1:*security=reality*) printf '%s\n' "$1" | sed -E 's/([?&]fp=)(qq|360|ios|android|edge)([&#]|$)/\1chrome\3/' ;;
    *) printf '%s\n' "$1" ;;
  esac
}

link_name() {
  case "$1" in
    *#?*) printf '%s\n' "${1#*#}" | awk "$URLDEC_AWK"'{ s = dec($0); gsub(/[[:cntrl:]]/, " ", s); print s }' ;;
    vmess://*) printf '%s' "${1#*://}" | b64d |
      jq -r '.ps // "vmess" | tostring | explode | map(if . < 32 or . == 127 then 32 else . end) | implode' 2>/dev/null || echo vmess ;;
    *) _h=${1#*://}; _h=${_h##*@}; printf '%s\n' "${_h%%[?/#]*}" ;;
  esac
}

# build_nodes: <dir>/ob.json for every link node, made by podkop's own link
# parser, so each node is tested exactly the way podkop will run it. A link
# podkop can't use gets <dir>/skip with podkop's reason instead.
build_nodes() (
  set +u
  # shellcheck disable=SC1091
  . "$PODKOP_LIB/sing_box_config_facade.sh"
  log() { case "$2" in error|fatal) printf '%s\n' "$1" >> "$_d/skip" ;; esac; }
  _i=0
  while [ "$_i" -lt "$COUNT" ]; do
    _i=$((_i + 1)); _d="$WORK/n/$_i"
    { [ -f "$_d/link" ] && [ ! -f "$_d/ob.json" ] && [ ! -f "$_d/skip" ]; } || continue
    ( sing_box_cf_add_proxy_outbound '{"outbounds":[]}' node "$(cat "$_d/link")" 0 ) > "$_d/sb.json" 2>/dev/null
    if [ ! -f "$_d/skip" ]; then
      jq -ce '.outbounds[0] | .tag = "node"' "$_d/sb.json" > "$_d/ob.json" 2>/dev/null ||
        echo "can't read this link" > "$_d/skip"
    fi
    if [ -f "$_d/skip" ]; then
      rm -f "$_d/ob.json"
      sed -i 's/ Aborted\.$//; s/^/podkop: /' "$_d/skip"
    fi
    rm -f "$_d/sb.json"
  done
)

# --- node sources ------------------------------------------------------------

NODES="$WORK/nodes"        # lines: "L<TAB>link", "O<TAB>name<TAB>outbound-json" or "N<TAB>link<TAB>outbound-json"

add_links() { extract_links | awk -v t="$TAB" '{print "L" t $0}' >> "$NODES"; }

# add_nodes <file>: the lines of --build-nodes ("link<TAB>outbound" or "link<TAB>!why")
add_nodes() {
  grep -E "^$LINK_RE$TAB." "$1" | awk -v t="$TAB" '{print "N" t $0}' >> "$NODES"
}

from_config() {
  [ -f "$1" ] || die "config not found: $1"
  need_tool jq jq "needed to read $1" || die "jq is required to read a sing-box config"
  jq -r '.outbounds[]? | select(type == "object" and .server != null and .type != "direct")
         | ((.tag // "node") + " (" + (.server | tostring) + ")") + "\t"
           + (del(.detour, .domain_resolver) | .tag = "node" | tojson)' "$1" 2>/dev/null |
    awk -v t="$TAB" '{print "O" t $0}' >> "$NODES"
}

podkop_config_path() {
  _p=$(uci -q get podkop.settings.config_path 2>/dev/null)
  for _c in "$_p" /etc/sing-box/config.json /tmp/sing-box/config.json \
            /tmp/etc/sing-box/config.json /var/etc/sing-box/config.json; do
    [ -n "$_c" ] && [ -f "$_c" ] && { echo "$_c"; return 0; }
  done
  return 1
}

from_uci() {
  command -v uci >/dev/null 2>&1 || return 1
  uci -q show podkop 2>/dev/null | grep -E '\.(proxy_string|[a-z_]*proxy_links|urltest_fallback_links)=' | add_links
}

ask_links() {
  [ -n "$TTY" ] || return 1
  echo
  info "No nodes found. Paste links (vless://, trojan://, ss://, ...) or a subscription URL."
  info "One per line, finish with an empty line:"
  while IFS= read -r _l < "$TTY"; do
    [ -z "$_l" ] && break
    case "$_l" in
      http://*|https://*) fetch "$_l" | add_links || warn "could not fetch $_l" ;;
      *) printf '%s\n' "$_l" | add_links ;;
    esac
  done
  SOURCE="pasted links"
}

# from_sub <url> <label>: try the client apps one by one until the panel gives
# real servers, the same thing Happ/Streisand/INCY do when a key is imported
from_sub() {
  _host=${1#*://}; _host=${_host%%/*}
  _raw="$WORK/sub.$_k"; _why=""; _app=""; _japp=""
  : > "$_raw.real"; rm -f "$_raw.json" "/tmp/podkop-probe-sub$_k.txt"
  _uas=$(printf '%s\n' "$SUB_UAS" | tr ' ' '~')
  set -f
  for _ua in $_uas; do
    _ua=$(printf '%s' "$_ua" | tr '~' ' ')
    sub_fetch "$1" "$_ua" "$_raw.try"
    sub_parse "$_raw.try" > "$_raw.try.links"
    awk "$REAL_AWK" "$_raw.try.links" > "$_raw.try.real"
    # keep the most telling reason: placeholder entries beat "web page" etc.
    if [ -z "$_why" ] || { [ -s "$_raw.try.links" ] && case "$_why" in "only placeholder"*) false ;; *) true ;; esac; }; then
      _why=$(sub_why "$_raw.try")
    fi
    [ "$VERBOSE" = 1 ] && [ ! -f "/tmp/podkop-probe-sub$_k.txt" ] && cp "$_raw.try" "/tmp/podkop-probe-sub$_k.txt"
    [ -s "$_raw.try.real" ] || continue
    # a plain list of links carries the provider's own server names; a JSON
    # answer (xray config for Happ & co.) is only kept as a fallback
    case "$(tr -d ' \r\n\t' < "$_raw.try" | cut -c 1)" in
      "{"|"[")
        [ -s "$_raw.json" ] || { mv "$_raw.try.real" "$_raw.json"; cp "$_raw.try.h" "$_raw.json.h" 2>/dev/null; _japp=${_ua%%/*}; }
        continue ;;
    esac
    _app=${_ua%%/*}; mv "$_raw.try.real" "$_raw.real"; cp "$_raw.try.h" "$_raw.h" 2>/dev/null
    break
  done
  set +f
  if [ ! -s "$_raw.real" ] && [ -s "$_raw.json" ]; then
    mv "$_raw.json" "$_raw.real"; mv "$_raw.json.h" "$_raw.h" 2>/dev/null; _app="$_japp, xray JSON"
  fi
  if [ ! -s "$_raw.real" ]; then
    warn "$2 ($_host): $_why"
    [ "$VERBOSE" = 1 ] && info "first answer saved to /tmp/podkop-probe-sub$_k.txt"
    return 1
  fi
  awk -v t="$TAB" '{print "L" t $0}' "$_raw.real" >> "$NODES"
  _si=$(sub_info "$_raw.h" 2>/dev/null)
  printf '  %s  %-24s %3s servers%s\n' "$2" "$_host" "$(wc -l < "$_raw.real" | tr -d ' ')" "${_si:+  · $_si}"
  [ "$VERBOSE" = 1 ] && info "    fetched as $_app"
  return 0
}

# sub_usage <url>: fetched like from_sub, prints "K<TAB>url<TAB>usage header
# <TAB>title header<TAB>why nothing came", then "L<TAB>link" for each server
sub_usage() {
  _raw="$WORK/use"; _why=""
  rm -f "$_raw.real" "$_raw.h" "$_raw.json" "$_raw.json.h"
  for _ua in $(printf '%s\n' "$SUB_UAS" | tr ' ' '~'); do
    _ua=$(printf '%s' "$_ua" | tr '~' ' ')
    sub_fetch "$1" "$_ua" "$_raw.try"
    sub_parse "$_raw.try" | awk "$REAL_AWK" > "$_raw.try.real"
    [ -n "$_why" ] || _why=$(sub_why "$_raw.try")
    [ -s "$_raw.try.real" ] || continue
    case "$(tr -d ' \r\n\t' < "$_raw.try" | cut -c 1)" in
      "{"|"[") [ -s "$_raw.json" ] || { mv "$_raw.try.real" "$_raw.json"; cp "$_raw.try.h" "$_raw.json.h"; }; continue ;;
    esac
    mv "$_raw.try.real" "$_raw.real"; cp "$_raw.try.h" "$_raw.h"; break
  done
  [ -s "$_raw.real" ] || { mv "$_raw.json" "$_raw.real" 2>/dev/null; mv "$_raw.json.h" "$_raw.h" 2>/dev/null; }
  [ -s "$_raw.real" ] && _why=""
  [ -f "$_raw.h" ] || cp "$_raw.try.h" "$_raw.h" 2>/dev/null
  printf 'K\t%s\t%s\t%s\t%s\n' "$1" \
    "$(sed -n 's/^[Ss]ubscription-[Uu]serinfo: *//p' "$_raw.h" 2>/dev/null | tr -d '\r\t' | tail -n 1)" \
    "$(sed -n 's/^[Pp]rofile-[Tt]itle: *//p' "$_raw.h" 2>/dev/null | tr -d '\r\t' | tail -n 1)" "$_why"
  [ -s "$_raw.real" ] && sed 's/^/L\t/' "$_raw.real"
  return 0
}

from_subs() {
  _k=0; _oks=0
  set -f
  for _u in $SUBS; do
    _k=$((_k + 1))
    _u=$(printf '%s' "$_u" | sed 's/^\[//; s/[])]*$//')
    from_sub "$_u" "key $_k" && _oks=$((_oks + 1))
  done
  set +f
  [ "$_oks" -gt 0 ] || die "none of the subscriptions gave any links"
  SOURCE="${SOURCE:+$SOURCE + }$_oks subscription(s)"
}

# read one or more subscription URLs from the terminal into SUBS
ask_subs() {
  if [ -n "$SAVED_SUBS" ]; then
    echo "Paste your keys (subscription URLs), then an empty line."
    echo "Just Enter = your $(printf '%s' "$SAVED_SUBS" | grep -c .) saved key(s):"
  else
    echo "Paste your keys (subscription URLs), then an empty line:"
  fi
  _got=""
  while IFS= read -r _l < "$TTY"; do
    [ -n "$(printf '%s' "$_l" | tr -d ' \r\t')" ] || break
    _u=$(printf '%s\n' "$_l" | grep -oE 'https?://[^[:space:]]+')
    [ -n "$_u" ] || { warn "not a URL, skipped: $_l"; continue; }
    _got="$_got$_u
"
  done
  if [ -z "$_got" ]; then
    [ -n "$SAVED_SUBS" ] || return 1
    SUBS=$SAVED_SUBS; return 0
  fi
  SUBS=$_got
}

ask_countries() {
  _def=1; case "$COUNTRIES" in all) _def=2 ;; europe|""|Europe|EU|eu) ;; *) _def=3 ;; esac
  printf 'Servers: 1) Europe only  2) all countries  3) country codes  [%s]: ' "$_def"
  read -r _c < "$TTY" || return
  [ -z "$_c" ] && [ "$_def" != 1 ] && return
  case "$_c" in
    ""|1) COUNTRIES=europe ;;
    2) COUNTRIES=all ;;
    3) printf 'Country codes, comma separated: '; read -r _c < "$TTY"; COUNTRIES=${_c:-europe} ;;
    *) COUNTRIES=$_c ;;
  esac
}

menu() {
  echo
  printf '%sWhat do you want to do?%s\n' "$BD" "$R0"
  echo "  1) Test my keys and put the best $TOP servers into podkop"
  echo "  2) Test the servers podkop uses now"
  echo "  3) Test pasted links"
  echo "  4) Install / change keys for the nightly run ($(nightly_status))"
  echo "  q) Quit"
  printf '> '
  read -r _m < "$TTY" || exit 1
  case "$_m" in
    1|"") ask_subs || die "no key given"
          [ -z "$COUNTRIES" ] && COUNTRIES=$SAVED_C
          ask_countries; MODE=sub ;;
    2) MODE=podkop ;;
    3) MODE="paste" ;;
    4) nightly_setup; exit 0 ;;
    *) exit 0 ;;
  esac
}

count_nodes() { [ -s "$NODES" ] && wc -l < "$NODES" | tr -d ' ' || echo 0; }

# --- server IP country ------------------------------------------------------

# resolve <host> -> first IPv4 address (public resolvers first: the router's
# own DNS may answer podkop's fake IPs)
resolve() {
  case "$1" in *[!0-9.]*) ;; *) echo "$1"; return ;; esac
  for _ns in 77.88.8.8 8.8.8.8 ""; do
    _a=$(nslookup "$1" $_ns 2>/dev/null | awk '/^Name:/ { n = 1; next } n && /^Address/ { sub(/^Address[^:]*: */, ""); sub(/ .*/, ""); if ($0 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) { print; exit } }')
    case "$_a" in ""|198.18.*|198.19.*) ;; *) echo "$_a"; return ;; esac
  done
}

# entry_countries: <dir>/ecc = country of each node's server IP ("-" when
# unknown or behind a CDN such as Cloudflare, where the IP says nothing)
entry_countries() {
  mkdir -p "$WORK/geo"; : > "$WORK/geo/hosts"
  _i=0
  while [ "$_i" -lt "$COUNT" ]; do
    _i=$((_i + 1)); _d="$WORK/n/$_i"; echo "-" > "$_d/ecc"
    [ -f "$_d/ob.json" ] || continue
    _h=$(sed -n 's/.*"server":"\([^"]*\)".*/\1/p' "$_d/ob.json" | head -n 1)
    [ -n "$_h" ] && printf '%s\t%s\n' "$_i" "$_h" >> "$WORK/geo/hosts"
  done
  [ -s "$WORK/geo/hosts" ] || return 0
  : > "$WORK/geo/ips"
  cut -f 2 "$WORK/geo/hosts" | sort -u | while IFS= read -r _h; do
    _ip=$(resolve "$_h"); [ -n "$_ip" ] && printf '%s\t%s\n' "$_h" "$_ip" >> "$WORK/geo/ips"
  done
  [ -s "$WORK/geo/ips" ] || return 0
  cut -f 2 "$WORK/geo/ips" | sort -u | while IFS= read -r _ip; do
    printf 'url = "https://ipinfo.io/%s/json"\noutput = "%s"\n' "$_ip" "$WORK/geo/$_ip"
  done > "$WORK/geo/req.cfg"
  if [ "$HAS_PARALLEL" = 1 ]; then
    curl -s -Z --parallel-max 8 --max-time 8 -K "$WORK/geo/req.cfg" 2>/dev/null
  else
    curl -s --max-time 8 -K "$WORK/geo/req.cfg" 2>/dev/null
  fi
  while IFS="$TAB" read -r _n _h; do
    _ip=$(awk -F "$TAB" -v h="$_h" '$1 == h { print $2; exit }' "$WORK/geo/ips")
    [ -n "$_ip" ] || continue
    [ -f "$WORK/geo/$_ip" ] || continue
    _org=$(sed -n 's/.*"org": *"\([^"]*\)".*/\1/p' "$WORK/geo/$_ip" | head -n 1)
    case "$_org" in *[Cc]loudflare*|*Fastly*|*Akamai*|*G-Core*|*Gcore*|*CDN77*|*CloudFront*) continue ;; esac
    _c=$(sed -n 's/.*"country": *"\([A-Z][A-Z]\)".*/\1/p' "$WORK/geo/$_ip" | head -n 1)
    [ -n "$_c" ] && echo "$_c" > "$WORK/n/$_n/ecc"
  done < "$WORK/geo/hosts"
}

# --- per-node worker ---------------------------------------------------------

# port_busy <port>: some socket has it as its local port (listening, or an
# open or closing connection: either stops sing-box from listening on it), or
# sing-box failed to listen on it in this run
port_busy() {
  grep -qx "$1" "$WORK/ports.bad" 2>/dev/null && return 0
  awk -v p=":$(printf '%04X' "$1")" 'substr($2, length($2) - 4) == p { f = 1; exit } END { exit !f }' \
    /proc/net/tcp /proc/net/tcp6 2>/dev/null
}

# One sing-box runs every node: node n is outbound n-<n> behind its own local
# port (inbound in-<n>). Starting it once instead of once per node saves the
# start-up of each node and ~40 MB of RAM per node tested at the same time.
SB_PID=""

# sb_config: the config for every node with a port in $WORK/ports. With
# --via every node and the server names' DNS go through the SSH outbound
# "via", and inbound "in-via" (port $WORK/via.port) is the router's own line.
sb_config() {
  if [ -n "$VIA" ]; then
    _vh=${VIA#*@}
    printf '{"log":{"level":"error"},"dns":{"servers":[{"type":"tcp","tag":"local","server":"77.88.8.8","detour":"via"},{"type":"local","tag":"here"}]},"inbounds":['
    printf '{"type":"mixed","tag":"in-via","listen":"127.0.0.1","listen_port":%s},' "$(cat "$WORK/via.port")"
  else
    printf '{"log":{"level":"error"},"dns":{"servers":[{"type":"local","tag":"local"}]},"inbounds":['
  fi
  awk -F "$TAB" '{ printf "%s{\"type\":\"mixed\",\"tag\":\"in-%s\",\"listen\":\"127.0.0.1\",\"listen_port\":%s}", (NR > 1 ? "," : ""), $1, $2 }' "$WORK/ports"
  printf '],"outbounds":['
  _bsep=""
  while IFS="$TAB" read -r _bn _bpp; do
    printf '%s' "$_bsep"; jq -c --arg t "n-$_bn" --arg v "$VIA" '.tag = $t | if $v != "" then .detour = "via" else . end' "$WORK/n/$_bn/ob.json"; _bsep=","
  done < "$WORK/ports"
  if [ -n "$VIA" ]; then
    printf '%s' "$_bsep"; _bsep=","
    jq -nc --arg u "${VIA%%@*}" --arg h "${_vh%:*}" --arg p "${_vh##*:}" --arg k "$VIA_KEY" --arg hk "$VIA_HOST_KEY" \
      '{type: "ssh", tag: "via", server: $h, server_port: ($p | tonumber), user: $u, private_key_path: $k, domain_resolver: "here",
        host_key: [$hk], host_key_algorithms: ($hk | split(" ")[0] | if . == "ssh-rsa" then ["rsa-sha2-256", "rsa-sha2-512", .] else [.] end)}' | tr -d '\n'
  fi
  printf '%s{"type":"direct","tag":"direct"}],"route":{"rules":[' "$_bsep"
  [ -n "$VIA" ] && printf '{"inbound":["in-via"],"outbound":"via"},'
  awk -F "$TAB" '{ printf "%s{\"inbound\":[\"in-%s\"],\"outbound\":\"n-%s\"}", (NR > 1 ? "," : ""), $1, $1 }' "$WORK/ports"
  printf '],"final":"n-%s","default_domain_resolver":"local"}}\n' "$(head -n 1 "$WORK/ports" | cut -f 1)"
}

# sb_all_start: start that sing-box. A node it refuses gets <dir>/nostart
# with sing-box's message, and it starts again without that node. Fails
# when no node is left or sing-box does not come up.
sb_all_start() {
  _btry=0
  while :; do
    : > "$WORK/ports"
    _bi=0; _bp=$BASEPORT
    if [ -n "$VIA" ]; then
      while port_busy "$_bp"; do _bp=$((_bp + 1)); done
      echo "$_bp" > "$WORK/via.port"; _bp=$((_bp + 1))
    fi
    while [ "$_bi" -lt "$COUNT" ]; do
      _bi=$((_bi + 1)); _bd="$WORK/n/$_bi"; rm -f "$_bd/port"
      { [ -f "$_bd/ob.json" ] && [ ! -f "$_bd/skip" ] && [ ! -f "$_bd/nostart" ]; } || continue
      while port_busy "$_bp"; do _bp=$((_bp + 1)); done
      printf '%s\t%s\n' "$_bi" "$_bp" >> "$WORK/ports"
      _bp=$((_bp + 1))
    done
    [ -s "$WORK/ports" ] || return 1
    sb_config > "$WORK/sb.json"
    # started from a subshell, so the "wait" for the test jobs doesn't wait for it
    ( sing-box run -c "$WORK/sb.json" > "$WORK/sb.log" 2>&1 & echo $! > "$WORK/pids/sing-box" )
    SB_PID=$(cat "$WORK/pids/sing-box")
    _blast=$(tail -n 1 "$WORK/ports" | cut -f 2); _bsk=0
    # inbounds open in order: the last port listening means all are
    while kill -0 "$SB_PID" 2>/dev/null && ! port_busy "$_blast"; do
      _bsk=$((_bsk + 1)); [ "$_bsk" -ge "$WAIT_TICKS" ] && break
      sleep "$NAP"
    done
    if kill -0 "$SB_PID" 2>/dev/null && port_busy "$_blast"; then
      while IFS="$TAB" read -r _bn _bpp; do echo "$_bpp" > "$WORK/n/$_bn/port"; done < "$WORK/ports"
      return 0
    fi
    sb_kill
    _btry=$((_btry + 1)); [ "$_btry" -lt 30 ] || return 1
    # "outbounds[3]": the 4th node in the config; a port taken meanwhile: again
    _bk=$(grep -o 'outbounds\{0,1\}\[[0-9]*\]' "$WORK/sb.log" | head -n 1 | tr -dc '0-9')
    if [ -n "$_bk" ]; then
      _bn=$(sed -n "$((_bk + 1))p" "$WORK/ports" | cut -f 1)
      [ -n "$_bn" ] || return 1
      sed -n 's/^.*FATAL[^ ]* *//p' "$WORK/sb.log" | head -n 1 | sed 's/^/sing-box: /' > "$WORK/n/$_bn/nostart"
      [ -s "$WORK/n/$_bn/nostart" ] || echo "sing-box did not start it" > "$WORK/n/$_bn/nostart"
    elif grep -q 'address already in use' "$WORK/sb.log"; then
      # taken since we looked (by a connection): never that one again
      sed -n 's/.*listen tcp [0-9.]*:\([0-9]*\): bind: address already in use.*/\1/p' "$WORK/sb.log" >> "$WORK/ports.bad"
    else
      sb_find_bad || return 1
    fi
  done
}

# sb_kill: stop the shared sing-box and wait until it is gone
sb_kill() {
  [ -n "$SB_PID" ] || return 0
  kill "$SB_PID" 2>/dev/null
  _bw=0; while kill -0 "$SB_PID" 2>/dev/null && [ "$_bw" -lt 25 ]; do sleep "$NAP"; _bw=$((_bw + 1)); done
  kill -9 "$SB_PID" 2>/dev/null; rm -f "$WORK/pids/sing-box"; SB_PID=""
}

# sb_find_bad: sing-box did not say which node it refused: check them one
# by one; fails when every node passes the check
sb_find_bad() {
  _bad=0
  while IFS="$TAB" read -r _bn _bpp; do
    printf '{"log":{"disabled":true},"outbounds":[%s]}\n' "$(cat "$WORK/n/$_bn/ob.json")" > "$WORK/chk.json"
    if ! _bm=$(sing-box check -c "$WORK/chk.json" 2>&1); then
      printf 'sing-box: %s\n' "$(printf '%s' "$_bm" | tail -n 1)" > "$WORK/n/$_bn/nostart"; _bad=1
    fi
  done < "$WORK/ports"
  rm -f "$WORK/chk.json"
  [ "$_bad" = 1 ]
}

# sb_alive: the shared sing-box still runs; if it died (a node crashed it,
# or memory ran out), start it again
# ($WORK/sb.restarts: "<last seen running> <found stopped>" in seconds)
SB_SEEN=0
sb_alive() {
  [ -n "$SB_PID" ] || return 1
  if kill -0 "$SB_PID" 2>/dev/null; then SB_SEEN=$(date +%s); return 0; fi
  echo "$SB_SEEN $(date +%s)" >> "$WORK/sb.restarts"
  sb_all_start
}

# sb_start <idx>: the local port of node idx -> _port; fails if sing-box
# refused the node
sb_start() { _port=$(cat "$WORK/n/$1/port" 2>/dev/null); [ -n "$_port" ]; }

sb_stop() { :; }

# speed_fetch <url>: download <url> through the node (again, if it ends
# early) and watch curl's progress every second. Stops when the running
# average has settled (changed <3% twice in a row, after 2 s), when the
# outcome is clear anyway (after 2 s 85% of the line's own speed and above
# MIN_SPEED: the node can't show more and passes; after 3 s under a third of
# MIN_SPEED: it is out), or after SPEED_T seconds -> Mbit/s on stdout; on
# failure the reason and exit 1.
LINE_MBIT=0
SPEED_AWK='
function b(x, m) { m = 1; if (x ~ /k$/) m = 1024; else if (x ~ /M$/) m = 1048576; else if (x ~ /G$/) m = 1073741824
  sub(/[kMG]$/, "", x); return x * m }
# progress meter records: % Total % Received % Xferd AvgDload AvgUpload ...
NF >= 12 && $1 ~ /^[0-9]+$/ { r = b($4); a = b($7) }
END { printf "%.0f %.0f\n", r, a }'
speed_fetch() {
  # _wB/_wT: bytes and time at 1 s: the speed counts from there, after the
  # tunnel's ramp-up, so a short test gives the steady speed
  _u=$1; _B=0; _T=0; _prev=0; _stab=0; _done=0; _wB=0; _wT=0
  while [ "$_done" = 0 ]; do
    _rem=$(awk -v a="$SPEED_T" -v t="$_T" 'BEGIN { r = a - t; if (r < 0.5) exit 1; printf "%.1f", r }') || break
    curl -o /dev/null --noproxy '' -x "$_px" --max-time "$_rem" \
      -w '%{http_code} %{size_download} %{time_total}\n' "$_u" > "$_sd/sp.out" 2> "$_sd/sp.prog" &
    _cp=$!
    while sleep 1; kill -0 "$_cp" 2>/dev/null; do
      set -- $(tr '\r' '\n' < "$_sd/sp.prog" | awk "$SPEED_AWK")
      # -> "stop B T avg stab prev wB wT" using this chunk's bytes so far and avg speed
      set -- $(awk -v B="$_B" -v T="$_T" -v rb="${1:-0}" -v ra="${2:-0}" -v p="$_prev" -v st="$_stab" -v lim="$SPEED_T" \
          -v line="$LINE_MBIT" -v mins="$MIN_SPEED" -v wb="$_wB" -v wt="$_wT" 'BEGIN {
        if (ra <= 0) { print 0, B, T, 0, 0, p, wb, wt; exit }
        t = T + rb / ra
        if (wt == 0 && t >= 1) { wb = B + rb; wt = t }
        avg = (wt > 0 && t - wt >= 0.5) ? (B + rb - wb) / (t - wt) : (B + rb) / t; mb = avg * 8 / 1000000
        st = (t >= 2 && p > 0 && (avg > p ? avg - p : p - avg) < 0.03 * avg) ? st + 1 : 0
        clear = (t >= 2 && line > 0 && mb >= 0.85 * line && mb > mins) || (t >= 3 && mb < mins / 3)
        printf "%d %.0f %.3f %.0f %d %.0f %.0f %.3f\n", (st >= 2 || clear || t >= lim - 0.3), B + rb, t, avg, st, avg, wb, wt }')
      _stab=$5; _prev=$6; _wB=${7:-0}; _wT=${8:-0}
      if [ "$1" = 1 ]; then
        kill "$_cp" 2>/dev/null; wait "$_cp" 2>/dev/null
        _B=$2; _T=$3; _done=1; break
      fi
    done
    [ "$_done" = 1 ] && break
    wait "$_cp" 2>/dev/null
    set -- $(cat "$_sd/sp.out" 2>/dev/null)
    if [ -z "${2:-}" ] || [ "${2:-0}" -lt 100000 ] 2>/dev/null; then
      [ "$_B" = 0 ] && { _h=${_u#*://}; echo "HTTP ${1:-000} from ${_h%%/*}"; return 1; }
      break
    fi
    # a whole chunk arrived: add it and check the running average again
    set -- $(awk -v B="$_B" -v T="$_T" -v b="$2" -v t="${3:-0}" -v p="$_prev" -v st="$_stab" -v lim="$SPEED_T" \
        -v line="$LINE_MBIT" -v mins="$MIN_SPEED" 'BEGIN {
      B += b; T += t; avg = (T > 0) ? B / T : 0; mb = avg * 8 / 1000000
      st = (T >= 2 && p > 0 && (avg > p ? avg - p : p - avg) < 0.03 * avg) ? st + 1 : 0
      clear = (T >= 2 && line > 0 && mb >= 0.85 * line && mb > mins) || (T >= 3 && mb < mins / 3)
      printf "%d %.0f %.3f %d %.0f\n", (st >= 2 || clear || T >= lim - 0.3), B, T, st, avg }')
    _B=$2; _T=$3; _stab=$4; _prev=$5
    [ "$1" = 1 ] && break
  done
  awk -v b="$_B" -v t="$_T" -v wb="$_wB" -v wt="$_wT" 'BEGIN {
    if (wt > 0 && t - wt >= 1) printf "%.1f\n", (b - wb) * 8 / (t - wt) / 1000000
    else if (t > 0) printf "%.1f\n", b * 8 / t / 1000000; else print "0.0" }'
}

# speed_measure <dir>: download speed through the running node -> <dir>/speed
# in Mbit/s (reason for a failure in <dir>/speed.why)
speed_measure() {
  _sd=$1; _why=""; _res=""
  set -f
  for _su in ${SPEED_URL:-$SPEED_URLS}; do
    if _res=$(speed_fetch "$_su"); then _why=""; break; fi
    _why="${_why:+$_why, }$_res"; _res=""
  done
  set +f
  echo "${_res:-0.0}" > "$1/speed"
  [ -n "$_why" ] && echo "$_why" > "$1/speed.why"
}

# fire <curl-config> <times-file>: the requests of one stage
fire() {
  if [ "$HAS_PARALLEL" = 1 ]; then
    curl -s -K "$1" -Z --parallel-max "$C" --parallel-immediate \
      --max-time "$T" --noproxy '' -x "$_px" -w '%{http_code} %{time_total}\n' >> "$2" 2>/dev/null
  else
    curl -s -K "$1" --max-time "$T" --noproxy '' -x "$_px" -w '%{http_code} %{time_total}\n' >> "$2" 2>/dev/null
  fi
}

# speed_node <idx>: start the node again, measure its speed and, if fast
# enough, check YouTube through it
speed_node() {
  _sn=$1
  if sb_start "$_sn"; then
    _px="socks5h://127.0.0.1:$_port"
    speed_measure "$WORK/n/$_sn"
    # fast enough: check YouTube now, while the node is up
    if [ "$YOUTUBE" = 1 ] && awk -v s="$(cat "$WORK/n/$_sn/speed")" -v y="$MIN_SPEED" 'BEGIN { exit !(s + 0 > y) }'; then
      yt_measure "$_sn"
    fi
  else
    echo "0.0" > "$WORK/n/$_sn/speed"; echo "sing-box did not start" > "$WORK/n/$_sn/speed.why"
  fi
  sb_stop "$_sn"
}

# same_key <idx>: links that differ only in the name share a speed test
# (e.g. "Германия #1" and "Германия #1 (для iOS)"); relays with one entry
# host:port but another SNI, path or key are different servers
same_key() {
  if [ -f "$WORK/n/$1/link" ]; then
    printf 'L %s\n' "$(sed 's/#.*//' "$WORK/n/$1/link" | tr -d '\t\n')"
  elif _ob=$(jq -cS 'del(.tag)' "$WORK/n/$1/ob.json" 2>/dev/null) && [ -n "$_ob" ]; then
    printf 'O %s\n' "$_ob"
  else
    echo "node $1"
  fi
}

run_node() {
  _i=$1; _d="$WORK/n/$_i"
  date +%s > "$_d/t0"
  _st=ok; _ip=""; _cc=""
  if [ -f "$_d/skip" ]; then
    _st=skip
  elif ! sb_start "$_i"; then
    _st=start
  fi

  : > "$_d/times"
  if [ "$_st" = ok ]; then
    _px="socks5h://127.0.0.1:$_port"
    exit_ip "$_px" > "$_d/exit" &                  # runs alongside the requests
    _epid=$!

    # stage 0: a few requests; if none gets through the node is dead
    # stage 1: up to half the series; a node losing >30% by then is bad
    #          enough, no need to wait for the rest to time out
    # stage 2: the rest
    fire "$WORK/req0.cfg" "$_d/times"
    if grep -qv '^000' "$_d/times"; then
      [ -s "$WORK/req1.cfg" ] && fire "$WORK/req1.cfg" "$_d/times"
      if [ -s "$WORK/req2.cfg" ] && awk '$1 == "000" { f++ } END { exit !(f * 10 <= NR * 3) }' "$_d/times"; then
        fire "$WORK/req2.cfg" "$_d/times"
      fi
    fi
    wait "$_epid" 2>/dev/null
    set -- $(cat "$_d/exit")
    _ip=${1:-}; _cc=${2:-}
    [ "${_cc:--}" = "-" ] && _cc=$(cat "$_d/ncc" 2>/dev/null)
    if [ -z "$_ip" ]; then _st=noexit
    elif [ "$_ip" = "$DIRECT_IP" ]; then _st=direct
    fi
    cc_allowed "${_cc:--}" || _st=region
    sb_stop "$_i"
  elif [ "$_st" = start ]; then
    sb_stop "$_i"
  fi

  # status ok total min median p90 (total = requests actually sent)
  set -- $(awk '$1 != "000" {print $2}' "$_d/times" | sort -n | awk -v tot="$(wc -l < "$_d/times")" -v st="$_st" '
    { a[++k] = $1 }
    END {
      if (st == "skip" || st == "start") { print st, 0, 0, "-", "-", "-"; exit }
      if (!k) { print st, 0, tot, "-", "-", "-"; exit }
      med = (k % 2) ? a[(k + 1) / 2] : (a[k / 2] + a[k / 2 + 1]) / 2
      p = int(k * 0.9); if (p < k * 0.9) p++; if (p < 1) p = 1
      printf "%s %d %d %.3f %.3f %.3f\n", st, k, tot, a[1], med, a[p]
    }')
  _sp=$(cat "$_d/speed" 2>/dev/null); [ -n "$_sp" ] || _sp=-
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "${_ip:--}" "${_cc:--}" "$_sp" "$(cat "$_d/name")" > "$_d/result"
  date +%s > "$_d/t1"
  print_progress "$_i" "$_d/result"
}

# curl through $_px when set; --noproxy '' stops NO_PROXY from bypassing it
pcurl() { if [ -n "$_px" ]; then curl --noproxy '' -x "$_px" "$@"; else curl "$@"; fi; }

# exit_ip [proxy] -> "ip country"
exit_ip() {
  _px=${1:-}
  _r=0
  while [ "$_r" -lt 1 ]; do
    _j=$(pcurl -s --max-time 5 https://ipinfo.io/json 2>/dev/null)
    _ip=$(printf '%s\n' "$_j" | sed -n 's/.*"ip": *"\([^"]*\)".*/\1/p' | head -n 1)
    _cc=$(printf '%s\n' "$_j" | sed -n 's/.*"country": *"\([^"]*\)".*/\1/p' | head -n 1)
    if [ -z "$_ip" ]; then
      _ip=$(pcurl -s --max-time 5 https://api.ipify.org 2>/dev/null | grep -oE '^[0-9a-fA-F.:]+$')
    fi
    [ -n "$_ip" ] && { echo "$_ip ${_cc:--}"; return; }
    _r=$((_r + 1))
  done
}

# yt_check -> "state country": YouTube through the node ($_px). country is
# where YouTube thinks we are (the code next to its logo); state is
#   ok    a video plays and the video servers (googlevideo) answer
#   bot   "Sign in to confirm you're not a bot": YouTube flagged the IP
#   no    "Video unavailable", or the video servers are blocked
#   fail  YouTube did not load at all
yt_check() {
  _yp=$(pcurl -s -L --max-time 12 -A "$YT_UA" -H 'Accept-Language: en-US,en;q=0.9' \
      -b 'SOCS=CAI; CONSENT=YES+' "https://www.youtube.com/watch?v=$YT_VIDEO&hl=en" 2>/dev/null |
    grep -oE '"(GL|INNERTUBE_CONTEXT_GL)":"[A-Z]{2}"|"playabilityStatus":[{]"status":"[A-Z_]+"' | head -n 20)
  _gl=$(printf '%s\n' "$_yp" | sed -n 's/.*GL":"\([A-Z][A-Z]\)"$/\1/p' | head -n 1)
  _ps=$(printf '%s\n' "$_yp" | sed -n 's/.*"status":"\([A-Z_]*\)"$/\1/p' | head -n 1)
  [ -n "$_gl$_ps" ] || { echo "fail -"; return; }
  case "$_ps" in
    OK|"") ;;
    LOGIN_REQUIRED) echo "bot ${_gl:--}"; return ;;
    *) echo "no ${_gl:--}"; return ;;
  esac
  case "$(pcurl -s -o /dev/null --max-time 6 -w '%{http_code}' https://redirector.googlevideo.com/generate_204 2>/dev/null)" in
    ""|000) echo "no ${_gl:--}" ;;
    *) echo "ok ${_gl:--}" ;;
  esac
}

# yt_bad <n>: YouTube was checked and does not work through node n
yt_bad() { case "$(cut -d ' ' -f 1 "$WORK/n/$1/yt" 2>/dev/null)" in bot|no|fail) return 0 ;; esac; return 1; }

# yt_col <n>: YouTube's country for node n, "no" if YouTube doesn't work
yt_col() {
  if [ ! -f "$WORK/n/$1/yt" ]; then echo "-"; return; fi
  read -r _ys _yc < "$WORK/n/$1/yt"
  if yt_bad "$1"; then echo "no"; else echo "$_yc"; fi
}

# yt_fmt <n>: yt_col padded, green for an ad-free country, red for "no"
yt_fmt() {
  _yv=$(yt_col "$1")
  case "$_yv" in
    no) printf '%s%-3s%s ' "$RD" no "$R0" ;;
    *) case " $YT_NOADS " in *" $_yv "*) printf '%s%-3s%s ' "$GR" "$_yv" "$R0" ;; *) printf '%-3s ' "$_yv" ;; esac ;;
  esac
}

# fast_enough <n>: node n works, its median ping is under MAX_PING and its
# speed (when measured) at least MIN_SPEED
fast_enough() {
  IFS="$TAB" read -r _fs _fok _ftot _fmn _fmd _frest < "$WORK/n/$1/result" || return 1
  case "$(verdict "$_fs" "$_fok" "$_ftot")" in GOOD|OK|FLAKY) ;; *) return 1 ;; esac
  awk -v m="$_fmd" -v s="$(cat "$WORK/n/$1/speed" 2>/dev/null)" -v x="$MAX_PING" -v y="$MIN_SPEED" -v sp="$SPEED" 'BEGIN {
    if (m == "-" || m * 1000 >= x) exit 1
    if (sp == 1 && (s == "" || s + 0 <= y)) exit 1 }'
}

# yt_measure <n>: through the running node ($_px): YouTube check -> yt,
# and if YouTube plays, the median ping to YouTube itself -> yt.ms
yt_measure() {
  _yd="$WORK/n/$1"
  yt_check > "$_yd/yt"
  read -r _ys _yc < "$_yd/yt"
  [ "$_ys" = ok ] || return 0
  # only servers YouTube places in a YT_NOADS country go into the YouTube
  # list, so the others need no YouTube ping
  [ -n "$YT_NOADS" ] && case " $YT_NOADS " in *" $_yc "*) ;; *) return 0 ;; esac
  : > "$_yd/yt.times"
  fire "$WORK/ytreq.cfg" "$_yd/yt.times"
  # at least half must get through, or YouTube is too unreliable here
  awk '$1 != "000" { print $2 }' "$_yd/yt.times" | sort -n | awk -v tot="$(wc -l < "$_yd/yt.times")" '
    { a[++k] = $1 }
    END { if (k * 2 < tot || !k) exit 1; printf "%.3f\n", (k % 2) ? a[(k + 1) / 2] : (a[k / 2] + a[k / 2 + 1]) / 2 }' > "$_yd/yt.ms" ||
    { rm -f "$_yd/yt.ms"; sed -i 's/^ok /no /' "$_yd/yt"; }
  return 0
}

# yt_node <n>: start node n again for yt_measure (only needed without the
# speed test, which otherwise does it while the node runs)
yt_node() {
  if sb_start "$1"; then
    _px="socks5h://127.0.0.1:$_port"
    yt_measure "$1"
  else
    echo "fail -" > "$WORK/n/$1/yt"
  fi
  sb_stop "$1"
}

# verdict <status> <ok> <total> -> "LABEL<TAB>color"
verdict() {
  case "$1" in
    skip)   echo "SKIPPED" ; return ;;
    start)  echo "NO START"; return ;;
    direct) echo "NOT VIA NODE"; return ;;
    region) echo "OTHER REGION"; return ;;
  esac
  [ "$3" -gt 0 ] || { echo "DEAD"; return; }
  _f=$(( ($3 - $2) * 1000 / $3 ))
  if   [ "$2" -eq 0 ];   then echo "DEAD"
  elif [ "$_f" -eq 0 ];  then echo "GOOD"
  elif [ "$_f" -le 50 ]; then echo "OK"
  elif [ "$_f" -le 200 ]; then echo "FLAKY"
  else echo "BAD"; fi
}

vcolor() {
  case "$1" in GOOD) echo "$GR" ;; OK) echo "$GR" ;; FLAKY) echo "$YL" ;; *) echo "$RD" ;; esac
}

# progress: a single updating line on a terminal, per-node lines with -v
progress() { [ "$PROG" = 1 ] && printf '\r  %s ' "$*"; return 0; }

print_progress() {
  echo "$1" >> "$WORK/lat.done"
  if [ "$VERBOSE" != 1 ]; then progress "testing  $(wc -l < "$WORK/lat.done")/$COUNT"; return; fi
  IFS="$TAB" read -r _s _ok _tot _mn _md _p9 _xi _xc _sp _nm < "$2"
  _v=$(verdict "$_s" "$_ok" "$_tot")
  printf '  [%*d/%d] %s%-12s%s %5s  med %-6s  %-15s %-3s %s\n' \
    "${#COUNT}" "$1" "$COUNT" "$(vcolor "$_v")" "$_v" "$R0" "$_ok/$_tot" "$_md" "$_xi" "$_xc" "$_nm"
}


# --- nightly run -------------------------------------------------------------

shq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

nightly_status() {
  if [ ! -x "$BIN" ]; then echo "not installed"
  elif [ -f "$CONF" ]; then ( . "$CONF"; printf 'installed, %s key(s), %s' "$(printf '%s' "$SUBS" | grep -c .)" "$(countries_label)" )
  else echo "installed, no keys"; fi
}

ask_time() {
  printf 'Time, HH:MM [%s]: ' "${CRON_TIME:-04:30}"
  read -r _t < "$TTY" || _t=""
  CRON_TIME=${_t:-${CRON_TIME:-04:30}}
}

# cron_line: the line for LuCI -> System -> Scheduled Tasks
cron_line() {
  case "${CRON_TIME:=04:30}" in [0-9]:[0-5][0-9]|[01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) ;;
    *) die "time must be HH:MM, got '$CRON_TIME'" ;; esac
  _h=$(echo "${CRON_TIME%%:*}" | sed 's/^0\(.\)/\1/'); _mi=$(echo "${CRON_TIME#*:}" | sed 's/^0\(.\)/\1/')
  echo
  printf '%sNightly run: paste this into LuCI -> System -> Scheduled Tasks and Save:%s\n\n' "$BD" "$R0"
  printf '    %s %s * * * podkop-probe --cron\n\n' "$_mi" "$_h"
}

# script_ok <file>: a whole podkop-probe (a cut-off download lacks the last line)
script_ok() {
  grep -q '^VERSION=' "$1" 2>/dev/null && [ "$(tail -n 1 "$1")" = "# end of podkop-probe" ] && sh -n "$1" 2>/dev/null
}

# install_self: copy this script to $BIN (download it when run from a pipe)
install_self() {
  _src=""
  case "$0" in /dev/*|/proc/*|sh|-sh|ash|-ash|bash) ;; *) [ -f "$0" ] && _src=$0 ;; esac
  if [ -n "$_src" ] && grep -q '^VERSION=' "$_src"; then
    [ "$_src" -ef "$BIN" ] && return 0
    cp "$_src" "$BIN.new" || return 1
  else
    fetch "$RELEASE_URL" > "$BIN.new" 2>/dev/null || { rm -f "$BIN.new"; return 1; }
  fi
  script_ok "$BIN.new" || { rm -f "$BIN.new"; return 1; }
  chmod +x "$BIN.new" && mv "$BIN.new" "$BIN"
  grep -qxF "$BIN" /etc/sysupgrade.conf 2>/dev/null || echo "$BIN" >> /etc/sysupgrade.conf
}

# save_conf: keys and options for the nightly run
save_conf() {
  {
    echo "# podkop-probe settings for the nightly run (podkop-probe --cron)"
    echo "SUBS=$(shq "$SUBS")"
    echo "COUNTRIES=$(shq "$COUNTRIES")"
    echo "TOP=$TOP"
    echo "SECTION=$(shq "$SECTION")"
    [ -n "$SPEED_J_USER" ] && echo "SPEED_J=$SPEED_J_USER"
    [ "$ENTRY_CHECK" = 0 ] && echo "ENTRY_CHECK=0"
    [ "$YOUTUBE" = 0 ] && echo "YOUTUBE=0"
    [ "$YT_NOADS" != RU ] && echo "YT_NOADS=$(shq "$YT_NOADS")"
    [ "$YT_SECTION" != youtube ] && echo "YT_SECTION=$(shq "$YT_SECTION")"
    [ "$APPLY_TO" != both ] && echo "APPLY_TO=$APPLY_TO"
    [ "$MAX_PING" != 700 ] && echo "MAX_PING=$MAX_PING"
    [ "$MIN_SPEED" != 40 ] && echo "MIN_SPEED=$MIN_SPEED"
    :
  } > "$CONF" || return 1
  chmod 600 "$CONF"
  grep -qxF "$CONF" /etc/sysupgrade.conf 2>/dev/null || echo "$CONF" >> /etc/sysupgrade.conf
}

# install + save + show the cron line
# install the script (+ keys, if any) and show what to put into cron
nightly_install() {
  install_self || die "could not install $BIN"
  if [ -n "$SUBS" ]; then
    save_conf || die "could not write $CONF"
    info "installed $BIN, $(printf '%s' "$SUBS" | grep -c .) key(s) saved ($(countries_label))"
  elif [ -n "$SAVED_SUBS" ]; then
    info "installed $BIN, your saved keys are kept"
  else
    info "installed $BIN (no keys yet: run 'podkop-probe' and choose 4 to add them)"
    return 0
  fi
  if grep -qs 'podkop-probe --cron' "$CRONTAB"; then
    info "the nightly line is already in Scheduled Tasks: $(grep 'podkop-probe --cron' "$CRONTAB" | head -n 1)"
  else
    cron_line
  fi
  return 0
}

# ask for keys, countries and time, then install
nightly_setup() {
  ask_subs || { nightly_install; return; }
  [ -z "$COUNTRIES" ] && COUNTRIES=$SAVED_C
  ask_countries
  grep -qs 'podkop-probe --cron' "$CRONTAB" || ask_time
  nightly_install
}

# self_update: replace $BIN with the latest release if it differs
self_update() {
  [ -f "$BIN" ] || return 1
  # downloaded to /tmp (RAM): $BIN's flash is written only for a newer version
  _dl="/tmp/podkop-probe.update.$$"
  if ! fetch "$RELEASE_URL" > "$_dl" 2>/dev/null || ! script_ok "$_dl"; then rm -f "$_dl"; return 1; fi
  _nv=$(sed -n 's/^VERSION="\(.*\)"/\1/p' "$_dl" 2>/dev/null)
  if [ -n "$_nv" ] && awk -v a="$_nv" -v b="$VERSION" 'BEGIN { n = split(a, x, "."); split(b, y, ".")
       for (i = 1; i <= n; i++) if (x[i] + 0 != y[i] + 0) exit !(x[i] + 0 > y[i] + 0); exit 1 }' &&
     cp "$_dl" "$BIN.new" && chmod +x "$BIN.new" && mv "$BIN.new" "$BIN"; then
    rm -f "$_dl"; info "updated $VERSION -> $_nv"; return 0
  fi
  rm -f "$_dl" "$BIN.new"; return 1
}

uninstall() {
  rm -f "$BIN" "$CONF"
  [ -f "$CRONTAB" ] && sed -i '/podkop-probe --cron/d' "$CRONTAB" && /etc/init.d/cron restart >/dev/null 2>&1
  [ -f /etc/sysupgrade.conf ] && sed -i "\\|^$BIN\$|d; \\|^$CONF\$|d" /etc/sysupgrade.conf
  info "removed $BIN, $CONF and the Scheduled Tasks line"
}

# --- podkop settings --------------------------------------------------------

# set_urltest <section> <links file> <fallback links file>: the section
# becomes a URLTest of the links, with the fallback links behind them
set_urltest() {
  uci -q delete "podkop.$1.urltest_proxy_links"
  uci -q delete "podkop.$1.urltest_fallback_links"
  uci set "podkop.$1.connection_type=proxy"
  uci set "podkop.$1.proxy_config_type=urltest"
  while IFS= read -r _l; do uci add_list "podkop.$1.urltest_proxy_links=$_l"; done < "$2"
  while IFS= read -r _l; do uci add_list "podkop.$1.urltest_fallback_links=$_l"; done < "$3"
}

# podkop_up <section> ...: wait until podkop's sing-box runs the URLTest
# group of every given section. podkop starts in the background, so the exit
# code of its restart says nothing; a failed start shows in procd as a
# finished "podkop start" with a non-zero exit code.
podkop_up() {
  _w=0
  while [ "$_w" -lt 90 ]; do
    sleep 3; _w=$((_w + 3))
    ubus call service list '{"name":"podkop"}' 2>/dev/null |
      jq -e '[.podkop.instances[]? | select(.running == false and (.exit_code // 0) != 0)] | length > 0' >/dev/null && return 1
    _pj=$(podkop clash_api get_proxies 2>/dev/null)
    _up=1
    for _s in "$@"; do printf '%s' "$_pj" | grep -q "\"$_s-urltest-out\"" || _up=0; done
    [ "$_up" = 1 ] && return 0
  done
  return 1
}

# sec_pos <section>: its position among podkop's sections (0 = first)
sec_pos() { uci show podkop 2>/dev/null | sed -n 's/^podkop\.\([^.=]*\)=.*/\1/p' | grep -nxF "$1" | cut -d: -f1 | awk '{ print $1 - 1; exit }'; }

# ensure_section <name>: create podkop section <name> if missing
ensure_section() {
  uci -q get "podkop.$1" >/dev/null && return 0
  uci set "podkop.$1=section"
  _msg="${_msg:+$_msg, }new section $1"
}

# apply_podkop: the main list (DO_MAIN) into section $SECTION and the YouTube
# list (DO_YT) into $YT_SECTION, each as a URLTest; then restart podkop.
# A new YouTube section gets podkop's "youtube" list and is moved before
# $SECTION: podkop routes by the first section that matches, and lists like
# "Russia inside" contain YouTube too.
# podkop_state: podkop's settings with what uci has staged, the options in any
# order (a list put back moves to the end of its section) but the sections in
# their order (it matters to podkop)
podkop_state() { uci -q show podkop | sort; echo ---; uci -q show podkop | grep -E '^podkop\.[^.]+='; }

apply_podkop() {
  _cfg=/etc/config/podkop
  # not at the same time as an apply or a recovery by podkop-agent
  if [ -d /tmp/podkop-agent ] && command -v flock >/dev/null 2>&1; then
    exec 8> /tmp/podkop-agent/apply.lock
    flock -w 600 8 || die "podkop-agent is busy with podkop's settings; try again later"
  fi
  podkop_state > "$WORK/podkop.before"
  _msg=""; _secs=""
  if [ "$DO_MAIN" = 1 ]; then
    uci -q get "podkop.$SECTION" >/dev/null || _new_main=1
    ensure_section "$SECTION"
    set_urltest "$SECTION" "$WORK/best" "$WORK/best.fb"
    _msg="${_msg:+$_msg, }$SECTION: $(wc -l < "$WORK/best" | tr -d ' ') server(s) + $(wc -l < "$WORK/best.fb" | tr -d ' ') fallback"
    _secs="$SECTION"
  fi
  if [ "$DO_YT" = 1 ]; then
    if ! uci -q get "podkop.$YT_SECTION" >/dev/null; then
      ensure_section "$YT_SECTION"
      uci add_list "podkop.$YT_SECTION.community_lists=youtube"
    fi
    set_urltest "$YT_SECTION" "$WORK/ytbest" "$WORK/ytbest.fb"
    uci set "podkop.$YT_SECTION.urltest_testing_url=$YT_PING_URL"
    _pm=$(sec_pos "$SECTION"); _py=$(sec_pos "$YT_SECTION")
    [ -n "$_pm" ] && [ -n "$_py" ] && [ "$_py" -gt "$_pm" ] && uci reorder "podkop.$YT_SECTION=$_pm"
    _msg="${_msg:+$_msg, }$YT_SECTION: $(wc -l < "$WORK/ytbest" | tr -d ' ') server(s) + $(wc -l < "$WORK/ytbest.fb" | tr -d ' ') fallback"
    _secs="${_secs:-} $YT_SECTION"
  fi
  # the same servers as now: nothing written to flash, podkop not restarted
  if podkop_state | cmp -s - "$WORK/podkop.before"; then
    uci -q revert podkop
    info "podkop already has these servers, nothing to change"
    return 0
  fi
  _bak="$_cfg.probe-backup.$(date +%Y%m%d-%H%M%S)"
  cp "$_cfg" "$_bak" || { uci -q revert podkop; die "cannot back up $_cfg"; }
  ls -1t "$_cfg".probe-backup.* 2>/dev/null | tail -n +4 | while IFS= read -r _f; do rm -f "$_f"; done
  uci commit podkop || die "uci commit failed, your old settings are in $_bak"
  /etc/init.d/podkop restart >/dev/null 2>&1 8>&-
  # shellcheck disable=SC2086
  if podkop_up $_secs; then
    # a server once chosen by hand in the dashboard stays chosen after a
    # restart, by its number in the list; give the choice back to URLTest
    for _s in $_secs; do podkop clash_api set_group_proxy "$_s-out" "$_s-urltest-out" >/dev/null 2>&1; done
    info "podkop updated ($_msg), restarted"
    [ "$CRON" = 1 ] && logger -t podkop-probe "podkop updated ($_msg), restarted" 2>/dev/null
  else
    cp "$_bak" "$_cfg" && /etc/init.d/podkop restart >/dev/null 2>&1 8>&-
    warn "podkop did not start with the new lists, the old settings are back (podkop's log: logread -e podkop)"
    [ "$CRON" = 1 ] && logger -t podkop-probe "podkop did not start with the new lists, old settings restored" 2>/dev/null
  fi
  [ "${_new_main:-0}" = 1 ] && warn "section '$SECTION' is new and has no lists yet: add them in LuCI -> Services -> Podkop"
  [ "$VERBOSE" = 1 ] && info "old settings saved in $_bak"
  return 0
}

# ask_sections: section names for both lists ("-" = don't use that list)
ask_sections() {
  printf 'Section for the main list [%s, - = skip]: ' "$SECTION"
  read -r _a < "$TTY" || _a=-; case "$_a" in -) DO_MAIN=0 ;; "") ;; *) SECTION=$_a ;; esac
  if [ "$DO_YT" = 1 ]; then
    printf 'Section for the YouTube list [%s, - = skip]: ' "$YT_SECTION"
    read -r _a < "$TTY" || _a=-; case "$_a" in -) DO_YT=0 ;; "") ;; *) YT_SECTION=$_a ;; esac
  fi
  [ "$DO_MAIN" = 1 ] && [ "$DO_YT" = 1 ] && [ "$SECTION" = "$YT_SECTION" ] && { warn "both lists can't go into one section"; DO_YT=0; }
  return 0
}

# --- main --------------------------------------------------------------------

SAVED_SUBS=""; SAVED_C=""
if [ -f "$CONF" ]; then
  chmod 600 "$CONF" 2>/dev/null        # older versions left it readable to all
  SAVED_SUBS=$( . "$CONF"; printf '%s' "$SUBS" ); SAVED_C=$( . "$CONF"; printf '%s' "${COUNTRIES:-}" )
fi
if [ "$UNINSTALL" = 1 ]; then uninstall; exit 0; fi
if [ "$UPDATE" = 1 ]; then self_update || info "already the latest version ($VERSION)"; exit 0; fi
if [ "$INSTALL" = 1 ]; then
  if [ -z "$SUBS" ] && [ -n "$TTY" ]; then nightly_setup; else nightly_install; fi
  exit 0
fi
if [ -n "$CRON_TIME" ]; then cron_line; exit 0; fi
if [ "$CRON" = 1 ] && [ "$NO_UPDATE" = 0 ] && [ "$0" -ef "$BIN" ] && self_update; then
  exec sh "$BIN" --cron --no-update
fi

if [ "$CRON" = 1 ]; then
  # the lock holds the run's pid; a run killed with SIGKILL leaves a stale one
  if ! mkdir /tmp/podkop-probe.lock 2>/dev/null; then
    _lp=$(cat /tmp/podkop-probe.lock/pid 2>/dev/null)
    # stale: its run is gone, or it still has no pid a minute after it was made
    if { [ -n "$_lp" ] && ! kill -0 "$_lp" 2>/dev/null; } ||
       { [ -z "$_lp" ] && find /tmp/podkop-probe.lock -mmin +1 2>/dev/null | grep -q .; }; then
      rm -f /tmp/podkop-probe.lock/pid; rmdir /tmp/podkop-probe.lock 2>/dev/null
    fi
    mkdir /tmp/podkop-probe.lock 2>/dev/null || { echo "another run is in progress" >&2; exit 1; }
  fi
  echo $$ > /tmp/podkop-probe.lock/pid
  trap 'rm -f /tmp/podkop-probe.lock/pid; rmdir /tmp/podkop-probe.lock 2>/dev/null' EXIT
fi

cleanup() {
  for _f in "$WORK"/pids/*; do [ -f "$_f" ] && kill "$(cat "$_f")" 2>/dev/null; done
  rm -rf "$WORK"
  [ "$CRON" = 1 ] && rm -f /tmp/podkop-probe.lock/pid && rmdir /tmp/podkop-probe.lock 2>/dev/null
}
mkdir -p "$WORK/n" "$WORK/pids" || die "cannot create $WORK"

trap cleanup EXIT
trap 'echo; warn "interrupted"; exit 130' INT TERM
trap 'exit 129' HUP   # SSH session closed: nothing to print to, just clean up

if [ "$SUB_INFO" = 1 ]; then
  set -f
  for _u in $SUBS; do sub_usage "$_u"; done
  exit 0
fi

# --apply-saved: the lists of the last run into podkop, no test
if [ "$APPLY_SAVED" = 1 ]; then
  if [ ! -f /etc/config/podkop ] || ! command -v uci >/dev/null 2>&1; then die "podkop not found"; fi
  cp "$SAVED_BEST" "$WORK/best" 2>/dev/null || : > "$WORK/best"
  cp "$SAVED_YT" "$WORK/ytbest" 2>/dev/null || : > "$WORK/ytbest"
  cp "$SAVED_FB" "$WORK/best.fb" 2>/dev/null || : > "$WORK/best.fb"
  cp "$SAVED_YTFB" "$WORK/ytbest.fb" 2>/dev/null || : > "$WORK/ytbest.fb"
  DO_MAIN=0; DO_YT=0
  [ -s "$WORK/best" ] && [ "$APPLY_TO" != youtube ] && DO_MAIN=1
  [ -s "$WORK/ytbest" ] && [ "$APPLY_TO" != main ] && [ "$YOUTUBE" = 1 ] && DO_YT=1
  [ "$DO_MAIN$DO_YT" != 00 ] || die "no saved lists: run a test first (they are kept in /tmp until the router restarts)"
  apply_podkop
  exit 0
fi
: > "$NODES"

# podkop's link parser builds the test nodes (here, or on the router for
# --nodes), podkop-engine (its sing-box) runs them
if [ -z "$NODES_FILE" ]; then
  { [ -f "$PODKOP_LIB/sing_box_config_facade.sh" ] && command -v podkop >/dev/null 2>&1; } ||
    die "podkop not found. Install it: $PODKOP_INSTALL"
fi
_feat=$(sing-box version 2>/dev/null | sed -n 's/^Features:[[:space:]]*//p')
for _f in tools.decode-link urltest.fallbacks; do
  case ",$_feat," in *",$_f,"*) ;; *) die "podkop-engine not found (sing-box has no $_f). Install it: $ENGINE_URL" ;; esac
done
need_tool curl curl "it sends the test requests" || die "curl is required"
SB_VER=$(sing-box version 2>/dev/null | awk 'NR == 1 {print $3}')

_model=$(cat /tmp/sysinfo/model 2>/dev/null)
_os=$(sed -n "s/^DISTRIB_DESCRIPTION='\(.*\)'/\1/p" /etc/openwrt_release 2>/dev/null)
_pv=$(podkop show_version 2>/dev/null)
_sys="${_model:+ · $_model}${_os:+ · $_os}${_pv:+ · podkop $_pv} · sing-box $SB_VER"
[ "$BUILD_NODES" = 1 ] || printf '%spodkop-probe %s%s%s\n' "$BD" "$VERSION" "$R0" "$DM$_sys$R0"

# collect nodes
if [ -n "$ARGLINKS" ]; then printf '%s' "$ARGLINKS" | add_links; SOURCE="command line"; fi
if [ -n "$FILE" ]; then
  case "$FILE" in
    -) add_links ;;
    http://*|https://*) fetch "$FILE" | add_links ;;
    *) [ -f "$FILE" ] || die "file not found: $FILE"; add_links < "$FILE" ;;
  esac
  SOURCE="${SOURCE:+$SOURCE + }$FILE"
fi
if [ -n "$NODES_FILE" ]; then
  [ -f "$NODES_FILE" ] || die "file not found: $NODES_FILE"
  add_nodes "$NODES_FILE"; SOURCE="${SOURCE:+$SOURCE + }$NODES_FILE"
fi
MODE=""
if [ -z "$ARGLINKS$FILE$SUBS$CONFIG$NODES_FILE" ] && [ -n "$TTY" ] && [ "$ASSUME_YES" = 0 ]; then
  menu
  [ "$MODE" = paste ] && ask_links
fi
[ -n "$SUBS" ] && from_subs
if [ -n "$CONFIG" ]; then from_config "$CONFIG"; SOURCE="${SOURCE:+$SOURCE + }$CONFIG"; fi

if [ "$(count_nodes)" -eq 0 ] && [ -z "$SOURCE" ]; then
  if _cfg=$(podkop_config_path) && { command -v jq >/dev/null 2>&1 || need_tool jq jq "needed to read podkop's sing-box config"; }; then
    from_config "$_cfg"; SOURCE="podkop sing-box config $_cfg"
  fi
  if [ "$(count_nodes)" -eq 0 ] && from_uci && [ "$(count_nodes)" -gt 0 ]; then
    SOURCE="podkop settings (/etc/config/podkop)"
  fi
  [ "$(count_nodes)" -gt 0 ] || ask_links
fi

# drop duplicates (the same link in several subscriptions differs only in #name)
awk '{ k = $0; if (substr(k, 1, 1) == "L") sub(/#.*/, "", k) } !seen[k]++' "$NODES" > "$NODES.u"
mv "$NODES.u" "$NODES"
COUNT=$(count_nodes)
if [ "$COUNT" -eq 0 ]; then
  warn "no proxy nodes found${SOURCE:+ in $SOURCE}"
  echo "Is podkop installed and configured? Or pass links / -s URL / -f FILE. See -h." >&2
  exit 1
fi

_i=0; OUT_REGION=0
while IFS= read -r _line; do
  _i=$((_i + 1)); _d="$WORK/n/$_i"; mkdir -p "$_d"
  case "$_line" in
    L"$TAB"*)
      fix_fp "${_line#L"$TAB"}" > "$_d/link"
      link_name "${_line#L"$TAB"}" > "$_d/name" ;;
    O"$TAB"*)
      _r=${_line#O"$TAB"}
      printf '%s\n' "${_r%%"$TAB"*}" > "$_d/name"
      printf '%s\n' "${_r#*"$TAB"}" > "$_d/ob.json" ;;
    N"$TAB"*)
      _r=${_line#N"$TAB"}
      printf '%s\n' "${_r%%"$TAB"*}" > "$_d/link"
      link_name "${_r%%"$TAB"*}" > "$_d/name"
      _r=${_r#*"$TAB"}
      case "$_r" in
        !*) printf '%s\n' "${_r#!}" > "$_d/skip" ;;
        *) printf '%s\n' "$_r" > "$_d/ob.json" ;;
      esac
      # through SSH only TCP gets to the server
      if [ -n "$VIA" ] && [ -f "$_d/ob.json" ]; then
        case "$(jq -r '.type' "$_d/ob.json" 2>/dev/null)" in
          hysteria|hysteria2|tuic|wireguard) echo "uses UDP: can't be tested through the router's SSH" > "$_d/skip"; rm -f "$_d/ob.json" ;;
        esac
      fi ;;
  esac
  _ncc=$(name_cc "$(cat "$_d/name")"); echo "$_ncc" > "$_d/ncc"
  if ! cc_allowed "$_ncc"; then           # e.g. "🇺🇸 USA" when only Europe is wanted
    rm -rf "$_d"; _i=$((_i - 1)); OUT_REGION=$((OUT_REGION + 1))
  fi
done < "$NODES"
COUNT=$_i
# (--nodes come built, by podkop on the router)
[ -f "$PODKOP_LIB/sing_box_config_facade.sh" ] && build_nodes

if [ "$BUILD_NODES" = 1 ]; then
  _i=0
  while [ "$_i" -lt "$COUNT" ]; do
    _i=$((_i + 1)); _d="$WORK/n/$_i"
    [ -f "$_d/link" ] || continue
    if [ -f "$_d/ob.json" ]; then _o=$(cat "$_d/ob.json"); else _o="!$(head -n 1 "$_d/skip" 2>/dev/null)"; fi
    printf '%s\t%s\n' "$(cat "$_d/link")" "$_o"
  done
  exit 0
fi

HAS_PARALLEL=0
curl --help all 2>/dev/null | grep -q -- '--parallel-immediate' && HAS_PARALLEL=1

# drop nodes whose server IP is outside the wanted countries, e.g. relays
# "via RU" whose entry point is in Russia
OUT_ENTRY=0
case "$COUNTRIES" in all|"") ;; *)
  if [ "$ENTRY_CHECK" = 1 ] && [ "$COUNT" -gt 0 ]; then
    entry_countries
    _i=0; _j=0
    while [ "$_i" -lt "$COUNT" ]; do
      _i=$((_i + 1))
      if cc_allowed "$(cat "$WORK/n/$_i/ecc" 2>/dev/null || echo -)"; then
        _j=$((_j + 1)); [ "$_j" -ne "$_i" ] && mv "$WORK/n/$_i" "$WORK/n/$_j"
      else
        rm -rf "$WORK/n/$_i"; OUT_ENTRY=$((OUT_ENTRY + 1))
      fi
    done
    COUNT=$_j
  fi ;;
esac

if [ "$COUNT" -eq 0 ]; then
  warn "all node(s) are outside $(countries_label)"
  exit 1
fi

# request list, shared by all nodes; "Connection: close" makes every request
# open a fresh tunnel through the node, which is what we want to measure
# req_cfg <count> [url]: curl config with <count> requests
req_cfg() {
  [ "$1" -gt 0 ] || return 0
  echo 'http1.1'
  echo 'header = "Connection: close"'
  _k=0; while [ "$_k" -lt "$1" ]; do printf 'url = "%s"\noutput = "/dev/null"\n' "${2:-$URL}"; _k=$((_k + 1)); done
}
N0=$C; [ "$N0" -gt "$N" ] && N0=$N
N1=$(( N / 2 - N0 )); [ "$N1" -lt 0 ] && N1=0
req_cfg "$N0" > "$WORK/req0.cfg"
req_cfg "$N1" > "$WORK/req1.cfg"
req_cfg $(( N - N0 - N1 )) > "$WORK/req2.cfg"
req_cfg 10 "$YT_PING_URL" > "$WORK/ytreq.cfg"

if sleep 0.1 2>/dev/null; then NAP=0.2; WAIT_TICKS=75; else NAP=1; WAIT_TICKS=15; fi

START=$(date +%s)
# (no node left for it when podkop or sing-box can't use any: they show as such)
sb_all_start || { [ -s "$WORK/ports" ] &&
  die "sing-box did not start: $(grep -v '^[[:space:]]*$' "$WORK/sb.log" 2>/dev/null | tail -n 1 | cut -c 1-300)"; }

# latency tests of one node per CPU core run side by side: every request
# opens a new encrypted tunnel, and more at once than the CPU can handshake
# makes every ping look slower (a dual-core router: +10% with 2 nodes at a
# time, +50% with 4, x3 with 10). With sing-box shared a node tested costs
# only its curl processes (~6 MB); capped by the memory left once it runs.
# Speed tests run afterwards, a few at a time.
_mem=$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null)
_max=$(( (${_mem:-160} - 24) / 6 )); [ "$_max" -lt 1 ] && _max=1
_cpus=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null); [ "${_cpus:-0}" -ge 1 ] || _cpus=1
if [ -z "$J" ]; then
  J=$_cpus; [ "$J" -gt "$_max" ] && J=$_max
elif [ "$J" -gt "$_max" ]; then
  warn "-j $J needs more free RAM, using $_max"; J=$_max
fi
[ "$J" -gt "$COUNT" ] && J=$COUNT

# the router's own line: with --via the SSH outbound, else this device
VIA_PX=""; [ -n "$VIA" ] && [ -n "$SB_PID" ] && VIA_PX="socks5h://127.0.0.1:$(cat "$WORK/via.port")"
set -- $(exit_ip "$VIA_PX")
DIRECT_IP=${1:-none}; DIRECT_CC=${2:--}
[ -n "$VIA_PX" ] && [ "$DIRECT_IP" = none ] && die "no connection through $VIA (SSH): $(grep -v '^[[:space:]]*$' "$WORK/sb.log" 2>/dev/null | tail -n 1 | cut -c 1-300)"
: > "$WORK/lat.done"

PROG=0; [ -t 1 ] && [ "$VERBOSE" != 1 ] && PROG=1
_sk=$(( OUT_REGION + OUT_ENTRY ))
echo
printf 'Testing %s%s server(s)%s%s ...\n' "$BD" "$COUNT" "$R0" \
  "$(case "$COUNTRIES" in all|"") ;; *) printf ' in %s' "$(countries_label | sed 's/ only//')"
     [ "$_sk" -gt 0 ] && printf ' (%s outside skipped)' "$_sk" ;; esac)"
if [ "$VERBOSE" = 1 ]; then
  info "source: ${SOURCE:-?}; outside the countries: $OUT_REGION by name, $OUT_ENTRY by server IP"
  info "test: $N requests per node, $C in flight, $J nodes at a time, timeout ${T}s -> $URL"
  info "router IP: $DIRECT_IP ($DIRECT_CC)${VIA:+ through $VIA}   sing-box $SB_VER"
fi

mkfifo "$WORK/sem" || die "mkfifo failed"
exec 3<>"$WORK/sem"
_k=0; while [ "$_k" -lt "$J" ]; do echo >&3; _k=$((_k + 1)); done

_i=0
while [ "$_i" -lt "$COUNT" ]; do
  _i=$((_i + 1))
  read -r _ <&3
  sb_alive || :
  ( run_node "$_i"; echo >&3 ) &
done
wait
# sing-box stopped on the way: the nodes whose test overlapped the time it
# was down are tested again
sb_alive || :
if [ -s "$WORK/sb.restarts" ] && [ -n "$SB_PID" ]; then
  _i=0
  while [ "$_i" -lt "$COUNT" ]; do
    _i=$((_i + 1))
    [ -f "$WORK/n/$_i/skip" ] && continue
    _a=$(cat "$WORK/n/$_i/t0" 2>/dev/null); _b=$(cat "$WORK/n/$_i/t1" 2>/dev/null)
    awk -v a="${_a:-0}" -v b="${_b:-0}" '$2 >= a && $1 <= b { f = 1 } END { exit !f }' "$WORK/sb.restarts" || continue
    read -r _ <&3
    ( run_node "$_i"; echo >&3 ) &
  done
  wait
  [ "$VERBOSE" = 1 ] && warn "sing-box stopped $(wc -l < "$WORK/sb.restarts") time(s) during the test and was started again"
fi
exec 3>&-

# --- speed test --------------------------------------------------------------

# nodes that lost at most 20% with ping under MAX_PING, best latency first
if [ "$SPEED" = 1 ]; then
  : > "$WORK/speedlist"
  _i=0
  while [ "$_i" -lt "$COUNT" ]; do
    _i=$((_i + 1))
    IFS="$TAB" read -r _s _ok _tot _mn _md _rest < "$WORK/n/$_i/result" || continue
    case "$_s" in ok|noexit) ;; *) continue ;; esac
    [ "$_ok" -gt 0 ] || continue
    [ $(( (_tot - _ok) * 5 )) -le "$_tot" ] || continue
    awk -v m="$_md" -v x="$MAX_PING" 'BEGIN { exit !(m * 1000 < x) }' || continue   # too slow to pick
    printf '%s\t%s\t%s\n' "$_md" "$_i" "$(same_key "$_i")" >> "$WORK/speedlist"
  done
  sort -t "$TAB" -k1,1g "$WORK/speedlist" -o "$WORK/speedlist"
  # one test per server; duplicates (same link but the name) get its result afterwards
  awk -F "$TAB" '!seen[$3]++ { print $2 }' "$WORK/speedlist" > "$WORK/speed.uniq"
  _ns=$(wc -l < "$WORK/speed.uniq" | tr -d ' ')
  if [ "$_ns" -gt 0 ]; then
    [ "$VERBOSE" = 1 ] && echo
    if [ -z "$SPEED_J" ]; then
      SPEED_J=1
    fi
    # the line's own speed, measured directly: a node can't show more (see
    # speed_fetch), and one test per ~200 Mbit/s fits on it at once (a node
    # rarely gives more than ~150)
    _line=$(_px=$VIA_PX; pcurl -s -o /dev/null --max-time 5 -w '%{size_download} %{time_total}' "$LINE_URL" 2>/dev/null |
      awk '$2 > 0 && $1 > 1000000 { printf "%.0f", $1 * 8 / $2 / 1000000 }')
    if [ -n "$_line" ]; then
      LINE_MBIT=$_line
      [ -z "$SPEED_J_USER" ] && [ "$_ns" -gt 1 ] && { SPEED_J=$(( _line / 200 )); [ "$SPEED_J" -lt 1 ] && SPEED_J=1; [ "$SPEED_J" -gt 3 ] && SPEED_J=3; }
      [ "$VERBOSE" = 1 ] && info "your line (direct): ~$_line Mbit/s"
    fi
    [ "$SPEED_J" -gt "$_ns" ] && SPEED_J=$_ns
    [ "$SPEED_J" -gt "$_max" ] && SPEED_J=$_max   # a few MB of RAM per test
    [ "$VERBOSE" = 1 ] && info "speed test: $_ns server(s), $SPEED_J at a time, until the result is stable (max ${SPEED_T}s)"
    mkfifo "$WORK/ssem" && exec 5<>"$WORK/ssem"
    _k=0; while [ "$_k" -lt "$SPEED_J" ]; do echo >&5; _k=$((_k + 1)); done
    : > "$WORK/speed.done"
    while IFS= read -r _n; do
      read -r _ <&5
      sb_alive || :
      (
        speed_node "$_n"
        echo "$_n" >> "$WORK/speed.done"
        if [ "$VERBOSE" = 1 ]; then
          printf '  [%*d/%d] %7s Mbit/s  %s\n' "${#_ns}" "$(wc -l < "$WORK/speed.done")" "$_ns" \
            "$(cat "$WORK/n/$_n/speed")" "$(cat "$WORK/n/$_n/name")"
        else
          progress "speed    $(wc -l < "$WORK/speed.done")/$_ns"
        fi
        echo >&5
      ) &
    done < "$WORK/speed.uniq"
    wait
    exec 5>&-
    # copy results to the duplicates
    awk -F "$TAB" -v OFS="$TAB" '!($3 in f) { f[$3] = $2 } { print $2, f[$3] }' "$WORK/speedlist" > "$WORK/speed.map"
    while IFS="$TAB" read -r _n _src; do
      [ "$_n" = "$_src" ] && continue
      [ -f "$WORK/n/$_src/speed" ] && cp "$WORK/n/$_src/speed" "$WORK/n/$_n/speed"
      [ -f "$WORK/n/$_src/speed.why" ] && cp "$WORK/n/$_src/speed.why" "$WORK/n/$_n/speed.why"
      for _f in yt yt.ms; do [ -f "$WORK/n/$_src/$_f" ] && cp "$WORK/n/$_src/$_f" "$WORK/n/$_n/$_f"; done
    done < "$WORK/speed.map"
  fi
fi

# --- YouTube -----------------------------------------------------------------

# normally the speed test has already checked YouTube on the fast servers;
# the rest (e.g. with --no-speed) are checked here, up to 4 at a time (fewer
# with little free RAM), one check per server
: > "$WORK/ytlist"
if [ "$YOUTUBE" = 1 ]; then
  _i=0
  while [ "$_i" -lt "$COUNT" ]; do
    _i=$((_i + 1))
    [ -f "$WORK/n/$_i/yt" ] && continue
    fast_enough "$_i" && printf '%s\t%s\n' "$_i" "$(same_key "$_i")" >> "$WORK/ytlist"
  done
fi
awk -F "$TAB" '!seen[$2]++ { print $1 }' "$WORK/ytlist" > "$WORK/yt.uniq"
_ny=$(wc -l < "$WORK/yt.uniq" | tr -d ' ')
if [ "$_ny" -gt 0 ]; then
  [ "$VERBOSE" = 1 ] && { echo; info "YouTube check: $_ny server(s)"; }
  mkfifo "$WORK/ysem" && exec 6<>"$WORK/ysem"
  _yj=4; [ "$_yj" -gt "$_max" ] && _yj=$_max
  _k=0; while [ "$_k" -lt "$_yj" ]; do echo >&6; _k=$((_k + 1)); done
  : > "$WORK/yt.done"
  while IFS= read -r _n; do
    read -r _ <&6
    sb_alive || :
    (
      yt_node "$_n"
      echo "$_n" >> "$WORK/yt.done"
      if [ "$VERBOSE" = 1 ]; then
        read -r _ys _yc < "$WORK/n/$_n/yt"
        printf '  [%*d/%d] %-4s %-3s %6s  %s\n' "${#_ny}" "$(wc -l < "$WORK/yt.done")" "$_ny" \
          "$_ys" "$_yc" "$(cat "$WORK/n/$_n/yt.ms" 2>/dev/null || echo -)" "$(cat "$WORK/n/$_n/name")"
      else
        progress "youtube  $(wc -l < "$WORK/yt.done")/$_ny"
      fi
      echo >&6
    ) &
  done < "$WORK/yt.uniq"
  wait
  exec 6>&-
  awk -F "$TAB" -v OFS="$TAB" '!($2 in f) { f[$2] = $1 } { print $1, f[$2] }' "$WORK/ytlist" |
  while IFS="$TAB" read -r _n _src; do
    [ "$_n" = "$_src" ] && continue
    for _f in yt yt.ms; do [ -f "$WORK/n/$_src/$_f" ] && cp "$WORK/n/$_src/$_f" "$WORK/n/$_n/$_f"; done
  done
fi

# the tests are over: free sing-box's memory before podkop restarts
sb_kill

# --- ranking -----------------------------------------------------------------

# all rows: n status ok total min median p90 ip cc speed name
_i=0
: > "$WORK/all"
while [ "$_i" -lt "$COUNT" ]; do
  _i=$((_i + 1))
  [ -f "$WORK/n/$_i/result" ] || continue
  _sp=$(cat "$WORK/n/$_i/speed" 2>/dev/null)
  awk -F "$TAB" -v OFS="$TAB" -v n="$_i" -v sp="${_sp:--}" '{ $9 = sp; print n, $0 }' "$WORK/n/$_i/result" >> "$WORK/all"
done
ELAPSED=$(( $(date +%s) - START ))

# classes: 0 = lost <=5% (and speed test passed), 1 = lost <=20% or speed test
# failed, 2 = worse or too slow to pick, 3 = wrong country / not via node,
# 4 = skipped / no start.
# Inside classes 0-1 the score is  median / best median  +  best speed / speed
# (1 + 1 = 2 for a node that is best at both; lower is better), so 20% more
# latency weighs the same as 20% less speed.
awk -F "$TAB" -v OFS="$TAB" -v speed="$SPEED" -v maxp="$MAX_PING" -v mins="$MIN_SPEED" '
{ row[NR] = $0
  st = $2; ok = $3 + 0; tot = $4 + 0
  fail = tot > 0 ? (tot - ok) / tot : 1
  M[NR] = ($6 == "-") ? 0 : $6 + 0; S[NR] = ($10 == "-") ? 0 : $10 + 0
  if ((st == "ok" || st == "noexit") && ok > 0) {
    cls = (fail <= 0.05) ? 0 : (fail <= 0.2 ? 1 : 2)
    if (cls == 0 && speed == 1 && S[NR] <= 0) cls = 1
    if (M[NR] * 1000 >= maxp || (speed == 1 && S[NR] <= mins)) cls = 2
  }
  else if (st == "direct" || st == "region") cls = 3
  else if (st == "ok" || st == "noexit") cls = 2
  else cls = 4
  C[NR] = cls; F[NR] = fail
  if (cls <= 1 && M[NR] > 0 && (bm == 0 || M[NR] < bm)) bm = M[NR]
  if (cls <= 1 && S[NR] > bs) bs = S[NR]
}
END {
  for (i = 1; i <= NR; i++) {
    sc = 99
    if (C[i] <= 1 && M[i] > 0) sc = M[i] / bm + ((speed == 1 && bs > 0) ? ((S[i] > 0) ? bs / S[i] : 10) : 0)
    printf "%d\t%.4f\t%.6f\t%s\n", C[i], sc, F[i], row[i]
  }
}' "$WORK/all" | sort -t "$TAB" -k1,1n -k2,2g -k3,3g | cut -f 4- > "$WORK/sorted"

# pick the best TOP + FALLBACK nodes that came from links (config outbounds
# have no link), usable classes only. The first TOP go into the URLTest group,
# the rest become its fallback links.
: > "$WORK/best"; : > "$WORK/best.names"
while IFS="$TAB" read -r _n _s _ok _tot _mn _md _p9 _xi _xc _sp _nm; do
  [ "$(wc -l < "$WORK/best")" -ge $((TOP + FALLBACK)) ] && break
  [ -f "$WORK/n/$_n/link" ] || continue
  case "$(verdict "$_s" "$_ok" "$_tot")" in GOOD|OK|FLAKY) ;; *) continue ;; esac
  fast_enough "$_n" || continue
  cat "$WORK/n/$_n/link" >> "$WORK/best"
  _sp=$(cat "$WORK/n/$_n/speed" 2>/dev/null); [ -n "$_sp" ] || _sp=-   # measured after the result line
  printf '#%-3s median %6ss %7s Mbit/s  %-3s %s\n' "$_n" "$_md" "$_sp" "$_xc" "$_nm" >> "$WORK/best.names"
done < "$WORK/sorted"

# YouTube: nodes where YouTube plays and places us in a YT_NOADS country (no
# ads; any country with --youtube-noads none), ranked by
# YouTube ping / best  +  best speed / speed
# -> ytsorted: n ad-free yt-ms speed
: > "$WORK/ytcand"
while IFS="$TAB" read -r _n _s _ok _tot _mn _md _p9 _xi _xc _sp _nm; do
  [ -f "$WORK/n/$_n/yt.ms" ] || continue
  yt_bad "$_n" && continue
  _af=0; case " $YT_NOADS " in *" $(yt_col "$_n") "*) _af=1 ;; esac
  printf '%s\t%s\t%s\t%s\n' "$_n" "$_af" "$(cat "$WORK/n/$_n/yt.ms")" "$(cat "$WORK/n/$_n/speed" 2>/dev/null || echo 0)" >> "$WORK/ytcand"
done < "$WORK/sorted"
awk -F "$TAB" -v OFS="$TAB" -v any="$( [ -z "$YT_NOADS" ] && echo 1)" '
any || $2 == 1 { r[++k] = $0; M[k] = $3 + 0; S[k] = $4 + 0
  if (M[k] > 0 && (bm == 0 || M[k] < bm)) bm = M[k]; if (S[k] > bs) bs = S[k] }
END {
  for (i = 1; i <= k; i++)
    printf "%.4f\t%s\n", (bm > 0 ? M[i] / bm : 1) + (bs > 0 ? (S[i] > 0 ? bs / S[i] : 10) : 0), r[i]
}' "$WORK/ytcand" | sort -t "$TAB" -k1,1g | cut -f 2- > "$WORK/ytsorted"

: > "$WORK/ytbest"; : > "$WORK/ytbest.names"
while IFS="$TAB" read -r _n _af _ym _sp; do
  [ "$(wc -l < "$WORK/ytbest")" -ge $((TOP + FALLBACK)) ] && break
  [ -f "$WORK/n/$_n/link" ] || continue
  cat "$WORK/n/$_n/link" >> "$WORK/ytbest"; echo "#$_n " >> "$WORK/ytbest.names"
done < "$WORK/ytsorted"

# split_fb <list>: <list> keeps the first TOP links, <list>.fb gets the rest
# (the same for .names)
split_fb() {
  for _x in "" .names; do
    tail -n +$((TOP + 1)) "$1$_x" > "$1.fb$_x"
    head -n "$TOP" "$1$_x" > "$1.top" && mv "$1.top" "$1$_x"
  done
}
split_fb "$WORK/best"; split_fb "$WORK/ytbest"

# --- report ------------------------------------------------------------------

[ "$PROG" = 1 ] && printf '\r%40s\r' ""
_good=0; _okn=0; _bad=0; _skp=0; _slow=0
while IFS="$TAB" read -r _n _s _ok _tot _rest; do
  case "$(verdict "$_s" "$_ok" "$_tot")" in
    GOOD|OK|FLAKY) fast_enough "$_n" || _slow=$((_slow + 1))
                   case "$(verdict "$_s" "$_ok" "$_tot")" in GOOD) _good=$((_good + 1)) ;; *) _okn=$((_okn + 1)) ;; esac ;;
    SKIPPED) _skp=$((_skp + 1)) ;; *) _bad=$((_bad + 1)) ;; esac
done < "$WORK/sorted"
printf 'Done in %dm%02ds: %s%d good%s, %s%d unstable%s, %s%d bad%s%s%s\n' $((ELAPSED / 60)) $((ELAPSED % 60)) \
  "$GR" "$_good" "$R0" "$YL" "$_okn" "$R0" "$RD" "$_bad" "$R0" \
  "$( [ "$_skp" -gt 0 ] && echo ", $_skp podkop can't use (-v: why)")" \
  "$( [ "$_slow" -gt 0 ] && echo "; $_slow too slow (ping >= $MAX_PING ms$( [ "$SPEED" = 1 ] && echo " or <= $MIN_SPEED Mbit/s"))")"

# lost%, latency in ms
row_fmt() { awk -v o="$1" -v t="$2" -v m="$3" 'BEGIN { printf "%s %s", (t > 0 ? sprintf("%d%%", (t - o) * 100 / t + 0.5) : "-"), (m == "-" ? "-" : sprintf("%d", m * 1000 + 0.5)) }'; }
ms() { awk -v m="$1" 'BEGIN { if (m == "" || m == "-") print "-"; else printf "%d", m * 1000 + 0.5 }'; }

echo
if [ "$VERBOSE" = 1 ]; then
  YTH=""; [ "$YOUTUBE" = 1 ] && YTH="yt  yt ms "
  printf '%s  %-3s %-12s %8s %6s %7s %7s %7s %7s  %-15s %-3s %s%s%s\n' "$BD" "#" "verdict" "ok" "fail%" "min" "median" "p90" "Mbit/s" "exit IP" "cc" "$YTH" "node" "$R0"
  while IFS="$TAB" read -r _n _s _ok _tot _mn _md _p9 _xi _xc _sp _nm; do
    _v=$(verdict "$_s" "$_ok" "$_tot")
    _fp="-"; [ "$_tot" -gt 0 ] && _fp=$(awk -v o="$_ok" -v t="$_tot" 'BEGIN {printf "%.1f", (t - o) * 100 / t}')
    _mk=" "; grep -q "^#$_n " "$WORK/best.names" && _mk="*"; grep -q "^#$_n " "$WORK/best.fb.names" && _mk="f"
    if grep -q "^#$_n " "$WORK/ytbest.names"; then _mk="${_mk}y"
    elif grep -q "^#$_n " "$WORK/ytbest.fb.names"; then _mk="${_mk}f"; fi
    _yc=""; [ "$YOUTUBE" = 1 ] && _yc="$(yt_fmt "$_n")$(printf '%5s ' "$(ms "$(cat "$WORK/n/$_n/yt.ms" 2>/dev/null)")")"
    printf '%-2s%-3s %s%-12s%s %8s %6s %7s %7s %7s %7s  %-15s %-3s %s%s\n' \
      "$_mk" "$_n" "$(vcolor "$_v")" "$_v" "$R0" "$_ok/$_tot" "$_fp" "$_mn" "$_md" "$_p9" "$_sp" "$_xi" "$_xc" "$_yc" "$_nm"
  done < "$WORK/sorted"
  printf '  %s* = main section, y = YouTube section, f = their fallback (1st column main, 2nd YouTube)%s\n' "$DM" "$R0"
else
  # the picks (*) and a few runners-up
  printf '%sMain%s %s(section %s: everything else)%s\n' "$BD" "$R0" "$DM" "$SECTION" "$R0"
  printf '%s  %-3s %7s %7s %5s  %-3s %s%s\n' "$BD" "#" "ping ms" "Mbit/s" "lost" "cc" "server" "$R0"
  _extra=0; _more=0
  while IFS="$TAB" read -r _n _s _ok _tot _mn _md _p9 _xi _xc _sp _nm; do
    _v=$(verdict "$_s" "$_ok" "$_tot")
    case "$_v" in GOOD|OK|FLAKY) ;; *) continue ;; esac
    _mk=" "
    if grep -q "^#$_n " "$WORK/best.names"; then _mk="*"
    elif grep -q "^#$_n " "$WORK/best.fb.names"; then _mk="f"
    elif [ "$_extra" -lt 5 ] && fast_enough "$_n"; then _extra=$((_extra + 1))
    else _more=$((_more + 1)); continue; fi
    set -- $(row_fmt "$_ok" "$_tot" "$_md")
    printf '%s %-3s %7s %7s %s%5s%s  %-3s %s\n' "$_mk" "$_n" "$2" "$_sp" "$(vcolor "$_v")" "$1" "$R0" "$_xc" "$_nm"
  done < "$WORK/sorted"
  [ "$_more" -gt 0 ] && printf '  %s+%d more (-v shows all servers and why the others failed)%s\n' "$DM" "$_more" "$R0"
  [ -s "$WORK/best.fb" ] && printf '  %sf = fallback: used only when all * servers fail%s\n' "$DM" "$R0"
fi

# YouTube part
_ytn=$(ls "$WORK"/n/*/yt 2>/dev/null | wc -l | tr -d ' ')
if [ "$YOUTUBE" = 1 ] && [ "$_ytn" -gt 0 ]; then
  echo
  _ytno=0; for _f in "$WORK"/n/*/yt; do case "$(cut -d ' ' -f 1 "$_f")" in ok) ;; *) _ytno=$((_ytno + 1)) ;; esac; done
  if [ ! -s "$WORK/ytsorted" ]; then
    warn "no fast server where YouTube sees ${YT_NOADS:-any country}$( [ "$_ytno" -gt 0 ] && echo " (on $_ytno of $_ytn YouTube does not play)"); the '$YT_SECTION' section will not be changed"
  else
    if [ "$VERBOSE" != 1 ]; then
      printf '%sYouTube%s %s(section %s: YouTube only)%s\n' "$BD" "$R0" "$DM" "$YT_SECTION" "$R0"
      printf '%s  %-3s %7s %7s  %-3s %s%s\n' "$BD" "#" "yt ms" "Mbit/s" "yt" "server" "$R0"
      _extra=0; _more=0
      while IFS="$TAB" read -r _n _af _ym _sp; do
        _mk=" "
        if grep -q "^#$_n " "$WORK/ytbest.names"; then _mk="y"
        elif grep -q "^#$_n " "$WORK/ytbest.fb.names"; then _mk="f"
        elif [ "$_extra" -lt 3 ]; then _extra=$((_extra + 1))
        else _more=$((_more + 1)); continue; fi
        printf '%s %-3s %7s %7s  %s%s\n' "$_mk" "$_n" "$(ms "$_ym")" "$(cat "$WORK/n/$_n/speed" 2>/dev/null || echo -)" \
          "$(yt_fmt "$_n")" "$(cat "$WORK/n/$_n/name")"
      done < "$WORK/ytsorted"
      [ "$_more" -gt 0 ] && printf '  %s+%d more%s\n' "$DM" "$_more" "$R0"
      printf '  %syt = the country YouTube sees; yt ms = ping to YouTube%s%s\n' "$DM" \
        "$( [ -s "$WORK/ytbest.fb" ] && echo "; f = fallback, used only when all y servers fail")" "$R0"
    fi
    printf 'YouTube: %s%s fast server(s) where YouTube sees %s%s%s\n' "$GR" "$(wc -l < "$WORK/ytsorted" | tr -d ' ')" \
      "${YT_NOADS:-any country}" "$R0" "$( [ "$_ytno" -gt 0 ] && echo "; on $_ytno YouTube does not play")"
  fi
fi

# speed tests that all failed deserve one line even without -v
if [ "$SPEED" = 1 ] && [ -s "$WORK/speedlist" ] && ! grep -qv '^0\.0$' "$WORK"/n/*/speed 2>/dev/null; then
  warn "all speed tests failed: $(cat "$WORK"/n/*/speed.why 2>/dev/null | sort | uniq -c | sort -rn | head -n 1 | sed 's/^ *[0-9]* //')"
fi

if [ "$VERBOSE" = 1 ]; then
  echo
  printf '%sProblems:%s\n' "$BD" "$R0"
  while IFS="$TAB" read -r _n _s _ok _tot _mn _md _p9 _xi _xc _sp _nm; do
    _why=""
    case "$_s" in
      skip)   _why=$(head -n 1 "$WORK/n/$_n/skip") ;;
      start)  _why=$(head -n 1 "$WORK/n/$_n/nostart" 2>/dev/null || echo "sing-box did not start") ;;
      direct) _why="exit IP equals the router's own IP: traffic did not go through the node" ;;
      region) _why="exit country $_xc is outside $(countries_label)" ;;
      noexit) _why="could not learn the exit IP through the node" ;;
    esac
    case "$(cut -d ' ' -f 1 "$WORK/n/$_n/yt" 2>/dev/null)" in
      bot)  _why="${_why:+$_why; }YouTube: \"sign in to confirm you're not a bot\" (IP flagged by YouTube)" ;;
      no)   _why="${_why:+$_why; }YouTube: video unavailable or video servers blocked" ;;
      fail) _why="${_why:+$_why; }YouTube did not load through this server" ;;
    esac
    case "$_s" in ok|noexit)
      [ "$_ok" -eq 0 ] && _why="all $_tot requests failed: unreachable, blocked, or wrong credentials" ;;
    esac
    case "$(verdict "$_s" "$_ok" "$_tot")" in GOOD|OK|FLAKY)
      fast_enough "$_n" || _why="${_why:+$_why; }too slow to pick (ping ${_md}s, $_sp Mbit/s; needed: < $MAX_PING ms, > $MIN_SPEED Mbit/s)" ;;
    esac
    [ "$(cat "$WORK/n/$_n/speed" 2>/dev/null)" = "0.0" ] && _why="${_why:+$_why; }speed test failed: $(cat "$WORK/n/$_n/speed.why" 2>/dev/null || echo "no data")"
    [ -n "$_why" ] && printf '  #%-3s %s: %s\n' "$_n" "$_nm" "$_why"
  done < "$WORK/sorted"
  echo
  printf '%sPer-request results (http_code seconds, 000 = failed):%s\n' "$BD" "$R0"
  while IFS="$TAB" read -r _n _s _ok _tot _rest; do
    printf -- '--- #%s %s\n' "$_n" "$(cat "$WORK/n/$_n/name")"
    awk '{ c[$1]++ } END { for (k in c) printf "  http %s x%d\n", (k == "000" ? "000 (failed)" : k), c[k] }' "$WORK/n/$_n/times"
    [ -s "$WORK/n/$_n/sb.log" ] && { echo "sing-box log:"; tail -n 5 "$WORK/n/$_n/sb.log"; }
  done < "$WORK/sorted"
fi

if [ -n "$CSV" ]; then
  { echo "n,verdict,ok,total,min,median,p90,mbit_s,exit_ip,country,youtube,youtube_ms,node"
    while IFS="$TAB" read -r _n _s _ok _tot _mn _md _p9 _xi _xc _sp _nm; do
      printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"%s"\n' "$_n" "$(verdict "$_s" "$_ok" "$_tot")" \
        "$_ok" "$_tot" "$_mn" "$_md" "$_p9" "$_sp" "$_xi" "$_xc" "$(sed 's/ /:/' "$WORK/n/$_n/yt" 2>/dev/null)" \
        "$(cat "$WORK/n/$_n/yt.ms" 2>/dev/null)" \
        "$(printf '%s' "$_nm" | sed 's/"/""/g')"
    done < "$WORK/sorted"
  } > "$CSV" && info "saved $CSV"
  # the link behind each row ("n<TAB>link"), for podkop-manager's rules by country
  while IFS="$TAB" read -r _n _rest; do
    [ -f "$WORK/n/$_n/link" ] && printf '%s\t%s\n' "$_n" "$(cat "$WORK/n/$_n/link")"
  done < "$WORK/sorted" > "$CSV.links"
fi

# --- put the best nodes into podkop ------------------------------------------

HAVE_PODKOP=0
[ -f /etc/config/podkop ] && command -v uci >/dev/null 2>&1 && HAVE_PODKOP=1
cp "$WORK/best" "$SAVED_BEST" 2>/dev/null
cp "$WORK/ytbest" "$SAVED_YT" 2>/dev/null
cp "$WORK/best.fb" "$SAVED_FB" 2>/dev/null
cp "$WORK/ytbest.fb" "$SAVED_YTFB" 2>/dev/null

DO_MAIN=0; DO_YT=0
[ -s "$WORK/best" ] && DO_MAIN=1
[ "$YOUTUBE" = 1 ] && [ -s "$WORK/ytbest" ] && DO_YT=1
if [ "$APPLY" != no ] && [ "$DO_MAIN" = 1 ] && case "$SOURCE" in podkop*) false ;; *) true ;; esac; then
  if [ "$HAVE_PODKOP" = 0 ]; then
    info "podkop not found; the lists are in $SAVED_BEST and $SAVED_YT"
  else
    if [ "$APPLY" != yes ] && [ -n "$TTY" ]; then
      _nb=$(wc -l < "$WORK/best" | tr -d ' ')
      echo
      if [ "$DO_YT" = 1 ]; then
        printf '%sPut into podkop and restart it?%s\n' "$BD" "$R0"
        printf '  Enter) both:  %s <- %s server(s) (*),  %s <- %s server(s) (y), each with its fallbacks (f)\n' "$SECTION" "$_nb" "$YT_SECTION" "$(wc -l < "$WORK/ytbest" | tr -d ' ')"
        printf '  1) only %s   2) only %s   3) other sections   n) nothing\n' "$SECTION" "$YT_SECTION"
        while :; do
          printf '> '; read -r _sel < "$TTY" || _sel=n
          case "$_sel" in
            ""|y|Y|yes|Yes|д|Д|да|Да) break ;;
            1) DO_YT=0; break ;;
            2) DO_MAIN=0; break ;;
            3) ask_sections; break ;;
            n|N|no|No|н|Н|нет|Нет|q) DO_MAIN=0; DO_YT=0; break ;;
            *) echo "Enter (or y) = both, 1, 2, 3, or n" ;;
          esac
        done
      else
        printf '%sPut the %s marked (*) server(s) into podkop (%s) and restart it?%s\n' "$BD" "$_nb" "$SECTION" "$R0"
        printf '  Enter) yes   3) other section   n) nothing\n'
        while :; do
          printf '> '; read -r _sel < "$TTY" || _sel=n
          case "$_sel" in
            ""|y|Y|yes|Yes|д|Д|да|Да) break ;;
            3) ask_sections; break ;;
            n|N|no|No|н|Н|нет|Нет|q) DO_MAIN=0; break ;;
            *) echo "Enter (or y) = yes, 3, or n" ;;
          esac
        done
      fi
    else
      [ "$APPLY_TO" = youtube ] && DO_MAIN=0
      [ "$APPLY_TO" = main ] && DO_YT=0
    fi
    if [ "$DO_MAIN" = 1 ] && [ "$SECTION" = main ] && ! uci -q get "podkop.$SECTION" >/dev/null; then
      die "podkop has no section '$SECTION' (use --section)"
    fi
    if [ "$DO_MAIN$DO_YT" != 00 ]; then apply_podkop; else info "podkop not changed (lists: $SAVED_BEST, $SAVED_YT; podkop-probe --apply-saved puts them in)"; fi
  fi
elif [ "$APPLY" != no ] && [ "$DO_MAIN" = 0 ] && case "$SOURCE" in podkop*) false ;; *) true ;; esac; then
  _why="no working servers found"
  [ "$_slow" -gt 0 ] && _why="no server is fast enough (ping < $MAX_PING ms, > $MIN_SPEED Mbit/s; see --max-ping, --min-speed)"
  warn "$_why, podkop not changed"
  [ "$CRON" = 1 ] && logger -t podkop-probe "$_why, podkop not changed" 2>/dev/null
fi

# offer the nightly run (install + saved keys) once, or when the keys changed
if [ -n "$SUBS" ] && [ "$CRON" = 0 ] && [ -n "$TTY" ] && [ "$ASSUME_YES" = 0 ] && [ "$HAVE_PODKOP" = 1 ]; then
  _saved_c=$( [ -f "$CONF" ] && . "$CONF" && printf '%s' "$COUNTRIES" )
  if [ ! -x "$BIN" ] || [ "$SUBS" != "$SAVED_SUBS" ] || [ "$COUNTRIES" != "$_saved_c" ]; then
    echo
    if ask_yn "Repeat this every night? (installs podkop-probe and saves these keys)"; then
      nightly_install
    fi
  fi
fi
exit 0
# end of podkop-probe
