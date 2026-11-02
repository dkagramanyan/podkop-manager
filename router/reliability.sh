#!/bin/sh
# shellcheck shell=dash disable=SC2034,SC2317
# Sourced by podkop-agent. Diagnostics never change UCI or production listeners.

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
  jq -e '(.fingerprint | type == "string") and (.banned | type == "array" and length <= 100)
    and all(.banned[]; type == "string" and test("^[A-Za-z0-9_-]+$"))' "$RUN/policy.$$.in" >/dev/null || fail bad_request "invalid eligibility policy"
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
  jq -e '(.tags | type == "array" and length <= 4) and all(.tags[]; type == "string" and test("^[A-Za-z0-9_-]+$"))' "$_dir/request" >/dev/null ||
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
  sing-box run -c "$_dir/config.json" > "$_dir/engine.log" 2>&1 & _pid=$!
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
# end of podkop reliability
