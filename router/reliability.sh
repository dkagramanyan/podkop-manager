#!/bin/sh
# shellcheck shell=dash disable=SC1091,SC2016,SC2034,SC2317,SC2329
# Sourced by podkop-agent. Diagnostics never change UCI or production listeners.

# OpenWrt's standard jq is built without Oniguruma: test/match are unavailable.
# Keep the same ASCII allowlist without regexes; tags also become filenames.
RELIABILITY_TAG_JQ='def valid_tag:
  if type != "string" then false else
    length > 0 and (explode | all(. == 45 or . == 95 or
      (. >= 48 and . <= 57) or (. >= 65 and . <= 90) or (. >= 97 and . <= 122)))
  end;'

# BusyBox builds may omit timeout. Bound the lookup ourselves using only
# shell job control and sleep; kill and reap it on expiry or cancellation.
# This is specific to nslookup, which runs as a single child process.
# the waits below are counted in ticks of one second; tests set a shorter
# tick (PODKOP_DIAG_TICK=0.1) and run the same code in a tenth of the time
DIAG_TICK=${PODKOP_DIAG_TICK:-1}
TAB=${TAB:-$(printf '\t')}

diagnostic_lookup() (
  _dl_limit=$1; shift
  _dl_pid=''; _dl_elapsed=0
  trap '[ -z "$_dl_pid" ] || { kill -KILL "$_dl_pid" 2>/dev/null; wait "$_dl_pid" 2>/dev/null; }' EXIT
  trap 'exit 1' HUP INT TERM
  nslookup "$@" & _dl_pid=$!
  while kill -0 "$_dl_pid" 2>/dev/null; do
    [ "$_dl_elapsed" -lt "$_dl_limit" ] || exit 124
    sleep "$DIAG_TICK"
    _dl_elapsed=$((_dl_elapsed + 1))
  done
  wait "$_dl_pid"; _dl_rc=$?
  _dl_pid=''
  exit "$_dl_rc"
)

diagnostic_primary_dns() (
  _dp=$(jq -c '.dns.servers[]? | select(.tag == "dns-server")' "$1")
  _dt=$(printf '%s' "$_dp" | jq -r '.type // "unknown"')
  _ds=$(printf '%s' "$_dp" | jq -r '.server // empty')
  _ok=null; _method="unsupported DNS transport or route"
  # A proxy-routed resolver must not accidentally be tested by a direct request.
  if printf '%s' "$_dp" | jq -e '(.detour // "direct-out") == "direct-out" and (.bind_interface // "") == ""' >/dev/null; then
    case "$_dt" in
      udp)
        _ok=false; _method="direct UDP lookup"
        diagnostic_lookup 6 example.com "$_ds" >/dev/null 2>&1 && _ok=true ;;
      https)
        _name=$(printf '%s' "$_dp" | jq -r '.tls.server_name // .server')
        _port=$(printf '%s' "$_dp" | jq -r '.server_port // 443')
        _path=$(printf '%s' "$_dp" | jq -r '.path // "/dns-query"')
        _ok=false; _method="direct DoH query"
        # RFC 8484 wire-format A query for example.com. Verify a DNS response,
        # not just an HTTP 200 from a gateway or a block page.
        case "$_name" in *:*) _name="[$_name]" ;; esac
        _code=$(curl --noproxy '*' -s --max-time 6 --max-filesize 4096 \
          --connect-to "$_name:$_port:$_ds:$_port" -H 'Accept: application/dns-message' \
          -o "$2/primary.dns" -w '%{http_code}' \
          "https://$_name:$_port$_path?dns=AAABAAABAAAAAAAAB2V4YW1wbGUDY29tAAABAAE" 2>/dev/null)
        if [ "$_code" = 200 ] && [ "$(wc -c < "$2/primary.dns" 2>/dev/null)" -ge 12 ]; then
          _qr=$(od -An -tu1 -j2 -N1 "$2/primary.dns" | tr -d ' ')
          _rcode=$(od -An -tu1 -j3 -N1 "$2/primary.dns" | tr -d ' ')
          [ "$((_qr & 128))" = 128 ] && [ "$((_rcode & 15))" = 0 ] && _ok=true
        fi ;;
    esac
  fi
  jq -nc --argjson ok "$_ok" --arg method "$_method" --arg type "$_dt" '{ok:$ok,method:$method,type:$type}'
)

reliability_filter() {
  _rf='[]'
  if [ -s "$RUN/eligibility.json" ] && [ "$(jq -r .expires "$RUN/eligibility.json")" -gt "$(date +%s)" ] &&
     [ "$(jq -r .fingerprint "$RUN/eligibility.json")" = "$(config_fp)" ]; then
    _rf=$(jq -c .banned "$RUN/eligibility.json")
  fi
  jq --argjson banned "$_rf" '
    (.outbounds[] | select(.type == "urltest")) |= (
      .outbounds as $original | (.fallbacks // []) as $fallback
      | .outbounds |= map(select(. as $t | $banned | index($t) | not))
      | .fallbacks = ($fallback | map(select(. as $t | $banned | index($t) | not)))
      | if (.outbounds | length) == 0 then
          if (.fallbacks | length) > 0 then .outbounds = [.fallbacks[0]] | .fallbacks = .fallbacks[1:]
          else .outbounds = $original | .fallbacks = $fallback end
        else . end
      | if (.fallbacks | length) == 0 then del(.fallbacks) else . end)' "$1" > "$2"
}

# A short lease returns selection to the engine when the manager is offline.
# It is renewed without a reload unless the banned set actually changes.
cmd_eligibility() (
  exec 9> "$RUN/apply.lock"; flock -n 9 || fail busy "an apply is in progress"
  head -c 65536 > "$RUN/policy.$$.in"
  trap 'rm -f "$RUN/policy.$$.in" "$RUN/policy.$$.old" "$RUN/policy.$$.config"' EXIT
  jq -e "$RELIABILITY_TAG_JQ"' (.fingerprint | type == "string") and (.banned | type == "array" and length <= 100)
    and all(.banned[]; valid_tag)' "$RUN/policy.$$.in" >/dev/null || fail bad_request "invalid eligibility policy"
  _fp=$(config_fp) || fail busy "configuration unavailable"
  [ "$(jq -r .fingerprint "$RUN/policy.$$.in")" = "$_fp" ] || fail conflict "configuration changed during checks"
  _ban=$(jq -c '.banned | unique | sort' "$RUN/policy.$$.in")
  # Never override an explicit user pin, even if it was set after the check.
  if [ -s "$ETC/pins" ] && [ "$_ban" != '[]' ]; then
    _pins=$(cut -f 1 "$ETC/pins" | jq -Rsc 'split("\n") | map(select(length > 0))')
    _ban=$(printf '%s' "$_ban" | jq -c --argjson p "$_pins" 'map(select(. as $t | any($p[]; . as $s | $t | startswith($s + "-")) | not))')
  fi
  _old=$(jq -c '.banned | sort' "$RUN/eligibility.json" 2>/dev/null || echo '[]')
  _oldfp=$(jq -r .fingerprint "$RUN/eligibility.json" 2>/dev/null || true)
  [ ! -f "$RUN/eligibility.json" ] || cp "$RUN/eligibility.json" "$RUN/policy.$$.old"
  _changed=true
  if [ "$_old" = "$_ban" ] && { [ "$_oldfp" = "$_fp" ] || [ "$_ban" = '[]' ]; }; then _changed=false; fi
  _cp=$(uci -q get podkop.settings.config_path)
  [ -n "$_cp" ] || _cp=/etc/sing-box/config.json
  if [ "$_changed" = true ]; then
    cp "$_cp" "$RUN/policy.$$.config" || fail error "cannot back up running configuration"
  fi
  if ! { jq -nc --arg fp "$_fp" --argjson b "$_ban" --argjson e "$(($(date +%s) + 180))" \
    '{fingerprint: $fp, banned: $b, expires: $e}' > "$RUN/eligibility.new" && mv "$RUN/eligibility.new" "$RUN/eligibility.json"; }; then
    fail error "cannot save eligibility policy"
  fi
  [ "$_changed" = true ] || reply "$(jq -nc --argjson b "$_ban" '{changed:false,banned:$b}')"
  if ! _why=$(hot_apply "temporary server eligibility"); then
    if [ -f "$RUN/policy.$$.old" ]; then mv "$RUN/policy.$$.old" "$RUN/eligibility.json"; else rm -f "$RUN/eligibility.json"; fi
    cp "$RUN/policy.$$.config" "$_cp.agent-new" && mv "$_cp.agent-new" "$_cp"
    _pid=$(singbox_pid); [ -z "$_pid" ] || kill -HUP "$_pid"
    fail recovery_failed "eligibility change rolled back: $_why"
  fi
  event eligibility "$(jq -nc --argjson b "$_ban" '{banned:$b}')"
  reply "$(jq -nc --argjson b "$_ban" '{changed:true,banned:$b}')"
)

reliability_expire() (
  [ -s "$RUN/eligibility.json" ] || exit 0
  [ "$(jq -r .expires "$RUN/eligibility.json")" -le "$(date +%s)" ] || exit 0
  exec 9> "$RUN/apply.lock"; flock -n 9 || exit 0
  _attempt=$(cat "$RUN/eligibility.retry" 2>/dev/null || echo 0)
  [ "$(date +%s)" -ge "$_attempt" ] || exit 0
  _expired=$(jq -c .banned "$RUN/eligibility.json")
  if [ "$_expired" = '[]' ]; then rm -f "$RUN/eligibility.json"; exit 0; fi
  # hot_apply rebuilds from UCI, so it restores original memberships. Leave
  # the expired policy in place on failure and retry next daemon round.
  _cp=$(uci -q get podkop.settings.config_path)
  [ -n "$_cp" ] || _cp=/etc/sing-box/config.json
  cp "$_cp" "$RUN/eligibility.restore" || exit 0
  echo "$(($(date +%s) + 60))" > "$RUN/eligibility.retry"
  if hot_apply "eligibility lease expired" >/dev/null; then
    rm -f "$RUN/eligibility.json" "$RUN/eligibility.retry"; event eligibility_expired '{}'
  else
    if cp "$RUN/eligibility.restore" "$_cp.agent-new" && mv "$_cp.agent-new" "$_cp"; then
      _pid=$(singbox_pid); [ -z "$_pid" ] || kill -HUP "$_pid"
    fi
  fi
  rm -f "$RUN/eligibility.restore"
)

cmd_diagnostics() (
  exec 7> "$RUN/diagnostics.lock"; flock -n 7 || fail busy "a connection check is already running"
  flock -n "$RUN/apply.lock" true || fail busy "configuration is being applied"
  [ "$(cat "$RUN/probe/state" 2>/dev/null)" != running ] || fail busy "the full probe is running"
  _dir=$(mktemp -d "$RUN/check.XXXXXX") || fail error "cannot create check directory"
  _pid=''; _jobs=''
  trap '[ -z "$_jobs" ] || kill $_jobs 2>/dev/null; [ -z "$_pid" ] || kill "$_pid" 2>/dev/null; rm -rf "$_dir"' EXIT
  trap 'exit 1' HUP INT TERM
  head -c 65536 > "$_dir/request"
  jq -e "$RELIABILITY_TAG_JQ"' (.tags | type == "array" and length <= 4) and all(.tags[]; valid_tag)' "$_dir/request" >/dev/null ||
    fail bad_request "up to four server tags expected"
  _mem=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
  [ "${_mem:-0}" -ge 49152 ] || fail resource_limit "less than 48 MiB available for isolated diagnostics"
  _cp=$(uci -q get podkop.settings.config_path)
  [ -n "$_cp" ] || _cp=/etc/sing-box/config.json
  [ -s "$_cp" ] || fail podkop_down "no running sing-box configuration"
  _fingerprint=$(config_fp) || fail busy "configuration unavailable"
  # Reserve a free local port per outbound. A race is caught by sing-box's
  # start check; it is a collector error, never a failed proxy observation.
  _port=$((20000 + $$ % 20000)); : > "$_dir/ports"
  for _tag in diagnostic-direct $(jq -r '.tags | unique[]' "$_dir/request"); do
    while awk -v p=":$(printf '%04X' "$_port")" '$2 ~ p "$" {found=1} END {exit !found}' /proc/net/tcp /proc/net/tcp6; do _port=$((_port + 1)); done
    printf '%s\t%s\n' "$_tag" "$_port" >> "$_dir/ports"; _port=$((_port + 1))
  done
  jq -Rn '[inputs | split("\t") | {tag:.[0], port:(.[1]|tonumber)}]' < "$_dir/ports" > "$_dir/inbounds"
  jq --slurpfile ports "$_dir/inbounds" '
    .outbounds as $all
    | [$ports[0][].tag | select(. != "diagnostic-direct")
      | limit(16; recurse(. as $t | ($all[] | select(.tag == $t) | .detour) // empty))] as $need
    | {log:{level:"error"}, dns:{servers:[{type:"local",tag:"local"}]},
       inbounds:[$ports[0][] | {type:"mixed",tag:("check-"+.tag),listen:"127.0.0.1",listen_port:.port}],
       outbounds:([$all[] | select(.tag as $t | $need | index($t)) | del(.domain_resolver)] +
         [(($all[] | select(.tag == "direct-out" and .type == "direct")) // {type:"direct"}) | .tag="diagnostic-direct"]),
       route:{default_domain_resolver:"local",rules:[$ports[0][] | {inbound:[("check-"+.tag)],outbound:.tag}]}}
    | if any(.outbounds[]; .type == "urltest" or .type == "selector") then error("test concrete servers only") else . end' \
    "$_cp" > "$_dir/config.json" || fail unsupported "could not build isolated connection checks"
  sing-box check -c "$_dir/config.json" >/dev/null 2>&1 || fail unsupported "engine cannot run these diagnostics"
  # 7>&-: a sing-box left behind (the check killed outright) must not keep the lock
  sing-box run -c "$_dir/config.json" > "$_dir/engine.log" 2>&1 7>&- & _pid=$!
  sleep "$DIAG_TICK"
  kill -0 "$_pid" 2>/dev/null || fail collector "diagnostic engine did not start"
  # Test DNS separately. Provider DNS comes from the WAN, never a guessed IP.
  _dns=false; diagnostic_lookup 6 example.com 127.0.0.42 > "$_dir/dns" 2>&1 && _dns=true
  _primary=$(diagnostic_primary_dns "$_cp" "$_dir")
  _wan=$(ubus call network.interface.wan status 2>/dev/null || echo '{}')
  _provider=$(printf '%s' "$_wan" | jq -r '.["dns-server"][0] // empty')
  _pdns=null
  case "$_provider" in ''|*[!0-9a-fA-F:.]*) ;; *) _pdns=false; diagnostic_lookup 6 example.com "$_provider" >/dev/null 2>&1 && _pdns=true ;; esac
  _gateway=$(ip -4 route show default | awk 'NR==1 {print $3}')
  _gw=null
  case "$_gateway" in ''|*[!0-9.]*) ;; *) _gw=false; ping -c 1 -W 2 "$_gateway" >/dev/null 2>&1 && _gw=true ;; esac
  # Same independent endpoints through each path. A tiny body is sufficient
  # for availability; periodic payload checks expose stalled downloads.
  while IFS="$TAB" read -r _tag _port; do
    (
      : > "$_dir/$_tag.rows"
      for _target in https://cp.cloudflare.com/generate_204 https://www.gstatic.com/generate_204; do
        _result=$(curl --noproxy '' -s --proxy "socks5h://127.0.0.1:$_port" --connect-timeout 4 --max-time 8 \
          --max-filesize 131072 -o /dev/null -w '%{http_code} %{time_total} %{time_connect} %{size_download}' "$_target" 2>/dev/null)
        _rc=$?
        printf '%s' "$_result" | jq -Rsc --arg t "$_target" --argjson rc "$_rc" '
          split(" ") | {target:$t,exit:$rc,code:(.[0]|tonumber? // 0),seconds:(.[1]|tonumber? // 0),
          connect_seconds:(.[2]|tonumber? // 0),bytes:(.[3]|tonumber? // 0)}' >> "$_dir/$_tag.rows"
      done
      if [ "$(jq -r '.payload // false' "$_dir/request")" = true ]; then
        : > "$_dir/$_tag.payload.rows"
        for _target in 'https://speed.cloudflare.com/__down?bytes=65536' 'https://ajax.googleapis.com/ajax/libs/jquery/3.7.1/jquery.min.js'; do
          _result=$(curl --noproxy '' -s --proxy "socks5h://127.0.0.1:$_port" --connect-timeout 4 --max-time 8 \
            --range 0-65535 --max-filesize 65536 -o /dev/null -w '%{http_code} %{time_total} %{size_download}' "$_target" 2>/dev/null)
          _rc=$?
          printf '%s' "$_result" | jq -Rsc --arg t "$_target" --argjson rc "$_rc" 'split(" ") |
            {target:$t,exit:$rc,code:(.[0]|tonumber? // 0),seconds:(.[1]|tonumber? // 0),bytes:(.[2]|tonumber? // 0)}' >> "$_dir/$_tag.payload.rows"
        done
        jq -sc '.' "$_dir/$_tag.payload.rows" > "$_dir/$_tag.payload"
      fi
    ) & _jobs="$_jobs $!"
  done < "$_dir/ports"
  for _j in $_jobs; do wait "$_j"; done; _jobs=''
  kill -0 "$_pid" 2>/dev/null || fail collector "diagnostic engine stopped during checks"
  [ "$_fingerprint" = "$(config_fp)" ] || fail conflict "configuration changed during checks"
  : > "$_dir/nodes"
  while IFS="$TAB" read -r _tag _port; do
    jq -sc --arg t "$_tag" '{key:$t,value:.}' "$_dir/$_tag.rows" >> "$_dir/nodes"
  done < "$_dir/ports"
  _payload=$(for _f in "$_dir"/*.payload; do [ ! -f "$_f" ] || jq --arg t "$(basename "$_f" .payload)" '{key:$t,value:.}' "$_f"; done | jq -sc 'from_entries')
  reply "$(jq -sc --arg fp "$_fingerprint" --argjson dns "$_dns" --argjson pdns "$_pdns" \
    --arg provider "$_provider" --argjson gateway "$_gw" --arg load "$(cut -d ' ' -f 1-3 /proc/loadavg)" \
    --argjson mem "$_mem" --argjson payload "$_payload" --argjson wan "$_wan" --argjson primary "$_primary" '
    from_entries | {fingerprint:$fp,direct:.["diagnostic-direct"],nodes:del(.["diagnostic-direct"]),payload:$payload,
      router_dns:$dns,primary_dns:$primary,provider_dns:$pdns,provider:$provider,gateway:$gateway,load:$load,available_kib:$mem,
      wan:{up:$wan.up,uptime:$wan.uptime,device:$wan.l3_device},method:"isolated engine through router"}' "$_dir/nodes")"
)

# --- dead groups: self-heal -----------------------------------------------------------
#
# Every 30 s the agent tests the server each URLTest group uses now (one
# request per group); when it fails, or the group has no working server, every
# server of the group is tested at once. A group whose own servers all fail in
# two checks in a row (30-75 s) while the WAN answers is "down": the agent
# reports group_down and, at once, borrows up to 3 working servers of other
# groups into it, in RAM only (sing-box's config, reloaded in place). The
# manager may add servers of its own (heal-links, a 2-hour lease) and runs the
# probe. When one of the group's own servers works again, or podkop's settings
# change (an apply), the borrowed servers go away.
#
# $ETC/heal-off: sections that are never healed and never lend their servers,
#   one per line ("*" = self-heal off). Written only when it changes (flash).
#   Kill-switch rules, and the rules they send through, are still watched
#   (see heal_watch_json): their kill switch works without self-heal.
# $RUN/heal/state.json: {fingerprint, groups: {<section>: {dead_since, reported,
#   added: [borrowed tags], extra: [manager tags], lease, hold}}}
# $RUN/heal/extra.json: {<section>: [outbounds built from the manager's links]}

HEAL_LEASE=7200      # the manager's links, unless it sends them again
HEAL_HOLD=600        # after a failed reload, no new try for this long
HEAL_STRIKES=2       # checks in a row with no answering server before a group is down
HEAL_RECOVER=4       # checks in a row with an answering server before it is up again
HEAL_MIN_HOLD=300    # s a group keeps its borrowed servers at least (no reload churn)
HEAL_TIMEOUT=5000    # ms for a test request through a server
HEAL_SILENT=4        # checks in a row without an answer of sing-box's API before podkop is restarted

heal_off_json() {
  if [ -s "$ETC/heal-off" ]; then jq -Rsc 'split("\n") | map(select(. != ""))' "$ETC/heal-off"; else echo '[]'; fi
}

# --- strict rules -----------------------------------------------------------------
#
# A strict rule's traffic goes through the rule's own servers or nowhere:
# - self-heal never lends it other rules' servers (the manager's servers from
#   the rule's own history, heal-links, are still taken);
# - when none of its servers answers, connections are refused at once instead
#   of hanging until a timeout: the rule's selector is switched to
#   <rule>-strict-out, a member that always refuses (this sing-box build has
#   no block outbound: a SOCKS outbound to a closed local port refuses every
#   connection immediately), and back to the URLTest group as soon as one of
#   the rule's servers answers again.
# $ETC/strict: the strict sections, one per line (written only when it changes)

strict_json() {
  if [ -s "$ETC/strict" ]; then jq -Rsc 'split("\n") | map(select(. != ""))' "$ETC/strict"; else echo '[]'; fi
}

# heal_watch_json <sing-box config>: the sections watched even where self-heal
# is off: the kill-switch rules and the rules they send through (their servers
# decide whether the kill switch closes). Watched but off = tested and tracked,
# never lends and never borrows.
heal_watch_json() {
  jq -c --argjson strict "$(strict_json)" '
    ($strict | map(select(type == "string"))) as $s
    | $s + [.outbounds[]? | select(.type == "selector" and (.tag | endswith("-out"))) | (.tag[:-4]) as $t
            | select($s | index($t)) | (.outbounds // [])[] | select(. != "\($t)-strict-out")
            | if endswith("-out") then .[:-4] else . end] | unique' "$1" 2>/dev/null || echo '[]'
}

# strict_filter <config>, in place: every strict section whose outbound is a
# selector (of its own URLTest group, or of another rule, e.g. main-out) gets
# its refusing member in that selector (a reload only, no podkop restart)
strict_filter() {
  jq --argjson strict "$(strict_json)" '
    reduce ($strict[] | select(type == "string")) as $s (.;
      "\($s)-strict-out" as $b
      | if any(.outbounds[]?; .tag == "\($s)-out" and .type == "selector") then
          (if any(.outbounds[]?; .tag == $b) then . else
             .outbounds += [{type: "socks", tag: $b, server: "127.0.0.1", server_port: 9}] end)
          | (.outbounds[] | select(.tag == "\($s)-out") | .outbounds) |= (if index($b) then . else . + [$b] end)
        else . end)' "$1" > "$1.strict" && mv "$1.strict" "$1"
}

# --- strict guard: while podkop is down -----------------------------------------
#
# With podkop's nft table gone (a crash loop stopped by the engine's failsafe,
# a full restart, 20-30 s) traffic goes direct. For strict sections the
# router blocks their destinations instead, from lists built in RAM from
# podkop's settings: user domains and subnets, local and remote lists, and
# the community lists (fetched like podkop fetches them, decompiled).
# - nft: a table of ours with their IPv4 subnets (and podkop's fake-IP range,
#   addresses devices may still hold), rejecting forwarded traffic from the
#   LAN. It is in place whenever strict sections exist: with podkop up these
#   packets go to sing-box before they could be forwarded, so it changes
#   nothing then, and there is no window when podkop goes down.
# - dnsmasq: a file in its conf-dir answering NXDOMAIN for their domains,
#   put in only while podkop is down or starting (with podkop up it would
#   shadow podkop's answers), taken out once podkop runs. Raised before
#   every podkop restart (podkop's own dnsmasq restart picks it up) and
#   within a second of sing-box going away (the daemon follows ubus events).
# A device with its own DNS (DoH in a browser) is not caught by the dnsmasq
# part; the subnet part catches it when the destination is in a list.

GUARD_TABLE=podkop_agent_guard
GUARD_DNS=podkop-agent-strict.conf
GUARD_LIST_AGE=1440     # minutes: remote and community lists are fetched again after a day
GUARD_RESOLVE_AGE=60    # minutes: the strict sections' own domains are resolved again after an hour
GUARD_RESOLVE_MAX=400   # names resolved at most
GUARD_RETRY=10          # minutes before a list that could not be fetched is tried again

guard_dnsmasq_dir() {
  _gd=$(grep -hs '^conf-dir=' /var/etc/dnsmasq.conf.* 2>/dev/null | head -n 1 | cut -d= -f2 | cut -d, -f1)
  [ -n "$_gd" ] || _gd=/tmp/dnsmasq.d
  printf '%s' "$_gd"
}
# the pieces that touch the system, so tests can stand them in
# guard_fetch <url> <file>: straight from the router; where the lists' host is
# cut off without the proxy (the router's own traffic is not routed by
# podkop), through a server that answers now: a sing-box run of just that
# outbound, like "exit-ip"
guard_fetch() {
  wget -q -T 30 -O "$2" "$1" 2>/dev/null && [ -s "$2" ] && return 0
  curl -fsSL --max-time 30 -o "$2" "$1" 2>/dev/null && [ -s "$2" ] && return 0
  _fc=$(uci -q get podkop.settings.config_path); [ -n "$_fc" ] || _fc=/etc/sing-box/config.json
  proxies > "$2.px" 2>/dev/null
  jq -c --slurpfile p "$2.px" '.outbounds as $all
    | ([$all[] | select(.type == "urltest") | ($p[0].proxies[.tag].now // "") as $n
        | select($n != "" and ((($p[0].proxies[$n].history // []) | last | (.delay // 0)) > 0)) | $n] | first) as $t
    | [$t | recurse(. as $x | ($all[] | select(.tag == $x) | .detour) // empty)] as $chain
    | {log: {disabled: true}, dns: {servers: [{type: "local", tag: "local"}]},
       outbounds: [$all[] | select(.tag as $x | $chain | index($x)) | del(.domain_resolver)],
       route: {final: $t, default_domain_resolver: "local"}}
    | if (.outbounds | length) == 0 then error("no outbound") else . end' "$_fc" > "$2.cfg" 2>/dev/null || { rm -f "$2.px" "$2.cfg"; return 1; }
  sing-box tools fetch -c "$2.cfg" "$1" > "$2" 2>/dev/null &
  _fp=$!; _fw=0
  while kill -0 "$_fp" 2>/dev/null && [ "$_fw" -lt 45 ]; do sleep 1; _fw=$((_fw + 1)); done
  if kill "$_fp" 2>/dev/null; then wait "$_fp" 2>/dev/null; _fr=1; else wait "$_fp" 2>/dev/null; _fr=$?; fi
  rm -f "$2.px" "$2.cfg"
  [ "$_fr" = 0 ] && [ -s "$2" ]
}
guard_decompile() { sing-box rule-set decompile -o "$2" "$1" >/dev/null 2>&1; }
guard_dnsmasq_restart() { /etc/init.d/dnsmasq restart >/dev/null 2>&1 8>&- 9>&-; }
guard_nft() { nft "$@" 2>/dev/null; }

# the lists of a strict section: lines "D domain", "S subnet", "C community
# list", "LD file", "LS file", "RD url", "RS url"
GUARD_LISTS_JQ='
def lst: if . == null then [] elif type == "array" then . else [.] end;
def words: (if type == "string" then [.] else lst end)
  | [.[] | tostring | split("\n")[] | split(" ")[] | split(",")[] | split("\t")[]] | map(select(. != ""));
.sections[] | select(.type == "section" and (.name as $n | $strict | index($n)))
| .options as $o
| (if ($o.user_domain_list_type // "disabled") == "dynamic" then ($o.user_domains | words | .[] | "D \(.)")
   elif ($o.user_domain_list_type // "") == "text" then ($o.user_domains_text | words | .[] | "D \(.)") else empty end),
  (if ($o.user_subnet_list_type // "disabled") == "dynamic" then ($o.user_subnets | words | .[] | "S \(.)")
   elif ($o.user_subnet_list_type // "") == "text" then ($o.user_subnets_text | words | .[] | "S \(.)") else empty end),
  ($o.community_lists | lst | .[] | "C \(.)"),
  ($o.local_domain_lists | lst | .[] | "LD \(.)"), ($o.local_subnet_lists | lst | .[] | "LS \(.)"),
  ($o.remote_domain_lists | lst | .[] | "RD \(.)"), ($o.remote_subnet_lists | lst | .[] | "RS \(.)")'

# guard_ip4: only well-formed IPv4 addresses and subnets pass (one bad
# element and nft refuses the whole table)
guard_ip4() {
  awk -F '[./]' '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(\/[0-9]+)?$/ && $1 <= 255 && $2 <= 255 && $3 <= 255 && $4 <= 255 && (NF == 4 || $5 <= 32)'
}

# The remote and community lists are fetched in the background, never by the
# build itself (an apply or a recovery holds the lock while it builds, and
# after a reboot the guard must stand before the network does): the build
# takes the copies in $RUN/guard/lists and writes what it wants to
# $RUN/guard/want ("url<TAB>file"); the job fetches what is missing or a day
# old, and the daemon builds again (lists.rev is part of the key). A failed
# fetch keeps the old copy and is tried again after GUARD_RETRY minutes.
guard_lists_job() {
  exec 6> "$RUN/guard/fetch.lock"; flock -n 6 || return 0
  mkdir -p "$RUN/guard/fetch"; _gch=""
  while IFS="$TAB" read -r _gu _gf; do
    [ -n "$_gf" ] || continue
    [ -s "$_gf" ] && [ -n "$(find "$_gf" -mmin "-$GUARD_LIST_AGE" 2>/dev/null)" ] && continue
    _gt="$RUN/guard/fetch/${_gf##*/}"
    if guard_fetch "$_gu" "$_gt" < /dev/null && [ -s "$_gt" ]; then
      case "$_gf" in *.srs)
        if guard_decompile "$_gt" "$_gt.json" && [ -s "$_gt.json" ]; then mv "$_gt.json" "${_gf%.srs}.json"
        else rm -f "$_gt" "$_gt.json"; continue; fi ;;
      esac
      mv "$_gt" "$_gf"; _gch=1
    else rm -f "$_gt"; fi
  done < "$RUN/guard/want"
  if [ -n "$_gch" ]; then date +%s > "$RUN/guard/lists.rev"; touch "$RUN/guard/poke"; fi
}
guard_lists_start() {
  [ -s "$RUN/guard/want" ] || return 0
  _gneed=""
  while IFS="$TAB" read -r _gu _gf; do
    if [ ! -s "$_gf" ] || [ -z "$(find "$_gf" -mmin "-$GUARD_LIST_AGE" 2>/dev/null)" ]; then _gneed=1; break; fi
  done < "$RUN/guard/want"
  [ -n "$_gneed" ] || return 0
  [ -n "$(find "$RUN/guard/fetch.stamp" -mmin "-$GUARD_RETRY" 2>/dev/null)" ] && return 0
  touch "$RUN/guard/fetch.stamp"
  # no lock of the caller's goes along (the apply lock above all)
  ( guard_lists_job ) </dev/null >/dev/null 2>&1 4>&- 5>&- 7>&- 8>&- 9>&- &
}

# guard_resolve_job: the addresses the strict sections' own domains resolve
# to now (outside resolvers, podkop's fake answers skipped), for devices that
# bring their own DNS; in the background, the daemon is poked when done
guard_resolve_job() {
  exec 5> "$RUN/guard/resolve.lock"; flock -n 5 || return 0
  : > "$RUN/guard/resolved.t"
  head -n "$GUARD_RESOLVE_MAX" "$RUN/guard/own-domains" 2>/dev/null | while IFS= read -r _gh; do
    _ga=$(resolve_host "$_gh"); [ -n "$_ga" ] && printf '%s\n' "$_ga" >> "$RUN/guard/resolved.t"
  done
  sort -u "$RUN/guard/resolved.t" > "$RUN/guard/resolved.n" && mv "$RUN/guard/resolved.n" "$RUN/guard/resolved"
  rm -f "$RUN/guard/resolved.t"
  touch "$RUN/guard/poke"
}
guard_resolve_start() {
  if [ -s "$RUN/guard/own-domains" ] && command -v resolve_host >/dev/null 2>&1 &&
     { [ ! -f "$RUN/guard/resolved" ] || [ -z "$(find "$RUN/guard/resolved" -mmin "-$GUARD_RESOLVE_AGE" 2>/dev/null)" ]; }; then
    touch "$RUN/guard/resolved" 2>/dev/null       # not again before the job is done
    # no lock of the caller's goes along: resolving takes minutes, and an
    # inherited apply lock would stop applies, self-heal and recovery for as long
    ( guard_resolve_job ) </dev/null >/dev/null 2>&1 4>&- 6>&- 7>&- 8>&- 9>&- &
  fi
}

# guard_build: the nft and dnsmasq files for the strict sections, built again
# when podkop's settings change (not the flag podkop writes at every start
# and stop), a list was fetched or the resolved addresses changed. One build
# at a time: the daemon, an apply and "strict" all come here.
guard_build() {
  mkdir -p "$RUN/guard/lists"
  ( flock 4; guard_build_locked ) 4> "$RUN/guard/build.lock"
}
guard_build_locked() {
  _gc=$(config_json) || return 1
  _gs=$(strict_json)
  _gk=$(printf '%s%s%s%s' "$(printf '%s' "$_gc" | jq -cS "${FP_JQ:-.}" 2>/dev/null)" "$_gs" "$(cat "$RUN/guard/resolved" 2>/dev/null)" \
    "$(cat "$RUN/guard/lists.rev" 2>/dev/null)" | md5sum | cut -c 1-32)
  guard_resolve_start
  if [ "$(cat "$RUN/guard/key" 2>/dev/null)" = "$_gk" ] && [ -s "$RUN/guard/nft.conf" ]; then guard_lists_start; return 0; fi
  _srs=$(. "$PODKOP_LIB/constants.sh" 2>/dev/null; printf '%s' "${SRS_MAIN_URL:-https://github.com/itdoginfo/allow-domains/releases/latest/download}")
  : > "$RUN/guard/domains.t"; : > "$RUN/guard/subnets.t"; : > "$RUN/guard/own.t"; : > "$RUN/guard/want.t"
  printf '%s' "$_gc" | jq -r --argjson strict "$_gs" "$GUARD_LISTS_JQ" 2>/dev/null | while IFS= read -r _gl; do
    _gv=${_gl#* }
    case "$_gl" in
      "D "*) printf '%s\n' "$_gv" >> "$RUN/guard/domains.t"; printf '%s\n' "$_gv" >> "$RUN/guard/own.t" ;;
      "S "*) printf '%s\n' "$_gv" >> "$RUN/guard/subnets.t" ;;
      "LD "*) [ -r "$_gv" ] && cat "$_gv" >> "$RUN/guard/domains.t" ;;
      "LS "*) [ -r "$_gv" ] && cat "$_gv" >> "$RUN/guard/subnets.t" ;;
      "RD "*|"RS "*)
        case "$_gv" in *"$TAB"*) continue ;; esac
        _gf="$RUN/guard/lists/$(printf '%s' "$_gv" | md5sum | cut -c 1-16).txt"
        printf '%s\t%s\n' "$_gv" "$_gf" >> "$RUN/guard/want.t"
        if [ -s "$_gf" ]; then
          case "$_gl" in "RD "*) cat "$_gf" >> "$RUN/guard/domains.t" ;; *) cat "$_gf" >> "$RUN/guard/subnets.t" ;; esac
        fi ;;
      "C "*)
        case "$_gv" in *[!A-Za-z0-9_-]*) continue ;; esac
        printf '%s\t%s\n' "$_srs/$_gv.srs" "$RUN/guard/lists/$_gv.srs" >> "$RUN/guard/want.t"
        if [ -s "$RUN/guard/lists/$_gv.json" ]; then
          jq -r '.rules[]? | (.domain // [])[], (.domain_suffix // [])[]' "$RUN/guard/lists/$_gv.json" 2>/dev/null >> "$RUN/guard/domains.t"
          jq -r '.rules[]? | (.ip_cidr // [])[]' "$RUN/guard/lists/$_gv.json" 2>/dev/null >> "$RUN/guard/subnets.t"
        fi ;;
    esac
  done
  sort -u "$RUN/guard/want.t" > "$RUN/guard/want.n" && mv "$RUN/guard/want.n" "$RUN/guard/want"; rm -f "$RUN/guard/want.t"
  # only names and IPv4 subnets that are safe in the two files
  tr -d '\r' < "$RUN/guard/domains.t" | sed 's/^\.//' | while IFS= read -r _gd; do
    case "$_gd" in ""|*[!A-Za-z0-9.-]*|-*|.*|*..*) ;; *) printf '%s\n' "$_gd" ;; esac
  done | sort -u > "$RUN/guard/domains"
  tr -d '\r' < "$RUN/guard/subnets.t" | guard_ip4 | sort -u > "$RUN/guard/subnets"
  tr -d '\r' < "$RUN/guard/own.t" | sed 's/^\.//' | while IFS= read -r _gd; do
    case "$_gd" in ""|*[!A-Za-z0-9.-]*|-*|.*|*..*) ;; *) printf '%s\n' "$_gd" ;; esac
  done | sort -u > "$RUN/guard/own-domains.n" && mv "$RUN/guard/own-domains.n" "$RUN/guard/own-domains"
  rm -f "$RUN/guard/domains.t" "$RUN/guard/subnets.t" "$RUN/guard/own.t"
  guard_resolve_start; guard_lists_start
  sed 's|.*|address=/&/|' "$RUN/guard/domains" > "$RUN/guard/dnsmasq.conf"
  _gi=$(printf '%s' "$_gc" | jq -r '.sections[] | select(.name == "settings") | .options.source_network_interfaces
          | if type == "array" then . else [.] end | map(select(. != null and . != "")) | if length == 0 then ["br-lan"] else . end
          | map("\"" + . + "\"") | join(", ")')
  {
    echo "table inet $GUARD_TABLE"
    echo "delete table inet $GUARD_TABLE"
    echo "table inet $GUARD_TABLE {"
    echo "  set strict4 { type ipv4_addr; flags interval; auto-merge;"
    _ge=$(cat "$RUN/guard/subnets" "$RUN/guard/resolved" 2>/dev/null | guard_ip4 | sort -u | tr '\n' ',' | sed 's/,$//; s/,/, /g')
    [ -n "$_ge" ] && printf '    elements = { %s }\n' "$_ge"
    echo "  }"
    echo "  chain forward { type filter hook forward priority -5; policy accept;"
    echo "    iifname { $_gi } ip daddr @strict4 meta l4proto tcp reject with tcp reset"
    echo "    iifname { $_gi } ip daddr @strict4 reject"
    echo "    iifname { $_gi } ip daddr 198.18.0.0/15 meta l4proto tcp reject with tcp reset"
    echo "    iifname { $_gi } ip daddr 198.18.0.0/15 reject"
    echo "  }"
    echo "}"
  } > "$RUN/guard/nft.conf"
  printf '%s' "$_gk" > "$RUN/guard/key"
  rm -f "$RUN/guard/nft.applied"
  logger -t podkop-agent "strict guard: $(wc -l < "$RUN/guard/domains") domains, $(wc -l < "$RUN/guard/subnets") subnets, $(wc -l 2>/dev/null < "$RUN/guard/resolved" || echo 0) resolved addresses"
}

# guard_wanted: strict sections exist and podkop is meant to run
guard_wanted() { [ -s "$ETC/strict" ] && [ ! -f "$ETC/paused" ] && podkop_enabled; }

guard_nft_sync() {
  if guard_wanted && [ -s "$RUN/guard/nft.conf" ]; then
    if [ "$(cat "$RUN/guard/nft.applied" 2>/dev/null)" = "$(cat "$RUN/guard/key")" ] && guard_nft list table inet "$GUARD_TABLE" >/dev/null; then return 0; fi
    if guard_nft -f "$RUN/guard/nft.conf"; then cp "$RUN/guard/key" "$RUN/guard/nft.applied"; rm -f "$RUN/guard/nft.refused"
    elif ! cmp -s "$RUN/guard/key" "$RUN/guard/nft.refused"; then     # once per build, not every 15 s
      logger -t podkop-agent "strict guard: nft refused the table"; cp "$RUN/guard/key" "$RUN/guard/nft.refused"
      event strict_guard '{"nft":false}'
    fi
  elif guard_nft list table inet "$GUARD_TABLE" >/dev/null; then
    guard_nft delete table inet "$GUARD_TABLE"; rm -f "$RUN/guard/nft.applied"
  fi
}

# guard_dns_on restart|norestart: the NXDOMAIN file in; "norestart" before a
# podkop restart, whose own dnsmasq restart then reads the file
guard_dns_on() {
  [ -s "$RUN/guard/dnsmasq.conf" ] || return 0
  _gf="$(guard_dnsmasq_dir)/$GUARD_DNS"
  cmp -s "$RUN/guard/dnsmasq.conf" "$_gf" 2>/dev/null && return 0
  mkdir -p "${_gf%/*}" && cp "$RUN/guard/dnsmasq.conf" "$_gf.t" && mv "$_gf.t" "$_gf" || return 1
  [ "$1" = norestart ] || guard_dnsmasq_restart
  event strict_guard '{"dns":true}'
}
guard_dns_off() {
  _gf="$(guard_dnsmasq_dir)/$GUARD_DNS"
  [ -f "$_gf" ] || return 0
  rm -f "$_gf"; guard_dnsmasq_restart
  event strict_guard '{"dns":false}'
}

# guard_sync: the daemon's step, every 15 s and within a second of a
# sing-box or podkop service event
guard_sync() {
  if ! guard_wanted; then
    guard_nft_sync; guard_dns_off; return 0
  fi
  guard_build || return 0
  guard_nft_sync
  if [ -z "$(singbox_pid)" ] || [ "$(podkop_start_state)" = running ]; then guard_dns_on restart; else guard_dns_off; fi
}

guard_status() {
  _gf="$(guard_dnsmasq_dir)/$GUARD_DNS"
  jq -nc --arg d "$(wc -l 2>/dev/null < "$RUN/guard/domains" || echo 0)" --arg s "$(wc -l 2>/dev/null < "$RUN/guard/subnets" || echo 0)" \
    --arg r "$(wc -l 2>/dev/null < "$RUN/guard/resolved" || echo 0)" \
    --arg nft "$([ -f "$RUN/guard/nft.applied" ] && echo 1)" --arg dns "$([ -f "$_gf" ] && echo 1)" \
    '{domains: ($d | tonumber? // 0), subnets: ($s | tonumber? // 0), resolved: ($r | tonumber? // 0), nft: ($nft == "1"), dns: ($dns == "1")}'
}

# strict_enforce <proxies file> <sing-box config>: each strict section's
# selector on its refusing member while its servers are down, on its usual
# member otherwise. A section with its own URLTest group follows the group's
# .block; a section that sends through another rule (its selector's first
# member is e.g. main-out) refuses while that rule's own servers are all
# down (.own false), even if that rule borrowed servers. Pins win.
strict_enforce() {
  [ -s "$ETC/strict" ] || return 0
  jq -nr --slurpfile st "$RUN/heal/state.json" --slurpfile pf "$1" --slurpfile cfg "$2" \
     --argjson strict "$(strict_json)" --arg fp "$(config_fp 2>/dev/null)" '
    (($st[0] // {}) | if (.fingerprint // "") == $fp then (.groups // {}) else {} end) as $g
    | ($pf[0] // {}) as $p | (($cfg[0] // {}).outbounds // []) as $ob
    | $strict[] | select(type == "string") | . as $s
    | select(($p.proxies["\($s)-out"].all // []) | index("\($s)-strict-out"))
    | ([$ob[] | select(.tag == "\($s)-out") | .outbounds[]? | select(. != "\($s)-strict-out")]
       | if index("\($s)-urltest-out") then "\($s)-urltest-out" else (first // "") end) as $usual
    | select($usual != "")
    | (if $usual == "\($s)-urltest-out" then {want: (if ($g[$s].block // false) then "\($s)-strict-out" else $usual end), dep: false}
       else ($usual | if endswith("-out") then .[:-4] else . end) as $via
            | {want: (if ($g[$via].reported // false) and (($g[$via].own // false) | not) then "\($s)-strict-out" else $usual end), dep: true}
       end) as $w
    | select($p.proxies["\($s)-out"].now != $w.want) | "\($s)\t\($w.want)\t\($w.dep)"' 2>/dev/null |
  while IFS="$TAB" read -r _ss _sw _sd; do
    [ -n "$_ss" ] || continue
    grep -q "^$_ss$TAB" "$ETC/pins" 2>/dev/null && continue
    if select_server "$_ss" "$_sw"; then
      # a section that follows another rule: its own events (the rule's come from the plan)
      if [ "$_sd" = true ]; then
        case "$_sw" in *-strict-out) event strict_closed "$(jq -nc --arg s "$_ss" '{section: $s, via: true}')" ;;
                       *) event strict_open "$(jq -nc --arg s "$_ss" '{section: $s}')" ;; esac
      fi
    else logger -t podkop-agent "strict: could not switch $_ss-out to $_sw"; fi
  done
}

HEAL_PLAN_JQ='
def alive($t): ((($p[0].proxies[$t].history) // []) | last | (.delay // 0)) > 0;
def delay($t): ((($p[0].proxies[$t].history) // []) | last | (.delay // 0)) as $d | if $d > 0 then $d else 1000000 end;
def dedupe: reduce .[] as $t ([]; if index($t) then . else . + [$t] end);
($st[0] // {}) as $s
# a settings change (an apply) starts every group anew, but a group reported
# down stays "alerted" until its servers answer, so its recovery is reported
| (if ($s.fingerprint // "") == $fp then ($s.groups // {})
   else (($s.groups // {}) | with_entries(select(.value.reported or .value.alerted) | .value = {alerted: true})) end) as $old
| ($off | index("*") != null) as $alloff
| [($cfg[0].outbounds // [])[] | select(.type == "urltest" and (.tag | endswith("-urltest-out")))
   | (.tag[:-12]) as $sec | ($old[$sec] // {}) as $o
   | (($o.added // []) + ($o.extra // [])) as $ours
   | ($alloff or ($off | index($sec) != null)) as $nh
   | {sec: $sec, off: ($nh and (($watch | index($sec)) | not)), heal: ($nh | not),
      strikes: ($o.strikes // 0), good: ($o.good // 0), borrowed_at: ($o.borrowed_at // 0), block: ($o.block // false),
      strict: ($strict | index($sec) != null),
      added: ($o.added // []), extra: ($o.extra // []), lease: ($o.lease // 0), hold: ($o.hold // 0),
      dead_since: $o.dead_since, reported: ($o.reported // false), alerted: ($o.alerted // false),
      orig: [((.outbounds // []) + (.fallbacks // []))[] | select(. as $t | $ours | index($t) | not)]}] as $G
| [$G[] | select(.heal) | .sec as $sec | .orig[] | select(alive(.)) | {sec: $sec, tag: ., d: delay(.)}] as $live
| reduce $G[] as $g ({groups: {}, events: [], apply: false, lists: false, due: false};
    if $g.off then
      if (($g.added + $g.extra) | length) > 0 then .apply = true else . end
    elif ([$g.orig[] | select(alive(.))] | length) > 0 then
      if (($g.added + $g.extra) | length) > 0 or $g.reported or $g.alerted then
        # up again only after several good checks in a row, and not before the
        # borrowed servers have been in for a while: one flapping server must
        # not reload sing-box (and cut every connection) every minute
        ($g.good + 1) as $good
        | if $good >= $recover and ($now - $g.borrowed_at) >= $minhold then
            .apply = (.apply or (($g.added + $g.extra) | length) > 0) | .lists = true
            | .events += [{type: "group_recovered", data: {section: $g.sec, servers: ($g.added + $g.extra)}}]
          else
            .groups[$g.sec] = {dead_since: $g.dead_since, strikes: $g.strikes, reported: $g.reported, added: $g.added,
                               extra: $g.extra, lease: $g.lease, hold: $g.hold, alerted: $g.alerted,
                               borrowed_at: $g.borrowed_at, good: $good, own: true}
            # a strict rule is open again the moment one of its servers answers
            | if $g.block then .events += [{type: "strict_open", data: {section: $g.sec}}] else . end
          end
      else . end
    else
      ($g.dead_since // $now) as $since
      | ($g.strikes + 1) as $k
      | (($g.extra | length) > 0 and $g.lease < $now) as $expired
      | (if $expired then [] else $g.extra end) as $extra
      | if $k < $need and ($g.reported | not) then .groups[$g.sec] = {dead_since: $since, strikes: $k, alerted: $g.alerted}
        elif $wan != true then
          .due = true
          | .groups[$g.sec] = {dead_since: $since, strikes: $k, reported: $g.reported, added: $g.added, extra: $extra,
                               lease: $g.lease, hold: $g.hold, alerted: $g.alerted, borrowed_at: $g.borrowed_at, block: $g.block,
                               own: (([$extra[] | select(alive(.))] | length) > 0)}
          | if $expired then .apply = true else . end
        else
          (if $g.strict or ($g.heal | not) then []
           elif ([($g.added + $extra)[] | select(alive(.))] | length) > 0 or $g.hold > $now then $g.added
           else [$live | sort_by(.d)[] | select(.sec != $g.sec) | .tag | select(. as $t | $g.orig | index($t) | not)]
                | dedupe | .[:3] end) as $donors
          # strict and nothing of its own (not even the manager'"'"'s servers) answers: refuse connections
          | ($g.strict and ([$extra[] | select(alive(.))] | length) == 0) as $block
          | .groups[$g.sec] = {dead_since: $since, strikes: $k, reported: true, added: $donors, extra: $extra,
                               lease: (if $expired then 0 else $g.lease end), hold: $g.hold, alerted: $g.alerted,
                               borrowed_at: (if $donors != $g.added then $now else $g.borrowed_at end), block: $block,
                               own: (([$extra[] | select(alive(.))] | length) > 0)}
          | if $donors != $g.added or $expired then .apply = true else . end
          | if ($donors | length) > 0 and $donors != $g.added then .lists = true else . end
          | if ($g.reported | not) or $expired then
              .events += [{type: "group_down", data: {section: $g.sec, servers: $g.orig, borrowed: $donors,
                                                      down_seconds: ($now - $since)}}]
            else . end
          | if $block and ($g.block | not) then .events += [{type: "strict_closed", data: {section: $g.sec, servers: $g.orig}}]
            elif ($block | not) and $g.block then .events += [{type: "strict_open", data: {section: $g.sec}}] else . end
        end
    end)
| {state: {fingerprint: $fp, groups: .groups}, events, apply, lists, due}'

# heal_plan <sing-box config> <proxies file> <fingerprint> <wan: true|false|null>
heal_plan() {
  jq -nc --slurpfile cfg "$1" --slurpfile p "$2" --slurpfile st "$RUN/heal/state.json" --arg fp "$3" \
    --argjson wan "$4" --argjson now "$(date +%s)" --argjson off "$(heal_off_json)" --argjson need "$HEAL_STRIKES" \
    --argjson recover "$HEAL_RECOVER" --argjson minhold "$HEAL_MIN_HOLD" --argjson strict "$(strict_json)" \
    --argjson watch "$(heal_watch_json "$1")" "$HEAL_PLAN_JQ"
}

# heal_filter <in> <out>: sing-box's config with the borrowed servers and the
# manager's in their groups; a state for other podkop settings changes nothing
heal_filter() {
  if [ ! -s "$RUN/heal/state.json" ]; then cp "$1" "$2" && strict_filter "$2"; return; fi
  [ -s "$RUN/heal/extra.json" ] || echo '{}' > "$RUN/heal/extra.json"
  jq --slurpfile st "$RUN/heal/state.json" --slurpfile ex "$RUN/heal/extra.json" --arg fp "$(config_fp)" '
    ($st[0] // {}) as $s | ($ex[0] // {}) as $x
    | if ($s.fingerprint // "") != $fp then . else
      reduce (($s.groups // {}) | to_entries[]) as $g (.;
        "\($g.key)-urltest-out" as $ut
        | ($g.value.extra // []) as $et
        | [($x[$g.key] // [])[] | select(.tag as $t | $et | index($t))] as $o
        | if (any(.outbounds[]?; .tag == $ut) | not) then . else
            [.outbounds[]?.tag] as $have
            | .outbounds += [$o[] | select(.tag as $t | $have | index($t) | not)]
            | [.outbounds[]?.tag] as $all
            | [($g.value.added // [])[], $o[].tag] as $want
            | (.outbounds[] | select(.tag == $ut) | .outbounds) |= (. as $cur
                | $cur + [$want[] | select(. as $t | ($all | index($t)) and ($cur | index($t) | not))])
          end)
      end' "$1" > "$2" && strict_filter "$2"
}

# clash_get <path> [curl -G args]: sing-box's Clash API
clash_get() {
  _cg=$(uci -q get podkop.settings.config_path); [ -n "$_cg" ] || _cg=/etc/sing-box/config.json
  _ca=$(jq -r '.experimental.clash_api.external_controller // empty' "$_cg" 2>/dev/null)
  _cs=$(jq -r '.experimental.clash_api.secret // empty' "$_cg" 2>/dev/null)
  [ -n "$_ca" ] || return 1
  _cp=$1; shift
  if [ -n "$_cs" ]; then curl -s --max-time 12 -G -H "Authorization: Bearer $_cs" "$@" "http://$_ca$_cp"
  else curl -s --max-time 12 -G "$@" "http://$_ca$_cp"; fi
}

# heal_test <sing-box config> <proxies file>: fresh results in sing-box's
# history (its URLTest picks by them too). First the server each group uses
# now; then every server of the groups whose server failed, that have no
# working server, or that are being healed. The proxies file is read again.
heal_test() {
  _ht="$RUN/heal/test"; rm -rf "$_ht"; mkdir -p "$_ht"
  jq -r --slurpfile p "$2" --argjson off "$(heal_off_json)" --argjson watch "$(heal_watch_json "$1")" '
    .outbounds[]? | select(.type == "urltest" and (.tag | endswith("-urltest-out")))
    | select(.tag[:-12] as $s | ($watch | index($s)) or (($off | index("*") or ($off | index($s))) | not))
    | [.tag, ($p[0].proxies[.tag].now // ""), (.url // "https://www.gstatic.com/generate_204")] | @tsv' "$1" > "$_ht/groups"
  while IFS="$TAB" read -r _hg _hn _hu; do
    case "$_hn" in ""|*[!A-Za-z0-9_-]*) echo "$_hg" > "$_ht/$_hg.failed"; continue ;; esac
    ( clash_get "/proxies/$_hn/delay" --data-urlencode "url=$_hu" --data "timeout=$HEAL_TIMEOUT" > "$_ht/$_hg.now"
      jq -e '(.delay // 0) > 0' "$_ht/$_hg.now" >/dev/null 2>&1 || echo "$_hg" > "$_ht/$_hg.failed" ) &
  done < "$_ht/groups"
  wait
  proxies > "$2.t" 2>/dev/null && jq -e .proxies "$2.t" >/dev/null 2>&1 && mv "$2.t" "$2"
  jq -r --slurpfile p "$2" --slurpfile st "$RUN/heal/state.json" '
    def alive($t): ((($p[0].proxies[$t].history) // []) | last | (.delay // 0)) > 0;
    .outbounds[]? | select(.type == "urltest" and (.tag | endswith("-urltest-out")))
    | select((((.outbounds // []) + (.fallbacks // [])) | map(select(alive(.))) | length) == 0
             or ((($st[0].groups // {})[.tag[:-12]] // {}) | ((.added // []) + (.extra // [])) | length) > 0)
    | .tag' "$1" > "$_ht/suspects" 2>/dev/null
  cat "$_ht"/*.failed 2>/dev/null >> "$_ht/suspects"
  # each server on its own: sing-box's group test skips servers tested within
  # the last interval, so their result could be minutes old
  sort -u "$_ht/suspects" | while IFS= read -r _hg; do
    grep -q "^$_hg$TAB" "$_ht/groups" || continue
    _hu=$(awk -F "$TAB" -v g="$_hg" '$1 == g { print $3; exit }' "$_ht/groups")
    jq -r --arg g "$_hg" '.outbounds[]? | select(.tag == $g) | ((.outbounds // []) + (.fallbacks // []))[:12][]' "$1" | {
      while IFS= read -r _hm; do
        case "$_hm" in ""|*[!A-Za-z0-9_-]*) continue ;; esac
        clash_get "/proxies/$_hm/delay" --data-urlencode "url=$_hu" --data "timeout=$HEAL_TIMEOUT" >/dev/null &
      done
      wait; }
  done
  proxies > "$2.t" 2>/dev/null && jq -e .proxies "$2.t" >/dev/null 2>&1 && mv "$2.t" "$2"
  rm -rf "$_ht"
}

# heal_wan: the provider's line answers (a lookup straight to an outside DNS
# server, which podkop does not route), so a dead group is not just a WAN outage
heal_wan() {
  _hd=$(ubus call network.interface.wan status 2>/dev/null | jq -r '.["dns-server"][0] // empty')
  for _hs in $_hd 77.88.8.8 8.8.8.8; do
    case "$_hs" in ""|*[!0-9.]*) continue ;; esac
    diagnostic_lookup 4 example.com "$_hs" 2>/dev/null |
      awk '/^Name:/ { n = 1; next } n && /^Address/ { f = 1 } END { exit !f }' && return 0
  done
  return 1
}

# heal_lists: podkop's last list update failed (its lists come through the
# proxy): once a group works again, update them now, not at the next hour
heal_lists() {
  [ -n "$(find "$RUN/heal/lists.t" -mmin -30 2>/dev/null)" ] && return 0
  logread 2>/dev/null | awk '/Starting lists update/ { f = 0 } /\[error\] Download .* failed/ { f = 1 } END { exit !f }' || return 0
  touch "$RUN/heal/lists.t"
  logger -t podkop-agent "self-heal: podkop's lists failed to download; updating them again"
  setsid sh -c 'sleep 30; podkop list_update' </dev/null >/dev/null 2>&1 &
}

# heal_events <plan>
heal_events() {
  printf '%s' "$1" | jq -c '.events[]' | while IFS= read -r _he; do
    event "$(printf '%s' "$_he" | jq -r .type)" "$(printf '%s' "$_he" | jq -c .data)"
  done
}

# heal_commit <new state json> <marker>: reload sing-box with it (holds the
# apply lock); on failure the old state stays, with a hold on new tries
heal_commit() {
  exec 9> "$RUN/apply.lock"; flock -n 9 || { exec 9>&-; return 2; }
  cp "$RUN/heal/state.json" "$RUN/heal/state.old"
  printf '%s\n' "$1" > "$RUN/heal/state.json"
  if ! _hwhy=$(hot_apply "$2"); then
    jq -c --argjson h "$(($(date +%s) + HEAL_HOLD))" '.groups |= map_values(.hold = $h | .added = [] | .extra = [])' \
      "$RUN/heal/state.json" > "$RUN/heal/state.t" && mv "$RUN/heal/state.t" "$RUN/heal/state.json"
    rm -f "$RUN/heal/state.old"
    hot_apply "self-heal undone" >/dev/null 2>&1
    exec 9>&-
    event heal_failed "$(jq -nc --arg r "$_hwhy" '{reason: $r}')"
    return 1
  fi
  rm -f "$RUN/heal/state.old"
  exec 9>&-
  return 0
}

# heal_check: one round (the daemon runs it every 30 s)
heal_check() {
  mkdir -p "$RUN/heal"
  _hc=$(uci -q get podkop.settings.config_path); [ -n "$_hc" ] || _hc=/etc/sing-box/config.json
  [ -s "$_hc" ] || return 0
  proxies > "$RUN/heal/proxies.json" 2>/dev/null
  if ! jq -e '.proxies' "$RUN/heal/proxies.json" >/dev/null 2>&1; then
    # sing-box runs but its API is silent: nothing here can watch the servers
    # or close a kill switch. After HEAL_SILENT rounds in a row podkop is
    # restarted, once (the count goes on until the API answers again).
    _hn=$(( $(cat "$RUN/heal/silent" 2>/dev/null || echo 0) + 1 )); echo "$_hn" > "$RUN/heal/silent"
    if [ "$_hn" = "$HEAL_SILENT" ]; then
      event clash_api_silent "$(jq -nc --argjson s "$((HEAL_SILENT * 30))" '{seconds: $s}')"
      if command -v recover >/dev/null 2>&1; then recover; fi
    fi
    return 0
  fi
  rm -f "$RUN/heal/silent"
  _hf=$(config_fp) || return 0
  [ -s "$RUN/heal/state.json" ] || echo '{}' > "$RUN/heal/state.json"
  heal_test "$_hc" "$RUN/heal/proxies.json"
  _hplan=$(heal_plan "$_hc" "$RUN/heal/proxies.json" "$_hf" null) || return 0
  if [ "$(printf '%s' "$_hplan" | jq -r .due)" = true ]; then
    if heal_wan; then _hw=true; else _hw=false; fi
    _hplan=$(heal_plan "$_hc" "$RUN/heal/proxies.json" "$_hf" "$_hw") || return 0
  fi
  _hnew=$(printf '%s' "$_hplan" | jq -c .state)
  if [ "$(printf '%s' "$_hplan" | jq -r .apply)" = true ]; then
    heal_commit "$_hnew" "self-heal: $(printf '%s' "$_hplan" | jq -r '[.state.groups | to_entries[]
      | "\(.key) +\((.value.added // []) + (.value.extra // []) | length)"] | join(", ") | if . == "" then "borrowed servers removed" else . end')"
    case $? in 2) return 0 ;; 1) heal_events "$_hplan"; return 0 ;; esac
  else
    printf '%s\n' "$_hnew" > "$RUN/heal/state.json"
  fi
  heal_events "$_hplan"
  strict_enforce "$RUN/heal/proxies.json" "$_hc"
  [ "$(printf '%s' "$_hplan" | jq -r .lists)" = true ] && heal_lists
  return 0
}

# heal_status: for "status" and "heal"
heal_status() {
  _hf=$(config_fp 2>/dev/null)
  jq -nc --argjson off "$(heal_off_json)" --slurpfile st "$RUN/heal/state.json" --arg fp "$_hf" '
    ($st[0] // {}) as $s
    | {off: $off, strict: $strict, guard: $guard, groups: (if ($s.fingerprint // "") == $fp then ($s.groups // {}) else {} end)}' \
    --argjson strict "$(strict_json)" --argjson guard "$(guard_status)" 2>/dev/null ||
    jq -nc --argjson off "$(heal_off_json)" --argjson strict "$(strict_json)" '{off: $off, strict: $strict, guard: null, groups: {}}'
}

cmd_heal() { reply "$(heal_status)"; }

# heal-off: stdin {"sections": [names, or "*"]}
cmd_heal_off() {
  _hr=$(head -c 65536)
  printf '%s' "$_hr" | jq -e '(.sections | type == "array" and length <= 100)
    and all(.sections[]; type == "string" and (. == "*" or (length > 0 and (explode | all(. == 95
      or (. >= 48 and . <= 57) or (. >= 65 and . <= 90) or (. >= 97 and . <= 122)))))) ' >/dev/null 2>&1 ||
    fail bad_request 'expected {"sections": [section names, or "*"]}'
  printf '%s' "$_hr" | jq -r '.sections | unique | .[]' > "$RUN/heal-off.t"
  if [ -s "$RUN/heal-off.t" ]; then
    cmp -s "$RUN/heal-off.t" "$ETC/heal-off" 2>/dev/null ||
      { cp "$RUN/heal-off.t" "$ETC/heal-off.t" && mv "$ETC/heal-off.t" "$ETC/heal-off"; } || fail error "could not write $ETC/heal-off"
  else
    rm -f "$ETC/heal-off"
  fi
  rm -f "$RUN/heal-off.t"
  event heal_off "$(jq -nc --argjson s "$(heal_off_json)" '{sections: $s}')"
  reply "$(heal_status)"
}

# strict: stdin {"sections": [names]}: the strict rules; sing-box reloads in
# place when the set changes (their refusing members go into the selectors)
cmd_strict() {
  _sr=$(head -c 65536)
  printf '%s' "$_sr" | jq -e '(.sections | type == "array" and length <= 100)
    and all(.sections[]; type == "string" and length > 0 and (explode | all(. == 95
      or (. >= 48 and . <= 57) or (. >= 65 and . <= 90) or (. >= 97 and . <= 122))))' >/dev/null 2>&1 ||
    fail bad_request 'expected {"sections": [section names]}'
  printf '%s' "$_sr" | jq -r '.sections | unique | .[]' > "$RUN/strict.t"
  _changed=1
  if [ -s "$RUN/strict.t" ]; then
    if cmp -s "$RUN/strict.t" "$ETC/strict" 2>/dev/null; then _changed=0
    else { cp "$RUN/strict.t" "$ETC/strict.t" && mv "$ETC/strict.t" "$ETC/strict"; } || { rm -f "$RUN/strict.t"; fail error "could not write $ETC/strict"; }; fi
  else
    [ -f "$ETC/strict" ] || _changed=0
    rm -f "$ETC/strict"
  fi
  rm -f "$RUN/strict.t"
  if [ "$_changed" = 1 ]; then
    event strict "$(jq -nc --argjson s "$(strict_json)" '{sections: $s}')"
    if [ -n "$(singbox_pid)" ]; then
      exec 9> "$RUN/apply.lock"; flock -n 9 || fail busy "an apply is in progress"
      _sw=$(hot_apply "strict rules changed") || { exec 9>&-; fail recovery_failed "sing-box did not take the change: $_sw"; }
      exec 9>&-
      _sc=$(uci -q get podkop.settings.config_path); [ -n "$_sc" ] || _sc=/etc/sing-box/config.json
      proxies > "$RUN/heal/proxies.json" 2>/dev/null && strict_enforce "$RUN/heal/proxies.json" "$_sc"
    fi
    guard_sync
  fi
  reply "$(heal_status)"
}

# heal-links: stdin {"section", "fingerprint", "links": [up to 5]}: the
# manager's servers for a down group, built by podkop's parser, in RAM only
cmd_heal_links() {
  _hr=$(head -c 65536)
  printf '%s' "$_hr" | jq -e '(.section | type == "string") and (.fingerprint | type == "string")
    and (.links | type == "array" and length > 0 and length <= 5 and all(type == "string"))' >/dev/null 2>&1 ||
    fail bad_request 'expected {"section": ..., "fingerprint": ..., "links": [up to 5]}'
  _hs=$(printf '%s' "$_hr" | jq -r .section)
  case "$_hs" in ""|*[!A-Za-z0-9_]*) fail bad_request "section name expected" ;; esac
  heal_off_json | jq -e --arg s "$_hs" 'index($s) or index("*")' >/dev/null && fail heal_off "self-heal is off for $_hs"
  _hf=$(config_fp) || fail busy "configuration unavailable"
  [ "$(printf '%s' "$_hr" | jq -r .fingerprint)" = "$_hf" ] || fail conflict "podkop's settings changed"
  _hc=$(uci -q get podkop.settings.config_path); [ -n "$_hc" ] || _hc=/etc/sing-box/config.json
  jq -e --arg t "$_hs-urltest-out" 'any(.outbounds[]?; .tag == $t and .type == "urltest")' "$_hc" >/dev/null 2>&1 ||
    fail not_found "section $_hs has no URLTest group"
  mkdir -p "$RUN/heal"
  _hi=0; : > "$RUN/heal/links.t"
  printf '%s' "$_hr" | jq -r '.links[]' | while IFS= read -r _hl; do
    _hi=$((_hi + 1))
    ( set +u; . "$PODKOP_LIB/sing_box_config_facade.sh"
      log() { :; }
      sing_box_cf_add_proxy_outbound '{"outbounds":[]}' "$_hs-heal-$_hi" "$_hl" 0 ) 2>/dev/null |
      jq -c --arg t "$_hs-heal-$_hi-out" '.outbounds[0] | select(type == "object") | .tag = $t' >> "$RUN/heal/links.t" 2>/dev/null
  done
  _hn=$(jq -sc '.' "$RUN/heal/links.t"); rm -f "$RUN/heal/links.t"
  [ "$(printf '%s' "$_hn" | jq length)" -gt 0 ] || fail bad_request "podkop cannot read these links"
  [ -s "$RUN/heal/extra.json" ] || echo '{}' > "$RUN/heal/extra.json"
  jq -c --arg s "$_hs" --argjson o "$_hn" '.[$s] = $o' "$RUN/heal/extra.json" > "$RUN/heal/extra.t" &&
    mv "$RUN/heal/extra.t" "$RUN/heal/extra.json"
  [ -s "$RUN/heal/state.json" ] || echo '{}' > "$RUN/heal/state.json"
  _hnew=$(jq -c --arg fp "$_hf" --arg s "$_hs" --argjson o "$_hn" --argjson now "$(date +%s)" --argjson l "$HEAL_LEASE" '
    (if (.fingerprint // "") == $fp then . else {fingerprint: $fp, groups: {}} end)
    | .groups[$s] = ((.groups[$s] // {dead_since: $now}) + {reported: true, extra: [$o[].tag], lease: ($now + $l), hold: 0, borrowed_at: $now})' \
    "$RUN/heal/state.json")
  heal_commit "$_hnew" "self-heal: $_hs +$(printf '%s' "$_hn" | jq length) from the manager"
  case $? in
    2) fail busy "an apply is in progress" ;;
    1) fail recovery_failed "sing-box did not take the servers" ;;
  esac
  event heal_links "$(jq -nc --arg s "$_hs" --argjson o "$_hn" '{section: $s, servers: [$o[].tag]}')"
  reply "$(heal_status)"
}
# end of podkop reliability
