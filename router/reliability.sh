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
diagnostic_lookup() (
  _dl_limit=$1; shift
  _dl_pid=''; _dl_elapsed=0
  trap '[ -z "$_dl_pid" ] || { kill -KILL "$_dl_pid" 2>/dev/null; wait "$_dl_pid" 2>/dev/null; }' EXIT
  trap 'exit 1' HUP INT TERM
  nslookup "$@" & _dl_pid=$!
  while kill -0 "$_dl_pid" 2>/dev/null; do
    [ "$_dl_elapsed" -lt "$_dl_limit" ] || exit 124
    sleep 1
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
  sleep 1
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
# $RUN/heal/state.json: {fingerprint, groups: {<section>: {dead_since, reported,
#   added: [borrowed tags], extra: [manager tags], lease, hold}}}
# $RUN/heal/extra.json: {<section>: [outbounds built from the manager's links]}

HEAL_LEASE=7200      # the manager's links, unless it sends them again
HEAL_HOLD=600        # after a failed reload, no new try for this long
HEAL_STRIKES=2       # checks in a row with no answering server before a group is down
HEAL_TIMEOUT=5000    # ms for a test request through a server

heal_off_json() {
  if [ -s "$ETC/heal-off" ]; then jq -Rsc 'split("\n") | map(select(. != ""))' "$ETC/heal-off"; else echo '[]'; fi
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
   | {sec: $sec, off: ($alloff or ($off | index($sec) != null)),
      strikes: ($o.strikes // 0),
      added: ($o.added // []), extra: ($o.extra // []), lease: ($o.lease // 0), hold: ($o.hold // 0),
      dead_since: $o.dead_since, reported: ($o.reported // false), alerted: ($o.alerted // false),
      orig: [((.outbounds // []) + (.fallbacks // []))[] | select(. as $t | $ours | index($t) | not)]}] as $G
| [$G[] | select(.off | not) | .sec as $sec | .orig[] | select(alive(.)) | {sec: $sec, tag: ., d: delay(.)}] as $live
| reduce $G[] as $g ({groups: {}, events: [], apply: false, lists: false, due: false};
    if $g.off then
      if (($g.added + $g.extra) | length) > 0 then .apply = true else . end
    elif ([$g.orig[] | select(alive(.))] | length) > 0 then
      if (($g.added + $g.extra) | length) > 0 or $g.reported or $g.alerted then
        .apply = (.apply or (($g.added + $g.extra) | length) > 0) | .lists = true
        | .events += [{type: "group_recovered", data: {section: $g.sec, servers: ($g.added + $g.extra)}}]
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
                               lease: $g.lease, hold: $g.hold, alerted: $g.alerted}
          | if $expired then .apply = true else . end
        else
          (if ([($g.added + $extra)[] | select(alive(.))] | length) > 0 or $g.hold > $now then $g.added
           else [$live | sort_by(.d)[] | select(.sec != $g.sec) | .tag | select(. as $t | $g.orig | index($t) | not)]
                | dedupe | .[:3] end) as $donors
          | .groups[$g.sec] = {dead_since: $since, strikes: $k, reported: true, added: $donors, extra: $extra,
                               lease: (if $expired then 0 else $g.lease end), hold: $g.hold, alerted: $g.alerted}
          | if $donors != $g.added or $expired then .apply = true else . end
          | if ($donors | length) > 0 and $donors != $g.added then .lists = true else . end
          | if ($g.reported | not) or $expired then
              .events += [{type: "group_down", data: {section: $g.sec, servers: $g.orig, borrowed: $donors,
                                                      down_seconds: ($now - $since)}}]
            else . end
        end
    end)
| {state: {fingerprint: $fp, groups: .groups}, events, apply, lists, due}'

# heal_plan <sing-box config> <proxies file> <fingerprint> <wan: true|false|null>
heal_plan() {
  jq -nc --slurpfile cfg "$1" --slurpfile p "$2" --slurpfile st "$RUN/heal/state.json" --arg fp "$3" \
    --argjson wan "$4" --argjson now "$(date +%s)" --argjson off "$(heal_off_json)" --argjson need "$HEAL_STRIKES" "$HEAL_PLAN_JQ"
}

# heal_filter <in> <out>: sing-box's config with the borrowed servers and the
# manager's in their groups; a state for other podkop settings changes nothing
heal_filter() {
  if [ ! -s "$RUN/heal/state.json" ]; then cp "$1" "$2"; return; fi
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
      end' "$1" > "$2"
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
  jq -r --slurpfile p "$2" --argjson off "$(heal_off_json)" '
    if ($off | index("*")) then empty else
    .outbounds[]? | select(.type == "urltest" and (.tag | endswith("-urltest-out")))
    | select(.tag[:-12] as $s | $off | index($s) | not)
    | [.tag, ($p[0].proxies[.tag].now // ""), (.url // "https://www.gstatic.com/generate_204")] | @tsv end' "$1" > "$_ht/groups"
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
  jq -e '.proxies' "$RUN/heal/proxies.json" >/dev/null 2>&1 || return 0
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
  [ "$(printf '%s' "$_hplan" | jq -r .lists)" = true ] && heal_lists
  return 0
}

# heal_status: for "status" and "heal"
heal_status() {
  _hf=$(config_fp 2>/dev/null)
  jq -nc --argjson off "$(heal_off_json)" --slurpfile st "$RUN/heal/state.json" --arg fp "$_hf" '
    ($st[0] // {}) as $s
    | {off: $off, groups: (if ($s.fingerprint // "") == $fp then ($s.groups // {}) else {} end)}' 2>/dev/null ||
    jq -nc --argjson off "$(heal_off_json)" '{off: $off, groups: {}}'
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
    | .groups[$s] = ((.groups[$s] // {dead_since: $now}) + {reported: true, extra: [$o[].tag], lease: ($now + $l), hold: 0})' \
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
