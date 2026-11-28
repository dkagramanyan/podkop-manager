"""Sections for the VPN server (agent 0.27): what the router learns from
sing-box's connections and what the VPN server's restricted key may ask."""
import json
import os
import subprocess
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "router/podkop-agent"


def _code():
    return subprocess.run(["sed", "-n", "/^# --- sections for the VPN server/,/^resolve_host() {/p", str(AGENT)],
                          capture_output=True, text=True, check=True).stdout.rsplit("resolve_host() {", 1)[0]


STUBS = r'''
RUN="$1"; mkdir -p "$RUN"
reply() { printf '%s\n' "$1"; exit 0; }
fail() { echo "$1: $2" >&2; exit 1; }
seen_json() { cat "$RUN/seen.json" 2>/dev/null; }
config_json() { return 1; }
nslookup() {
  case "$1" in
    www.youtube.com) printf 'Server: 127.0.0.42\nAddress: 127.0.0.42:53\n\nName: www.youtube.com\nAddress: 198.18.0.40\n' ;;
    api.github.com) printf 'Server: 127.0.0.42\nAddress: 127.0.0.42:53\n\nName: api.github.com\nAddress: 198.18.0.14\n' ;;
    *) printf 'Server: 127.0.0.42\nAddress: 127.0.0.42:53\n\nName: %s\nAddress: 93.184.215.14\n' "$1" ;;
  esac
}
nft() { cat "$RUN/nft.json" 2>/dev/null; }
'''
# the code's own sections_gen reads the router's files: here the test names the generation
GEN = '\nsections_gen() { cat "$RUN/gen" 2>/dev/null; }\n'


def _sh(tmp_path, body, stdin="", env=None):
    code = STUBS + _code() + GEN + body
    return subprocess.run(["sh", "-c", code, "sh", str(tmp_path)], input=stdin, capture_output=True, text=True,
                          timeout=30, env={**os.environ, **(env or {})})


def conn(host, chains, dst=""):
    return {"id": host + dst, "metadata": {"host": host, "destinationIP": dst, "sourceIP": "171.134.51.2"},
            "chains": chains, "upload": 1, "download": 2}


CONNS = {"connections": [
    conn("www.youtube.com", ["youtube-1-out", "youtube-urltest-out", "youtube-out"]),
    conn("api.github.com", ["github-out"]),
    conn("www.instagram.com", ["meta-1-out", "meta-out"]),
    conn("", ["meta-2-out", "meta-out"], dst="157.240.1.35"),         # subnet list: address, no name
    conn("example.org", ["direct-out"]),                                # excluded: not a section
    conn("odd.example", ["main-out"], dst="198.18.0.99"),               # FakeIP is never a real address
]}


def learn(tmp_path, conns=CONNS):
    (tmp_path / "c.json").write_text(json.dumps(conns))
    p = _sh(tmp_path, '\nsections_learn "$RUN/c.json"')
    assert p.returncode == 0, p.stderr
    return json.loads((tmp_path / "sections/map.json").read_text())


def test_learns_hosts_real_addresses_and_fakeips(tmp_path):
    m = learn(tmp_path)
    assert {h: v["s"] for h, v in m["hosts"].items()} == {
        "www.youtube.com": "youtube", "api.github.com": "github", "www.instagram.com": "meta", "odd.example": "main"}
    assert m["hosts"]["www.youtube.com"]["f"] == "198.18.0.40"
    assert m["hosts"]["odd.example"]["f"] == "-"         # podkop's DNS gave a real address: none to map
    assert {d: v["s"] for d, v in m["dsts"].items()} == {"157.240.1.35": "meta"}


def test_answers_only_what_was_asked_and_known(tmp_path):
    learn(tmp_path)
    (tmp_path / "seen.json").write_text(json.dumps({"sections": [
        {"type": "settings", "name": "settings"}, {"type": "section", "name": "youtube"},
        {"type": "section", "name": "meta"}]}))
    rq = {"domains": ["www.youtube.com", "unknown.example"],
          "addresses": ["198.18.0.40", "157.240.1.35", "198.18.0.77", "1.1.1.1"]}
    p = _sh(tmp_path, "\ncmd_sections", json.dumps(rq))
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout)
    assert d["sections"] == ["youtube", "meta"]
    assert d["domains"] == {"www.youtube.com": "youtube"}
    assert d["addresses"] == {"198.18.0.40": {"section": "youtube", "host": "www.youtube.com"},
                              "157.240.1.35": {"section": "meta", "host": ""}}
    assert "subnets" not in d and len(d["boot_id"]) >= 8
    # the rest of the household's names never leave the router
    assert "api.github.com" not in p.stdout


def test_subnets_as_prefixes_ranges_and_single_addresses(tmp_path):
    (tmp_path / "nft.json").write_text(json.dumps({"nftables": [{"metainfo": {}}, {"set": {
        "name": "podkop_subnets", "elem": [{"prefix": {"addr": "149.154.160.0", "len": 20}},
                                           {"range": ["91.108.4.0", "91.108.23.255"]}, "8.8.8.8"]}}]}))
    p = _sh(tmp_path, "\ncmd_sections", json.dumps({"subnets": True}))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["subnets"] == ["149.154.160.0/20", "91.108.4.0-91.108.23.255", "8.8.8.8"]


def test_new_fakeip_addresses_after_a_boot_or_a_new_cache(tmp_path):
    (tmp_path / "gen").write_text("boot1-248")
    assert learn(tmp_path)["hosts"]["www.youtube.com"]["f"] == "198.18.0.40"
    p = _sh(tmp_path, "\ncmd_sections", "{}")
    assert json.loads(p.stdout)["boot_id"] == "boot1-248"
    # sing-box restarting (no cache file for a moment) changes nothing
    (tmp_path / "gen").write_text("")
    m = learn(tmp_path, {"connections": []})
    assert m["g"] == "boot1-248" and m["hosts"]["www.youtube.com"]["f"] == "198.18.0.40"
    assert json.loads(_sh(tmp_path, "\ncmd_sections", "{}").stdout)["boot_id"] == "boot1-248"
    # a new cache: every FakeIP is asked again (here the DNS stub now gives another one)
    (tmp_path / "gen").write_text("boot1-999")
    (tmp_path / "c.json").write_text('{"connections": []}')
    p = _sh(tmp_path, '''
nslookup() { printf 'Name: %s\\nAddress: 198.18.7.7\\n' "$1"; }
sections_learn "$RUN/c.json"''')
    assert p.returncode == 0, p.stderr
    m = json.loads((tmp_path / "sections/map.json").read_text())
    assert m["g"] == "boot1-999" and {v["f"] for v in m["hosts"].values()} == {"198.18.7.7"}
    # three names on one address now: the answer names the one seen last
    (tmp_path / "c.json").write_text(json.dumps({"connections": [conn("api.github.com", ["github-out"])]}))
    assert _sh(tmp_path, '\nsleep 1; sections_learn "$RUN/c.json"').returncode == 0
    d = json.loads(_sh(tmp_path, "\ncmd_sections", json.dumps({"addresses": ["198.18.7.7"]})).stdout)
    assert d["addresses"]["198.18.7.7"] == {"section": "github", "host": "api.github.com"}


def test_bad_requests_are_refused(tmp_path):
    for rq in ('{"domains": "x"}', json.dumps({"addresses": ["1.1.1.1"] * 501}), "not json"):
        p = _sh(tmp_path, "\ncmd_sections", rq)
        assert p.returncode == 1 and "bad_request" in p.stderr


def test_map_keeps_the_newest_and_forgets_old_entries(tmp_path):
    (tmp_path / "sections").mkdir()
    old = {"hosts": {"stale.example": {"s": "main", "t": 1, "f": "-"}}, "dsts": {"9.9.9.9": {"s": "main", "t": 1}}}
    (tmp_path / "sections/map.json").write_text(json.dumps(old))
    many = {"connections": [conn(f"h{i}.example", ["main-out"]) for i in range(30)]}
    (tmp_path / "c.json").write_text(json.dumps(many))
    p = _sh(tmp_path, '\nSECTIONS_MAX=10; sections_learn "$RUN/c.json"')
    assert p.returncode == 0, p.stderr
    m = json.loads((tmp_path / "sections/map.json").read_text())
    assert len(m["hosts"]) == 10 and "stale.example" not in m["hosts"] and m["dsts"] == {}


def test_a_broken_map_starts_over(tmp_path):
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections/map.json").write_text("{trunc")
    assert learn(tmp_path)["hosts"]["www.youtube.com"]["s"] == "youtube"


def test_restricted_key_runs_only_sections(tmp_path):
    """authorized_keys: restrict,command="/usr/bin/podkop-agent --sections-only": the
    client's own command (here "apply") is ignored."""
    agent = tmp_path / "agent"
    run = tmp_path / "run"
    agent.write_text(AGENT.read_text().replace("RUN=/tmp/podkop-agent", f"RUN={run}")
                     .replace("ETC=/etc/podkop-agent", f"ETC={tmp_path / 'etc'}")
                     .replace(". /usr/share/podkop-agent/reliability.sh", "true"))
    env = {"SSH_CONNECTION": "170.134.51.30 50000 171.134.51.1 22", "SSH_ORIGINAL_COMMAND": "apply",
           "PATH": os.environ["PATH"]}
    p = subprocess.run(["sh", str(agent), "--sections-only"], input='{"domains": []}', capture_output=True,
                       text=True, timeout=30, env=env)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["ok"] and out["data"]["domains"] == {} and "boot_id" in out["data"]
    # the manager's key still reaches every command, sections included
    p = subprocess.run(["sh", str(agent)], input='{"domains": []}', capture_output=True, text=True, timeout=30,
                       env={**env, "SSH_ORIGINAL_COMMAND": "podkop-agent sections"})
    assert json.loads(p.stdout)["ok"]


def test_no_regular_expressions_in_jq():
    """OpenWrt's jq has no ONIGURUMA: test(), sub(), match() and the like fail there."""
    import re
    jq_lines = "\n".join(line for line in _code().splitlines() if "awk" not in line)
    assert not re.search(r"\b(test|match|capture|scan|sub|gsub|splits)\(", jq_lines)
