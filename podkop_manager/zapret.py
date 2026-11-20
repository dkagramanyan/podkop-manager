"""zapret2 on the routers (agent 0.29): the release files, the strategy list,
which rule is which service, and the search that finds a working strategy
for a rule and turns zapret2 on with it.

nfqws2 comes from bol-van/zapret2's latest GitHub release, checked against
the release's sha256sum.txt and kept in the data folder; the router gets its
own architecture's binary, the Lua files and the fake files strategies name.
(zapret, the first version, is not used: it is no longer developed.)"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import tarfile
import time
import urllib.request
from pathlib import Path

DEFAULTS = json.loads(Path(__file__).with_name("zapret.json").read_text())
REPO, NAME = "bol-van/zapret2", "zapret2"
# uname -m -> the release's binaries/ folders to try, in order (mips says
# nothing of its byte order: the router's agent refuses one that doesn't run)
ARCHES = [("aarch64", ["linux-arm64"]), ("arm", ["linux-arm"]), ("mips64", ["linux-mips64"]),
          ("mipsel", ["linux-mipsel"]), ("mips", ["linux-mipsel", "linux-mips", "linux-lexra"]),
          ("x86_64", ["linux-x86_64"]), ("i386", ["linux-x86"]), ("i486", ["linux-x86"]),
          ("i586", ["linux-x86"]), ("i686", ["linux-x86"]), ("riscv64", ["linux-riscv64"]), ("ppc", ["linux-ppc"])]
LUA = ("zapret-lib", "zapret-antidpi", "zapret-auto")
FAKE = re.compile(r"(tls_clienthello_[A-Za-z0-9_.-]+|quic_initial_www_google_com|stun|zero_[0-9]+)\.bin")
ARG = re.compile(r"--[a-z0-9-]+(=[!-~]*)?")
POLL = 3.0          # seconds between looks at a running search


def arch_dirs(machine: str) -> list[str]:
    for prefix, dirs in ARCHES:
        if machine.startswith(prefix):
            return dirs
    return []


def _get(url: str, timeout: float = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "podkop-manager"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (GitHub)
        return r.read()


def release(cache: Path) -> tuple[str, Path]:
    """(tag, tarball) of the latest release, downloaded once. The tarball is
    checked against the SHA-256 GitHub gives for the asset; the release's own
    sha256sum.txt (which lists the files inside) is kept next to it for
    payload() to check nfqws2."""
    rel = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest", 30))
    tag = rel["tag_name"]
    if not re.fullmatch(r"v[0-9][0-9A-Za-z.-]*", tag):
        raise ValueError(f"unexpected release tag {tag!r}")
    file = f"{NAME}-{tag}-openwrt-embedded.tar.gz"
    dest = cache / file
    if not dest.exists():
        asset = next((a for a in rel.get("assets") or [] if a.get("name") == file), None)
        want = str((asset or {}).get("digest") or "")
        if not want.startswith("sha256:"):
            raise ValueError(f"{file}: the release gives no SHA-256 for it")
        base = f"https://github.com/{REPO}/releases/download/{tag}/"
        data = _get(base + file)
        if hashlib.sha256(data).hexdigest() != want.removeprefix("sha256:"):
            raise ValueError(f"{file}: checksum does not match the release's")
        sums = _get(base + "sha256sum.txt", 30)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{file}.sha256sum.txt").write_bytes(sums)
        tmp = dest.with_suffix(".new")
        tmp.write_bytes(data)
        tmp.replace(dest)
    return tag, dest


def payload(tag: str, path: Path, arch: str) -> dict:
    """The agent's "zapret install" input for one binaries/ folder."""
    files = {}
    sums_file = path.with_name(path.name + ".sha256sum.txt")
    sums = sums_file.read_text() if sums_file.exists() else ""
    with tarfile.open(path, "r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            rel = "/".join(parts[1:])
            if rel == f"binaries/{arch}/nfqws2":
                key = "nfqws2"
            elif len(parts) == 3 and parts[1] == "lua" and parts[2].removesuffix(".gz").removesuffix(".lua") in LUA:
                key = "lua/" + parts[2]
            elif rel.startswith("files/fake/") and FAKE.fullmatch(parts[-1]):
                key = "fake/" + parts[-1]
            else:
                continue
            data = tf.extractfile(m).read()
            if key == "nfqws2" and sums:
                want = next((ln.split()[0] for ln in sums.splitlines() if ln.split()[-1:] == [m.name]), None)
                if want != hashlib.sha256(data).hexdigest():
                    raise ValueError(f"{m.name}: not the file the release's sha256sum.txt lists")
            files[key] = base64.b64encode(data).decode()
    return {"files": files, "version": {"nfqws2": tag} if "nfqws2" in files else {}}


async def install(agent, cache: Path, machine: str) -> dict:
    """Downloads (once) and sends the router its files; tries each binaries/
    folder its CPU may use until the router can run one."""
    dirs = arch_dirs(machine)
    if not dirs:
        raise ValueError(f"zapret2 has no build for this router's CPU ({machine})")
    tag, path = await asyncio.to_thread(release, cache)
    last = None
    for d in dirs:
        body = await asyncio.to_thread(payload, tag, path, d)
        if "nfqws2" not in body["files"]:
            continue
        try:
            return await agent.call("zapret", "install", payload=body, timeout=120)
        except Exception as e:       # noqa: BLE001 - "does not run on this router": the next folder
            last = e
            if "does not run" not in str(e):
                raise
    raise last or ValueError(f"zapret2 has no build for {machine}")


# --- strategies and services --------------------------------------------------

def parse_strategies(text: str) -> list[dict]:
    """One nfqws2 strategy per line: "<name> | <options>"; # starts a
    comment. Raises ValueError naming the first bad line."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 1)]
        if len(parts) != 2:
            raise ValueError(f"line {n}: expected \"name | --options\"")
        args = parts[1].split()
        bad = next((a for a in args if not ARG.fullmatch(a)), None)
        if not args or bad:
            raise ValueError(f"line {n}: {bad or 'no options'} is not an --option")
        out.append({"name": parts[0][:120] or f"line {n}", "args": args})
    return out


def strategies_text(items: list[dict]) -> str:
    return "\n".join(f"{s['name']} | {s['args'] if isinstance(s['args'], str) else ' '.join(s['args'])}" for s in items)


def default_strategies() -> list[dict]:
    return parse_strategies(strategies_text(DEFAULTS["strategies"]))


def service_of(section: dict) -> str | None:
    """The service a rule is: its name, or its community lists when they are
    all of one service. A rule mixing lists (like main) is none."""
    name = section["name"].lower()
    lists = section.get("options", {}).get("community_lists") or []
    lists = lists if isinstance(lists, list) else [lists]
    for key, svc in DEFAULTS["services"].items():
        names = svc["names"]
        if name in names or any(name.startswith(n + "_") or name.endswith("_" + n) for n in names):
            return key
        if lists and all(x in names for x in lists):
            return key
    return None


INTERVALS = (15, 30, 60, 120, 300)


def interval(db) -> int:
    """Seconds between checks of a zapret rule's direct path: sing-box's
    URLTest and the agent's own test use the same (Settings, all routers)."""
    try:
        v = int(db.get_setting("zapret_interval") or 30)
    except (TypeError, ValueError):
        v = 30
    return v if v in INTERVALS else 30


def check_rule(name: str, r) -> dict:
    """One rule's zapret ({args, url, name, service}) as the router takes it;
    raises ValueError naming the rule."""
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", str(name)) or not isinstance(r, dict):
        raise ValueError(f"bad rule {name!r}")
    args = r.get("args")
    args = args.split() if isinstance(args, str) else args
    url = str(r.get("url") or "")
    if not isinstance(args, list) or not args or not all(isinstance(a, str) and ARG.fullmatch(a) for a in args):
        raise ValueError(f"{name}: nfqws2 --options expected")
    if not re.fullmatch(r"https?://[!-~]{1,290}", url):
        raise ValueError(f"{name}: the test address must be an http(s) URL")
    service = str(r.get("service") or "")
    if service and service not in DEFAULTS["services"] and service != "other":
        raise ValueError(f"{name}: unknown service {service!r}")
    return {"args": args, "url": url, "name": str(r.get("name") or "").replace("\n", " ")[:120], "service": service}


def check_rules(rules, config: dict) -> dict:
    """A config's zapret part: {rule: zapret} for its proxy rules only; raises ValueError."""
    if not isinstance(rules, dict) or len(rules) > 30:
        raise ValueError("zapret must be {rule: {args, url, name, service}}")
    out = {k: check_rule(k, v) for k, v in rules.items()}
    missing = sorted(set(out) - {r["name"] for r in rules_view(config)})
    if missing:
        raise ValueError(f"zapret is for proxy rules of this config only, not {', '.join(missing)}")
    return out


def rules_view(config: dict) -> list[dict]:
    """The rules zapret can serve: proxy rules, with their service."""
    out = []
    for s in config.get("sections", []):
        o = s.get("options", {})
        if s.get("type") != "section" or o.get("connection_type") != "proxy":
            continue
        key = service_of(s)
        svc = DEFAULTS["services"].get(key) or {}
        out.append({"name": s["name"], "service": key, "default": bool(svc.get("default")),
                    "urls": svc.get("urls") or [], "test": svc.get("test") or "", "note": svc.get("note") or ""})
    return out


def best(results: list[dict]) -> dict | None:
    """The fastest strategy that opened every address."""
    ok = [r for r in results if r.get("ok") and r.get("urls")]
    return min(ok, key=lambda r: sum(u.get("ms") or 0 for u in r["urls"])) if ok else None


async def search(agent, rule: str, urls: list[str], strategies: list[dict], state: dict,
                 timeout: int = 8, want: int = 3) -> dict:
    """One strategy search on the router (a job), followed until it ends;
    state["jobs"][rule] shows it to the page meanwhile."""
    out = await agent.call("zapret", "check", payload={"urls": urls, "strategies": strategies,
                                                        "timeout": timeout, "want": want}, timeout=30)
    job = out["job"]
    deadline = time.monotonic() + len(strategies) * (len(urls) * timeout + 3) + 120
    while True:
        await asyncio.sleep(POLL)
        try:
            j = await agent.call("job", job, timeout=30)
        except Exception:            # noqa: BLE001 - a dropped connection: ask again
            if time.monotonic() > deadline:
                raise
            continue
        state["jobs"][rule] = j
        if j.get("state") in ("done", "failed") or time.monotonic() > deadline:
            return j


async def auto(agent, rules: list[dict], strategies: list[dict], current: dict, state: dict) -> dict:
    """For each rule ({name, urls, test}): search, and turn zapret on with the
    fastest working strategy. Rules where nothing opened every address stay
    as they are. -> the rules to send to the router."""
    new = dict(current)
    for r in rules:
        state["now"] = r["name"]
        j = await search(agent, r["name"], r["urls"], strategies, state)
        pick = best(j.get("results") or [])
        bare = all(b.get("ok") for b in j.get("baseline") or []) and bool(j.get("baseline"))
        if j.get("state") != "done":
            state["outcome"][r["name"]] = {"on": False, "why": j.get("message") or "the search did not finish"}
        elif bare:
            state["outcome"][r["name"]] = {"on": False, "why": "its sites open without zapret on this line: nothing to fix"}
        elif not pick:
            state["outcome"][r["name"]] = {"on": False, "why": "no strategy opened every test address; the rule stays on its servers"}
        else:
            new[r["name"]] = {"args": pick["args"], "url": r["test"] or r["urls"][0], "name": pick.get("name") or "",
                              "service": r.get("service") or ""}
            state["outcome"][r["name"]] = {"on": True, "strategy": pick.get("name") or ""}
    state["now"] = None
    return new
