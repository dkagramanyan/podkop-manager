"""Self-heal of dead URLTest groups on the router (router/reliability.sh)."""
import json
import subprocess
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "router/reliability.sh"

CONFIG = {"outbounds": [
    {"type": "urltest", "tag": "main-urltest-out", "outbounds": ["main-1-out", "main-2-out"],
     "fallbacks": ["main-fallback-1-out"], "interval": "3m"},
    {"type": "urltest", "tag": "youtube-urltest-out", "outbounds": ["youtube-1-out", "youtube-2-out"], "interval": "3m"},
    {"type": "urltest", "tag": "anthropic_am-urltest-out", "outbounds": ["anthropic_am-1-out"], "interval": "3m"},
    *[{"type": "vless", "tag": t} for t in ("main-1-out", "main-2-out", "main-fallback-1-out", "youtube-1-out",
                                             "youtube-2-out", "anthropic_am-1-out")],
    *[{"type": "selector", "tag": f"{g}-out", "outbounds": [f"{g}-1-out", f"{g}-urltest-out"], "default": f"{g}-urltest-out"}
      for g in ("main", "youtube", "anthropic_am")],
    {"type": "selector", "tag": "meta-out", "outbounds": ["main-out"]},       # an outbound rule that sends through main
    {"type": "direct", "tag": "direct-out"}]}

STUBS = '''
. "$1"
D="$2"; RUN="$D/run"; ETC="$D/etc"; mkdir -p "$RUN" "$ETC"
uci() { echo "$RUN/live.json"; }
config_fp() { cat "$D/fp"; }
proxies() { cat "$D/proxies.json"; }
date() { if [ "${1:-}" = +%s ]; then cat "$D/now"; else command date "$@"; fi; }
event() { printf '%s %s\\n' "$1" "${2:-{\\}}" >> "$D/events"; }
hot_apply() { echo "$1" >> "$D/applies"; heal_filter "$RUN/base.json" "$RUN/live.json"; [ ! -f "$D/fail" ]; }
heal_wan() { [ -f "$D/wan" ]; }
logread() { cat "$D/log" 2>/dev/null; }
logger() { :; }
clash_get() { echo "$*" >> "$D/tests"; }
select_server() { echo "$1 $2" >> "$D/selected"; }
singbox_pid() { echo 123; }
setsid() { echo "$*" >> "$D/spawned"; }
reply() { printf '%s\\n' "$1"; exit 0; }
fail() { echo "$*" >&2; exit 1; }
'''


class Router:
    def __init__(self, tmp: Path):
        self.d = tmp
        (tmp / "run").mkdir()
        (tmp / "run/base.json").write_text(json.dumps(CONFIG))
        (tmp / "run/live.json").write_text(json.dumps(CONFIG))
        (tmp / "fp").write_text("fp1")
        (tmp / "wan").write_text("")
        self.now(1000)
        self.alive([])

    def now(self, t):
        (self.d / "now").write_text(str(t))

    def alive(self, tags, delay=100, now=None):
        """now: {group: selected tag} of the sections' selectors (main-out etc.), with the live members"""
        live = json.loads((self.d / "run/live.json").read_text()) if (self.d / "run/live.json").exists() else CONFIG
        proxies = {t: {"history": [{"delay": delay if t in tags else 0}] if t in tags else []}
                   for t in [o["tag"] for o in CONFIG["outbounds"]] + ["main-heal-1-out"]}
        for o in live["outbounds"]:
            if o["type"] == "selector":
                proxies[o["tag"]] = {"all": o["outbounds"], "now": (now or {}).get(o["tag"], o.get("default", o["outbounds"][0]))}
        (self.d / "proxies.json").write_text(json.dumps({"proxies": proxies}))

    def sh(self, command, stdin=""):
        p = subprocess.run(["sh", "-c", STUBS + command, "sh", str(HELPER), str(self.d)], input=stdin,
                           capture_output=True, text=True, timeout=30)
        assert p.returncode == 0, p.stderr
        return p.stdout

    def check(self):
        self.sh("heal_check")

    def events(self):
        f = self.d / "events"
        rows = [line.split(" ", 1) for line in f.read_text().splitlines()] if f.exists() else []
        return [(t, json.loads(d)) for t, d in rows]

    def members(self, group="main"):
        live = json.loads((self.d / "run/live.json").read_text())
        return next(o for o in live["outbounds"] if o["tag"] == f"{group}-urltest-out")["outbounds"]

    def state(self):
        return json.loads((self.d / "run/heal/state.json").read_text())


def test_dead_group_borrows_working_servers_then_gives_them_back(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "run/../etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("anthropic_am\n")
    r.alive(["youtube-2-out", "youtube-1-out", "anthropic_am-1-out"])
    r.check()                                   # first check without an answer: one strike
    assert r.events() == [] and r.state()["groups"]["main"]["strikes"] == 1
    tests = (tmp_path / "tests").read_text()
    assert "/proxies/main-fallback-1-out/delay" in tests and "/proxies/anthropic_am-1-out/delay" not in tests
    r.now(1030); r.check()                      # the second, 30 s later: down
    (typ, data), = r.events()
    assert typ == "group_down" and data["section"] == "main"
    assert set(data["borrowed"]) == {"youtube-1-out", "youtube-2-out"}     # never the section marked off
    assert data["servers"] == ["main-1-out", "main-2-out", "main-fallback-1-out"]
    assert r.members()[:2] == ["main-1-out", "main-2-out"] and set(r.members()[2:]) == {"youtube-1-out", "youtube-2-out"}
    r.now(1060); r.check()                      # still down: no second report, no reload
    assert len(r.events()) == 1 and len((tmp_path / "applies").read_text().splitlines()) == 1
    r.alive(["youtube-1-out", "main-2-out"])    # one of its own servers works again
    for t in (1090, 1120, 1150, 1180, 1300):    # 4 good checks in a row, and 5 min since the borrow: not yet
        r.now(t); r.check()
    assert [t for t, _ in r.events()] == ["group_down"] and r.state()["groups"]["main"]["good"] == 5
    assert len((tmp_path / "applies").read_text().splitlines()) == 1
    r.now(1340); r.check()
    assert r.events()[-1][0] == "group_recovered"
    assert r.members() == ["main-1-out", "main-2-out"] and r.state()["groups"] == {}
    r.alive(["youtube-1-out"])                  # dead again: a flapping server starts the count anew
    r.now(1370); r.check(); r.now(1400); r.check()
    assert r.events()[-1][0] == "group_down" and r.state()["groups"]["main"].get("good", 0) == 0
    r.alive(["youtube-1-out", "main-2-out"])
    r.now(1430); r.check()
    assert r.events()[-1][0] == "group_down" and r.state()["groups"]["main"]["good"] == 1


def test_wan_outage_is_not_reported(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "wan").unlink()
    r.check(); r.now(1030); r.check(); r.now(1060); r.check()
    assert r.events() == [] and not (tmp_path / "applies").exists()
    (tmp_path / "wan").write_text("")
    r.now(2060); r.check()
    assert [t for t, _ in r.events()] == ["group_down"] * 3   # every group, nothing to borrow
    assert r.events()[0][1]["borrowed"] == []


def test_all_off_and_manager_links_expire(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("*\n")
    r.check(); r.now(5000); r.check()
    assert r.events() == []
    (tmp_path / "etc/heal-off").unlink()
    # the manager's servers: built by podkop's parser (stubbed), 2-hour lease
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/sing_box_config_facade.sh").write_text(
        'sing_box_cf_add_proxy_outbound() { printf \'{"outbounds":[{"type":"vless","tag":"%s-out","server":"x"}]}\' "$2"; }\n')
    out = r.sh(f'PODKOP_LIB="{tmp_path}/lib"; cmd_heal_links',
               json.dumps({"section": "main", "fingerprint": "fp1", "links": ["vless://a@b:1#x"]}))
    assert json.loads(out)["groups"]["main"]["extra"] == ["main-heal-1-out"]
    live = json.loads((tmp_path / "run/live.json").read_text())
    assert "main-heal-1-out" in r.members() and any(o["tag"] == "main-heal-1-out" for o in live["outbounds"])
    r.now(5000 + 7300); r.check()               # lease over: they go, and the group is reported again
    assert "main-heal-1-out" not in r.members()
    assert [t for t, _ in r.events()].count("group_down") >= 1


def test_settings_change_and_failed_reload(tmp_path):
    r = Router(tmp_path)
    r.alive(["youtube-1-out"])
    (tmp_path / "fail").write_text("")
    r.check(); r.now(1500); r.check()
    assert [t for t, _ in r.events()] == ["heal_failed", "group_down", "group_down"]   # main and anthropic_am
    assert r.state()["groups"]["main"]["added"] == [] and r.state()["groups"]["main"]["hold"] > 1500
    (tmp_path / "fail").unlink()
    r.now(1560); r.check()                      # held: no new try for 10 minutes
    assert len((tmp_path / "applies").read_text().splitlines()) == 2    # the try and its undo
    r.now(2200); r.check()
    assert "youtube-1-out" in r.members()
    (tmp_path / "fp").write_text("fp2")         # an apply: the old state means nothing now
    r.sh('heal_filter "$RUN/base.json" "$RUN/live.json"')
    assert r.members() == ["main-1-out", "main-2-out"]


def test_failed_lists_are_updated_once_a_group_works(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "log").write_text("podkop: [info] Starting lists update...\npodkop: [error] Download cloudflare list failed\n")
    r.alive(["youtube-1-out"])
    r.check(); r.now(1500); r.check()
    assert "podkop list_update" in (tmp_path / "spawned").read_text()


def test_strict_rule_refuses_instead_of_borrowing(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("anthropic_am\n")
    out = json.loads(r.sh("cmd_strict", json.dumps({"sections": ["main"]})))
    assert out["strict"] == ["main"] and (tmp_path / "etc/strict").read_text() == "main\n"
    assert (tmp_path / "applies").read_text().splitlines() == ["strict rules changed"]
    live = json.loads((tmp_path / "run/live.json").read_text())
    assert {"type": "socks", "tag": "main-strict-out", "server": "127.0.0.1", "server_port": 9} in live["outbounds"]
    sel = next(o for o in live["outbounds"] if o["tag"] == "main-out")
    assert sel["outbounds"][-1] == "main-strict-out" and sel["default"] == "main-urltest-out"
    assert not any(o["tag"] == "youtube-strict-out" for o in live["outbounds"])
    json.loads(r.sh("cmd_strict", json.dumps({"sections": ["main"]})))      # the same: nothing written, no reload
    assert len((tmp_path / "applies").read_text().splitlines()) == 1
    # its servers die while others work: nothing is borrowed, the selector refuses
    r.alive(["youtube-1-out", "youtube-2-out"])
    r.check(); r.now(1030); r.check()
    assert [t for t, _ in r.events()] == ["strict", "group_down", "strict_closed"]
    down = dict(r.events())["group_down"]
    assert down["borrowed"] == [] and r.state()["groups"]["main"]["block"] is True
    assert r.members() == ["main-1-out", "main-2-out"]
    assert (tmp_path / "selected").read_text().splitlines() == ["main main-strict-out"]
    r.alive(["youtube-1-out", "youtube-2-out"], now={"main-out": "main-strict-out"})
    r.now(1060); r.check()                      # still down: selected already, nothing to do
    assert (tmp_path / "selected").read_text().splitlines() == ["main main-strict-out"]
    # the manager's own servers for it (its history) are taken, and open it
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/sing_box_config_facade.sh").write_text(
        'sing_box_cf_add_proxy_outbound() { printf \'{"outbounds":[{"type":"vless","tag":"%s-out","server":"x"}]}\' "$2"; }\n')
    r.sh(f'PODKOP_LIB="{tmp_path}/lib"; cmd_heal_links', json.dumps({"section": "main", "fingerprint": "fp1", "links": ["vless://a@b:1#x"]}))
    assert "main-heal-1-out" in r.members()
    r.alive(["youtube-1-out", "youtube-2-out", "main-heal-1-out"], now={"main-out": "main-strict-out"})
    r.now(1090); r.check()
    assert r.events()[-1][0] == "strict_open" and r.state()["groups"]["main"]["block"] is False
    assert (tmp_path / "selected").read_text().splitlines()[-1] == "main main-urltest-out"
    # one of its own answers: open at once, up after the usual good checks
    r.alive(["youtube-1-out", "main-2-out"], now={"main-out": "main-urltest-out"})
    for t in (1120, 1150, 1180, 1210, 1400, 1430):
        r.now(t); r.check()
    assert r.events()[-1][0] == "group_recovered" and r.state()["groups"] == {}
    # a pinned section is left to its pin
    (tmp_path / "etc/pins").write_text("main\tabc\n")
    r.alive(["youtube-1-out"], now={"main-out": "main-1-out"})
    r.now(1460); r.check(); r.now(1490); r.check()
    assert r.state()["groups"]["main"]["block"] is True
    assert (tmp_path / "selected").read_text().splitlines()[-1] == "main main-urltest-out"
    json.loads(r.sh("cmd_strict", json.dumps({"sections": []})))
    assert not (tmp_path / "etc/strict").exists()
    assert not any(o["tag"] == "main-strict-out" for o in json.loads((tmp_path / "run/live.json").read_text())["outbounds"])


def test_kill_switch_on_a_rule_that_sends_through_another_rule(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("anthropic_am\n")
    (tmp_path / "etc/pins").write_text("")
    r.sh("cmd_strict", json.dumps({"sections": ["meta"]}))
    live = json.loads((tmp_path / "run/live.json").read_text())
    sel = next(o for o in live["outbounds"] if o["tag"] == "meta-out")
    assert sel["outbounds"] == ["main-out", "meta-strict-out"] and not any(o["tag"] == "main-strict-out" for o in live["outbounds"])
    # main's servers die; main (not strict) borrows youtube's, but meta refuses: main's own are all down
    r.alive(["youtube-1-out", "youtube-2-out"])
    r.check(); r.now(1030); r.check()
    assert set(r.members()[2:]) == {"youtube-1-out", "youtube-2-out"} and r.state()["groups"]["main"]["own"] is False
    assert (tmp_path / "selected").read_text().splitlines() == ["meta meta-strict-out"]
    assert [t for t, _ in r.events()][-1] == "strict_closed" and dict(r.events())["strict_closed"]["via"] is True
    r.alive(["youtube-1-out", "youtube-2-out"], now={"meta-out": "meta-strict-out"})
    r.now(1060); r.check()
    assert len((tmp_path / "selected").read_text().splitlines()) == 1
    # one of main's own servers answers: meta opens at once (main is still "recovering")
    r.alive(["youtube-1-out", "main-2-out"], now={"meta-out": "meta-strict-out"})
    r.now(1090); r.check()
    assert r.state()["groups"]["main"]["own"] is True and r.state()["groups"]["main"]["reported"] is True
    assert (tmp_path / "selected").read_text().splitlines()[-1] == "meta main-out"
    assert [t for t, _ in r.events()][-1] == "strict_open"


def test_kill_switch_works_with_self_heal_off(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("*\n")
    (tmp_path / "etc/pins").write_text("")
    r.sh("cmd_strict", json.dumps({"sections": ["main"]}))
    r.alive(["youtube-1-out", "youtube-2-out"])
    r.check(); r.now(1030); r.check()
    tests = (tmp_path / "tests").read_text()                 # only main's servers are tested
    assert "/proxies/main-1-out/delay" in tests and "/proxies/youtube-1-out/delay" not in tests
    assert [t for t, _ in r.events()] == ["strict", "group_down", "strict_closed"]
    assert dict(r.events())["group_down"]["borrowed"] == [] and r.members() == ["main-1-out", "main-2-out"]
    assert (tmp_path / "selected").read_text().splitlines() == ["main main-strict-out"]
    assert (tmp_path / "applies").read_text().splitlines() == ["strict rules changed"]     # no reload for borrowing
    r.alive(["youtube-1-out", "main-2-out"], now={"main-out": "main-strict-out"})
    r.now(1060); r.check()
    assert r.events()[-1][0] == "strict_open" and (tmp_path / "selected").read_text().splitlines()[-1] == "main main-urltest-out"
    # a rule that sends through main, with self-heal off for main only: main is watched, borrows nothing
    (tmp_path / "etc/heal-off").write_text("main\n")
    r.sh("cmd_strict", json.dumps({"sections": ["meta"]}))
    r.alive(["youtube-1-out", "youtube-2-out"])
    r.now(1090); r.check(); r.now(1120); r.check()
    assert r.members() == ["main-1-out", "main-2-out"] and r.state()["groups"]["main"]["own"] is False
    assert (tmp_path / "selected").read_text().splitlines()[-1] == "meta meta-strict-out"
    assert r.events()[-1][0] == "strict_closed" and r.events()[-1][1]["via"] is True


GUARD_STUBS = '''
PODKOP_LIB="$D/nolib"
config_json() { cat "$D/podkop.json"; }
podkop_enabled() { [ ! -f "$D/disabled" ]; }
podkop_start_state() { cat "$D/start" 2>/dev/null || echo 0; }
singbox_pid() { cat "$D/pid" 2>/dev/null; }
guard_dnsmasq_dir() { printf '%s' "$D/dnsmasq.d"; }
guard_dnsmasq_restart() { echo restart >> "$D/dnsmasq-restarts"; }
guard_fetch() { echo "$1" >> "$D/fetched"; [ ! -f "$D/fetch-fails" ] && echo srs > "$2"; }
guard_decompile() { printf '{"version":3,"rules":[{"domain_suffix":["youtube.com","ytimg.com"],"domain":["www.youtube.com"]},{"ip_cidr":["5.6.7.0/24"]}]}' > "$2"; }
guard_nft() { echo "$*" >> "$D/nft"; case "$1" in -f) touch "$D/nft-table" ;; delete) rm -f "$D/nft-table" ;; list) [ -f "$D/nft-table" ] ;; esac; }
resolve_host() { echo "$1" >> "$D/resolved-names"; case "$1" in netflix.com) echo 45.57.40.1 ;; fast.com) echo 45.57.40.2 ;; esac; }
guard_resolve_start() {      # as the real one, but in the foreground
  if [ -s "$RUN/guard/own-domains" ] && { [ ! -f "$RUN/guard/resolved" ] || [ -z "$(find "$RUN/guard/resolved" -mmin -60)" ]; }; then
    touch "$RUN/guard/resolved"; guard_resolve_job; fi; }
'''

PODKOP = {"sections": [
    {"name": "settings", "type": "settings", "anonymous": False, "options": {"source_network_interfaces": ["br-lan", "br-guest"]}},
    {"name": "main", "type": "section", "anonymous": False, "options": {
        "connection_type": "proxy", "proxy_config_type": "urltest", "community_lists": ["youtube"],
        "user_domain_list_type": "text", "user_domains_text": "netflix.com fast.com\n.nflxvideo.net\nbad domain\n",
        "user_subnet_list_type": "dynamic", "user_subnets": ["1.2.3.0/24", "not-a-subnet", "9.9.9.9", "300.1.1.1", "1.2.3.4/40"]}},
    {"name": "youtube", "type": "section", "anonymous": False, "options": {
        "connection_type": "proxy", "proxy_config_type": "urltest", "user_domain_list_type": "text", "user_domains_text": "other.example"}}]}


def test_strict_guard_blocks_the_rules_destinations_while_podkop_is_down(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/strict").write_text("main\n")
    (tmp_path / "podkop.json").write_text(json.dumps(PODKOP))
    (tmp_path / "pid").write_text("123")
    sh = lambda c, i="": r.sh(GUARD_STUBS + c, i)       # noqa: E731
    g = tmp_path / "run/guard"
    # the first build does not wait for the community list: the guard stands with the rule's own
    # domains and subnets at once (after a reboot, or with the list's host unreachable)
    (tmp_path / "fetch-fails").write_text("")
    sh("guard_sync; wait")
    assert "youtube.com" not in (g / "domains").read_text() and (tmp_path / "nft-table").exists()
    assert (g / "subnets").read_text().splitlines() == ["1.2.3.0/24", "9.9.9.9"]       # 300.1.1.1 and /40 would make nft refuse it all
    sh("guard_sync; wait")                             # the failed fetch is not tried again within 10 minutes
    assert (tmp_path / "fetched").read_text().count("\n") == 1
    (tmp_path / "fetch-fails").unlink(); (g / "fetch.stamp").unlink()
    sh("guard_sync; wait")                             # fetched in the background, the daemon is poked
    assert (g / "poke").exists() and (tmp_path / "fetched").read_text().count("\n") == 2
    (tmp_path / "fetched").write_text("u/youtube.srs\n")
    sh("guard_sync")                                   # built once more, with the list
    assert (g / "domains").read_text().splitlines() == sorted(["netflix.com", "fast.com", "nflxvideo.net", "youtube.com", "ytimg.com", "www.youtube.com", "bad", "domain"])
    assert (g / "subnets").read_text().splitlines() == ["1.2.3.0/24", "5.6.7.0/24", "9.9.9.9"]
    assert (tmp_path / "fetched").read_text().strip().endswith("/youtube.srs")
    nft = (g / "nft.conf").read_text()
    assert 'elements = { 1.2.3.0/24, 45.57.40.1, 45.57.40.2, 5.6.7.0/24, 9.9.9.9 }' in nft and 'iifname { "br-lan", "br-guest" } ip daddr @strict4' in nft
    # only the rules' own domains are resolved (not the community lists), once an hour
    assert (g / "own-domains").read_text().splitlines() == ["bad", "domain", "fast.com", "netflix.com", "nflxvideo.net"]
    assert set((tmp_path / "resolved-names").read_text().split()) == {"bad", "domain", "fast.com", "netflix.com", "nflxvideo.net"}
    assert "198.18.0.0/15" in nft and "delete table inet podkop_agent_guard" in nft
    assert (tmp_path / "nft").read_text().splitlines()[-1] == f"-f {g}/nft.conf"     # the table is in while podkop runs
    assert not (tmp_path / "dnsmasq.d/podkop-agent-strict.conf").exists()               # the DNS part is not
    assert json.loads(sh("guard_status")) == {"domains": 8, "subnets": 3, "resolved": 2, "nft": True, "dns": False}
    sh("guard_sync")                                   # nothing changed: nothing fetched, resolved or applied again
    assert (tmp_path / "fetched").read_text().count("\n") == 1 and (tmp_path / "nft").read_text().splitlines()[-1].startswith("list ")
    assert (tmp_path / "resolved-names").read_text().count("\n") == 5
    # sing-box goes away: the domains answer NXDOMAIN after one dnsmasq restart
    (tmp_path / "pid").write_text("")
    sh("guard_sync")
    conf = (tmp_path / "dnsmasq.d/podkop-agent-strict.conf").read_text().splitlines()
    assert "address=/netflix.com/" in conf and "address=/youtube.com/" in conf and len(conf) == 8
    assert (tmp_path / "dnsmasq-restarts").read_text().count("restart") == 1
    sh("guard_sync")                                   # still down: no second restart
    assert (tmp_path / "dnsmasq-restarts").read_text().count("restart") == 1
    # podkop starting (not finished) keeps it; podkop up takes it out
    (tmp_path / "pid").write_text("124"); (tmp_path / "start").write_text("running")
    sh("guard_sync")
    assert (tmp_path / "dnsmasq.d/podkop-agent-strict.conf").exists()
    (tmp_path / "start").write_text("0")
    sh("guard_sync")
    assert not (tmp_path / "dnsmasq.d/podkop-agent-strict.conf").exists()
    assert (tmp_path / "dnsmasq-restarts").read_text().count("restart") == 2
    assert [t for t, _ in r.events()] == ["strict_guard", "strict_guard"]
    # before a podkop restart the file goes in without a dnsmasq restart of ours (podkop's follows)
    sh("guard_dns_on norestart")
    assert (tmp_path / "dnsmasq.d/podkop-agent-strict.conf").exists() and (tmp_path / "dnsmasq-restarts").read_text().count("restart") == 2
    # no strict sections any more: table and file go
    (tmp_path / "etc/strict").unlink()
    sh("guard_sync")
    assert not (tmp_path / "nft-table").exists() and not (tmp_path / "dnsmasq.d/podkop-agent-strict.conf").exists()
    assert json.loads(sh("guard_status"))["nft"] is False


def test_silent_clash_api_restarts_podkop_once(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "proxies.json").write_text("")
    stub = 'recover() { echo recover >> "$D/recovers"; }\n'
    for t in (1000, 1030, 1060):
        r.now(t); r.sh(stub + "heal_check")
    assert r.events() == [] and not (tmp_path / "recovers").exists()
    for t in (1090, 1120, 1150):                # the 4th silent round: an event and one restart, no more after it
        r.now(t); r.sh(stub + "heal_check")
    assert r.events() == [("clash_api_silent", {"seconds": 120})] and (tmp_path / "recovers").read_text() == "recover\n"
    r.alive(["main-1-out"]); r.now(1180); r.check()
    assert not (tmp_path / "run/heal/silent").exists()


def test_heal_off_command_validates_and_writes_only_on_change(tmp_path):
    r = Router(tmp_path)
    out = json.loads(r.sh("cmd_heal_off", json.dumps({"sections": ["anthropic_am"]})))
    assert out["off"] == ["anthropic_am"] and (tmp_path / "etc/heal-off").read_text() == "anthropic_am\n"
    p = subprocess.run(["sh", "-c", STUBS + "cmd_heal_off", "sh", str(HELPER), str(tmp_path)],
                       input=json.dumps({"sections": ["bad name"]}), capture_output=True, text=True)
    assert p.returncode == 1
    json.loads(r.sh("cmd_heal_off", json.dumps({"sections": []})))
    assert not (tmp_path / "etc/heal-off").exists()


# --- manager ----------------------------------------------------------------------------------

import asyncio  # noqa: E402
import time  # noqa: E402

from podkop_manager import podkop  # noqa: E402
from podkop_manager.db import DB  # noqa: E402
from podkop_manager.hub import Hub, RouterRuntime  # noqa: E402


def test_link_host_and_network():
    assert podkop.link_host("vless://uuid@Host.example:443?type=tcp#name") == "host.example"
    assert podkop.link_host("ss://YWVzOnBhc3M=@1.2.3.4:2030#x") == "1.2.3.4"
    assert podkop.link_host("vless://u@[2001:db8::1]:443") == "2001:db8::1"
    assert podkop.link_host("vmess://eyJhZGQiOiJ4In0=") == ""
    assert podkop.entry_net("62.144.49.101") == "62.144.49.0/24"
    assert podkop.entry_net("2001:db8::1") is None and podkop.entry_net(None) is None


def _row(n, ip, verdict="GOOD", key=1):
    return {"link": f"vless://u@h{n}:443#s{n}", "verdict": verdict, "median": 0.1 + n / 1000, "p90": 0.2, "mbit": 50,
            "country": "DE", "entry_ip": ip, "key": key}


def test_picks_spread_across_networks_but_never_take_flaky_first():
    rows = [_row(i, f"62.144.49.{i}") for i in range(1, 8)] + [_row(10 + i, f"10.0.{i}.1", "OK") for i in range(3)]
    rows.append(_row(20, "10.9.9.9", "FLAKY"))
    pick = podkop.auto_pick(rows, podkop.check_auto_rule({"top": 5, "fallback": 2}))
    nets = [podkop.entry_net(x["entry_ip"]) for x in pick["matched"]]
    assert nets.count("62.144.49.0/24") == 2 and len(pick["links"]) == 5
    assert all(x["verdict"] != "FLAKY" for x in pick["matched"] + pick["fallback_rows"])
    # unknown networks: the order is the old one (reliability, then key diversity)
    plain = podkop.auto_pick([dict(r, entry_ip=None) for r in rows], podkop.check_auto_rule({"top": 5, "fallback": 2}))
    assert [x["verdict"] for x in plain["matched"]] == ["GOOD"] * 5


class FakeAgent:
    def __init__(self, cfg):
        self.cfg, self.calls = cfg, []

    async def call(self, name, *args, **kwargs):
        self.calls.append((name, kwargs.get("payload")))
        if name == "config-get":
            return {"config": self.cfg, "fingerprint": "fp"}
        if name == "resolve":
            return {h: "62.144.49.1" for h in kwargs["payload"]["hosts"]}
        return {}


def _hub(tmp_path, cfg):
    db = DB(tmp_path / "manager.db")
    rid = db.add_router("reserv", "router", 22, "ssh-ed25519 AAAA")
    hub = Hub(db, tmp_path / "key", Path(__file__).resolve().parents[1])
    rt = RouterRuntime(db.router(rid), tmp_path / "key")
    rt.agent = FakeAgent(cfg)
    hub.routers[rid] = rt
    return db, hub, rt, rid


def _cfg(links):
    return {"sections": [{"name": "settings", "type": "settings", "anonymous": False, "options": {}},
                         {"name": "main", "type": "section", "anonymous": False, "options": {
                             "connection_type": "proxy", "proxy_config_type": "urltest", "urltest_proxy_links": links}}]}


def test_group_down_lends_earlier_servers_and_runs_one_probe(tmp_path):
    now = _cfg(["vless://u@dead1:443#a", "vless://u@dead2:443#b"])
    db, hub, rt, rid = _hub(tmp_path, now)
    for links in (["vless://u@old1:443#x", "vless://u@dead1:443#a"], ["vless://u@old2:443#y"]):
        vid = db.add_version(rid, _cfg(links), [], "", "fp")
        db.update_version(vid, state="applied")
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 5}))
    probes = []

    async def start_probe(r, trigger="manual"):
        probes.append(trigger)
        return 1
    hub.start_probe = start_probe
    asyncio.run(hub.heal(rt, {"section": "main", "borrowed": []}))
    name, payload = rt.agent.calls[-1]
    assert name == "heal-links" and payload["fingerprint"] == "fp"
    assert payload["links"] == ["vless://u@old2:443#y", "vless://u@old1:443#x"]    # newest first, none it has now
    assert probes == ["heal"]
    rt.agent.calls.clear()
    asyncio.run(hub.heal(rt, {"section": "main", "borrowed": ["youtube-1-out"]}))
    assert rt.agent.calls == [] and probes == ["heal"]        # borrowed on the router; probe only every 30 min


def test_heal_probe_result_is_applied_with_entry_networks(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@dead1:443#a"]))
    applied = []

    async def apply_probe(r, st, note, sections=None):
        applied.append((note, st))
        return 7
    hub.apply_probe = apply_probe
    run = db.add_probe_run(rid, "heal")
    st = {"state": "done", "rows": [{"link": "vless://u@new1:443#n", "verdict": "GOOD"}], "log": []}
    asyncio.run(hub._finish_probe(rt, run, "heal", st))
    assert applied[0][0] == "self-heal probe" and applied[0][1]["rows"][0]["entry_ip"] == "62.144.49.1"
    run = db.add_probe_run(rid, "heal")
    asyncio.run(hub._finish_probe(rt, run, "heal", {"state": "failed", "log": ["error: no connection"]}))
    assert db.events(rid)[0]["type"] == "heal_probe_failed"


def test_heal_probe_is_quick_and_only_for_the_dead_rules(tmp_path):
    cfg = _cfg(["vless://u@dead1:443#a"])
    cfg["sections"].append({"name": "youtube", "type": "section", "anonymous": False, "options": {
        "connection_type": "proxy", "proxy_config_type": "urltest", "urltest_proxy_links": ["vless://u@yt1:443#y"]}})
    db, hub, rt, rid = _hub(tmp_path, cfg)
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 5, "countries": ["DE"]}))
    db.set_auto_rule(rid, "youtube", podkop.check_auto_rule({"top": 5, "countries": ["NL"], "youtube": "plays"}))
    db.set_probe_settings(rid, keys=["https://sub.example/k"])
    asyncio.run(hub.heal(rt, {"section": "main", "borrowed": ["youtube-1-out"]}))
    opts = next(payload["options"] for name, payload in rt.agent.calls if name == "probe-start")
    assert "-q" in opts and "--no-speed" in opts and "--no-youtube" in opts
    assert opts[opts.index("--countries") + 1] == "DE"                   # not youtube's NL
    assert rt.heal_sections == {"main"}
    # the result changes main only; youtube keeps its servers
    st = {"state": "done", "rows": [{"link": "vless://u@new1:443#n", "verdict": "GOOD", "country": "DE", "median": 0.1}], "log": []}
    new = hub.probe_config(rid, {"config": cfg, "fingerprint": "fp"}, st, sections={"main"})
    by = {s["name"]: s for s in new["sections"]}
    assert by["main"]["options"]["urltest_proxy_links"] == ["vless://u@new1:443#n"]
    assert by["youtube"]["options"]["urltest_proxy_links"] == ["vless://u@yt1:443#y"]
    applied = []

    async def apply_probe(r, st, note, sections=None):
        applied.append(sections)
        return 7
    hub.apply_probe = apply_probe
    # youtube went down while the probe for main ran: the result is applied to main only, and
    # youtube (its countries were not tested) gets a probe of its own
    rt.heal_sections.add("youtube")
    started = []

    async def start_probe(r, trigger="manual"):
        started.append((trigger, set(rt.heal_sections)))
        return 2
    real_start, hub.start_probe = hub.start_probe, start_probe
    run = db.add_probe_run(rid, "heal", sections=["main"])
    assert db.one("SELECT sections, ran_on FROM probe_runs WHERE id = ?", run) == {"sections": '["main"]', "ran_on": "router"}
    asyncio.run(hub._finish_probe(rt, run, "heal", st, {"main"}))
    assert applied == [{"main"}] and started == [("heal", {"youtube"})]
    hub.start_probe = real_start
    # a result the manager cannot read ends the run as failed, with an event, not "running" for ever
    async def boom(*a, **k):
        raise KeyError("rows")
    hub.apply_probe = boom
    bad = db.add_probe_run(rid, "heal", sections=["youtube"])
    asyncio.run(hub._finish_probe(rt, bad, "heal", st, {"youtube"}))
    assert db.one("SELECT state FROM probe_runs WHERE id = ?", bad)["state"] == "failed" and rt.heal_sections == set()
    assert db.q("SELECT type FROM events WHERE router_id = ? ORDER BY id DESC LIMIT 1", rid)[0]["type"] == "probe_failed"
    # a self-heal run is never the "last probe" other rules take their servers from
    full = db.add_probe_run(rid, "manual")
    db.finish_probe_run(full, "done", {"state": "done", "rows": [{"link": "vless://u@full:443#f"}]})
    heal = db.add_probe_run(rid, "heal")
    db.finish_probe_run(heal, "done", st)
    assert db.last_probe_result(rid)["rows"][0]["link"] == "vless://u@full:443#f"


def test_loaded_config_keeps_its_servers_when_the_probe_is_old(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    run = db.add_probe_run(rid, "manual")
    db.finish_probe_run(run, "done", {"state": "done", "rows": [
        {"link": "vless://u@stale:443#s", "verdict": "GOOD", "country": "DE", "median": "0.1", "mbit": "50"}]})
    saved = {"config": _cfg(["vless://u@file:443#f"]), "rules": {"main": podkop.check_auto_rule({"top": 1})}}
    db.x("UPDATE probe_runs SET finished = ? WHERE id = ?", time.time() - 2 * 86400, run)
    out = hub.load_config(rid, saved)
    assert podkop.sections_by_name(out)["main"]["options"]["urltest_proxy_links"] == ["vless://u@file:443#f"]
    db.x("UPDATE probe_runs SET finished = ? WHERE id = ?", time.time(), run)
    out = hub.load_config(rid, saved)
    assert podkop.sections_by_name(out)["main"]["options"]["urltest_proxy_links"] == ["vless://u@stale:443#s"]


def test_a_reported_group_reports_its_recovery_after_an_apply(tmp_path):
    r = Router(tmp_path)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/heal-off").write_text("youtube\nanthropic_am\n")
    r.check(); r.now(1030); r.check()
    assert [t for t, _ in r.events()] == ["group_down"]
    (tmp_path / "fp").write_text("fp2")         # the probe's servers applied
    r.now(1060); r.check()                      # still down: a new count, no second report yet
    assert [t for t, _ in r.events()] == ["group_down"] and r.state()["groups"]["main"]["alerted"]
    r.alive(["main-1-out"])
    for t in (1090, 1120, 1150):                # nothing borrowed: up after 4 good checks
        r.now(t); r.check()
    assert [t for t, _ in r.events()] == ["group_down"]
    r.now(1180); r.check()
    assert [t for t, _ in r.events()] == ["group_down", "group_recovered"] and r.state()["groups"] == {}


def test_alert_clears_when_the_router_has_no_group_down(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    rt.alert = {"type": "group_down", "t": time.time() - 120}
    rt.status = {"heal": {"off": [], "groups": {"main": {"dead_since": 1, "reported": True}}}}
    hub._clear_heal_alert(rt)
    assert rt.alert
    rt.status = {"heal": {"off": [], "groups": {}}}
    hub._clear_heal_alert(rt)
    assert rt.alert is None


def test_resolved_addresses_are_kept_for_later_picks(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 2}))
    rows = [{"link": f"vless://u@h{i}:443#s{i}", "verdict": "GOOD", "median": "0.1", "mbit": "50", "country": "DE"}
            for i in range(3)]
    asyncio.run(hub._entry_ips(rt, {"rows": [dict(r) for r in rows]}))
    pick = hub.auto_picks(rid, {"rows": rows})["main"]      # rows read again, without addresses
    assert all(x["entry_ip"] == "62.144.49.1" for x in pick["matched"])


def test_lending_waits_while_the_router_is_busy(tmp_path, monkeypatch):
    import podkop_manager.hub as hubmod
    from podkop_manager.agent import AgentError
    monkeypatch.setattr(hubmod, "HEAL_RETRY_SECONDS", 0)
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@dead:443#a"]))
    vid = db.add_version(rid, _cfg(["vless://u@old:443#x"]), [], "", "fp")
    db.update_version(vid, state="applied")
    tries = []
    call = rt.agent.call

    async def busy_once(name, *args, **kwargs):
        if name == "heal-links":
            tries.append(1)
            if len(tries) == 1:
                raise AgentError("busy", "an apply is in progress")
        return await call(name, *args, **kwargs)
    rt.agent.call = busy_once
    asyncio.run(hub.heal(rt, {"section": "main", "borrowed": []}))
    assert len(tries) == 2 and not [e for e in db.events(rid) if e["type"] == "heal_links_failed"]


def test_heal_probe_widens_to_fallback_countries_only_when_nothing_in_its_own_works(tmp_path):
    cfg = _cfg(["vless://u@dead1:443#a"])
    db, hub, rt, rid = _hub(tmp_path, cfg)
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 2, "countries": ["BG", "LT"], "heal_fallback": "europe"}))
    assert hub.probe_options(rid)[1] == "BG,LT"                                  # the nightly run keeps to the rule
    heal = hub.probe_options(rid, {"main"}, heal=True)[1].split(",")
    assert "BG" in heal and "DE" in heal and "NL" in heal                       # self-heal tests Europe too
    rows = [{"link": "vless://u@de1:443#d", "verdict": "GOOD", "country": "DE", "median": 0.1, "mbit": 50},
            {"link": "vless://u@lt1:443#l", "verdict": "GOOD", "country": "LT", "median": 0.2, "mbit": 50}]
    st = {"state": "done", "rows": rows}
    picks = hub.auto_picks(rid, st, widen={"main"})
    assert [x["country"] for x in picks["main"]["matched"]] == ["LT"] and not picks["main"].get("widened")   # its own first
    picks = hub.auto_picks(rid, {"state": "done", "rows": rows[:1]}, widen={"main"})
    assert [x["country"] for x in picks["main"]["matched"]] == ["DE"] and picks["main"]["widened"]
    assert hub.auto_picks(rid, {"state": "done", "rows": rows[:1]})["main"]["links"] == []    # not for the nightly picks
    db.set_auto_rule(rid, "main", podkop.check_auto_rule({"top": 2, "countries": ["BG"], "heal_fallback": "any"}))
    assert hub.probe_options(rid, {"main"}, heal=True)[1] == "all"
    assert podkop.widened(podkop.check_auto_rule({"countries": []})) is None


def test_low_memory_router_tests_servers_on_the_manager(tmp_path, monkeypatch):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    db.set_probe_settings(rid, keys=["https://sub.example/k"])
    rt.status = {"agent": {"version": "0.15.0"}, "router": {"mem_free": 40}}
    hub.local_probe_missing = lambda: ""
    ran = []

    async def local(rt_, run_id, trigger, options, sections=None):
        ran.append(("local", trigger))
    hub._run_local_probe = local
    asyncio.run(hub.start_probe(rid, "manual"))
    assert ran == [("local", "manual")] and not [c for c in rt.agent.calls if c[0] == "probe-start"]
    assert db.events(rid)[0]["type"] == "probe_on_manager"
    hub.probes.clear()
    rt.status["router"]["mem_free"] = 120
    asyncio.run(hub.start_probe(rid, "manual"))
    assert [c[0] for c in rt.agent.calls if c[0] == "probe-start"] == ["probe-start"]    # enough memory: on the router


def test_failed_list_downloads_are_observed():
    from podkop_manager import reliability as r
    line = "Sun Nov  9 10:00:00 2026 daemon.err sing-box[1]: ERROR[72000] router: fetch rule-set main-hetzner-community-ruleset: unexpected status: 504 Gateway Timeout"
    o = r.log_observation(line)
    assert o["kind"] == "lists" and o["scope"] == "main-hetzner-community-ruleset"
    assert r.classify("dial tcp: i/o timeout") == "transport"


def test_notifications_text_and_channels(tmp_path, monkeypatch):
    from podkop_manager import notify
    assert notify.text("home", "group_down", {"section": "main", "borrowed": []}) == "home: main: no server answers (nothing to borrow)"
    assert notify.text("home", "strict_closed", {"section": "meta"}) == "home: meta: kill switch closed, connections are refused"
    assert notify.text("home", "applied", {}) is None           # not worth a message
    assert not notify.configured({"telegram_token": "t", "telegram_chat": ""}) and notify.configured({"webhook_url": "http://x"})
    sent = []

    class Answer:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def urlopen(req, data=None, timeout=None):
        sent.append((req if isinstance(req, str) else req.full_url, data if data is not None else req.data))
        return Answer()
    monkeypatch.setattr(notify.urllib.request, "urlopen", urlopen)
    assert notify.send({"telegram_token": "T", "telegram_chat": "42", "webhook_url": "http://hook/x"}, "hello", {"event": "e"}) == []
    assert sent[0][0] == "https://api.telegram.org/botT/sendMessage" and b"chat_id=42" in sent[0][1] and b"text=hello" in sent[0][1]
    assert sent[1][0] == "http://hook/x" and json.loads(sent[1][1]) == {"text": "hello", "event": "e"}
    # the hub sends router events and its own through it
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))
    db.set_setting("notify", {"webhook_url": "http://hook/x"})
    sent.clear()

    async def go():
        hub._on_message(rt, {"type": "strict_closed", "id": "b:1", "t": "now", "section": "main"})
        hub._on_message(rt, {"type": "applied", "id": "b:2", "t": "now"})
        await asyncio.gather(*hub.jobs)
    asyncio.run(go())
    assert [json.loads(d)["text"] for _, d in sent] == ["reserv: main: kill switch closed, connections are refused"]


def test_a_change_nobody_watches_that_is_not_applied_raises_an_alert(tmp_path):
    db, hub, rt, rid = _hub(tmp_path, _cfg(["vless://u@a:443#a"]))

    async def call(name, *args, **kw):
        return {"state": "rejected", "message": "podkop's settings changed"}
    rt.agent.call = call
    vid = db.add_version(rid, _cfg(["vless://u@b:443#b"]), [], "nightly probe", "fp", "probe")
    asyncio.run(hub._track_job(rt, vid, "j1"))
    assert rt.alert["type"] == "change_not_applied"
    web = db.add_version(rid, _cfg(["vless://u@c:443#c"]), [], "", "fp", "web")     # the apply dialog shows this one
    rt.alert = None
    asyncio.run(hub._track_job(rt, web, "j2"))
    assert rt.alert is None
