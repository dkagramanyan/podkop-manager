"""Config files: podkop's sections plus the rules that take servers from the
probe and the rules' zapret first, as a JSON file the manager exports and
imports. Import also reads podkop's own /etc/config/podkop (UCI)."""

from __future__ import annotations

import json
import re
import time

from . import zapret as zapret_mod
from .podkop import INTERNAL_OPTIONS, check_auto_rule

FORMAT = "podkop-manager-config"


def to_file(name: str, config: dict, rules: dict, podkop_version: str = "", zapret: dict | None = None) -> dict:
    """The exported file: {format, name, saved, podkop, config, rules[, zapret]}:
    zapret is {rule: {args, url, name, service}}, the rules with zapret first."""
    out = {"format": FORMAT, "version": 1, "name": name,
           "saved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "podkop": podkop_version,
           "config": config, "rules": rules}
    return out | ({"zapret": zapret} if zapret else {})


def _uci_statements(text: str):
    """UCI text -> lists of words, one list per line; quoted values may span
    lines ('a'\\''b' is a'b)."""
    words, word, has, quote, i = [], [], False, None, 0
    while i < len(text):
        ch = text[i]
        i += 1
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i < len(text):
                word.append(text[i])
                i += 1
            else:
                word.append(ch)
            continue
        if ch in "'\"":
            quote, has = ch, True
        elif ch == "\\" and i < len(text):
            word.append(text[i])
            i += 1
        elif ch == "#" and not has and not word:
            while i < len(text) and text[i] != "\n":
                i += 1
        elif ch in " \t\r\n":
            if has or word:
                words.append("".join(word))
                word, has = [], False
            if ch == "\n" and words:
                yield words
                words = []
        else:
            word.append(ch)
    if quote:
        raise ValueError("a quote is not closed")
    if has or word:
        words.append("".join(word))
    if words:
        yield words


def parse_uci(text: str) -> dict:
    """/etc/config/podkop -> {"sections": [{name, type, anonymous, options}]}."""
    sections: list[dict] = []
    for n, w in enumerate(_uci_statements(text), 1):
        if w[0] == "package":
            if len(w) > 1 and w[1] != "podkop":
                raise ValueError(f"this is the config of {w[1]}, not podkop")
        elif w[0] == "config" and len(w) in (2, 3):
            name = w[2] if len(w) == 3 else f"cfg{len(sections) + 1:02d}"
            sections.append({"name": name, "type": w[1], "anonymous": len(w) == 2, "options": {}})
        elif w[0] in ("option", "list") and len(w) == 3 and sections:
            opts = sections[-1]["options"]
            if w[0] == "option":
                opts[w[1]] = w[2]
            else:
                opts[w[1]] = (opts[w[1]] if isinstance(opts.get(w[1]), list) else []) + [w[2]]
        else:
            raise ValueError(f"line {n} is not UCI: {' '.join(w)[:60]}")
    return {"sections": sections}


def check_config(config: dict) -> dict:
    """A podkop config as the manager keeps it, without the options podkop
    writes itself; raises ValueError."""
    secs = config.get("sections") if isinstance(config, dict) else None
    if not isinstance(secs, list) or not secs:
        raise ValueError("the file has no podkop sections")
    out, names = [], set()
    for s in secs:
        if not isinstance(s, dict) or not isinstance(s.get("options"), dict) or s.get("type") not in ("settings", "section"):
            raise ValueError("podkop 0.7 has sections of type settings and section only")
        name = str(s.get("name") or "")
        if not re.fullmatch(r"[A-Za-z0-9_]+", name) or name in names:
            raise ValueError(f"bad or repeated section name {name!r}")
        names.add(name)
        opts = {}
        for k, v in s["options"].items():
            if k in INTERNAL_OPTIONS:
                continue
            if not (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))):
                raise ValueError(f"{name}.{k}: values are text or lists of text")
            opts[k] = v
        out.append({"name": name, "type": s["type"], "anonymous": bool(s.get("anonymous")), "options": opts})
    if not any(s["type"] == "settings" for s in out):
        raise ValueError("the file has no settings section")
    return {"sections": out}


def read(text: str) -> dict:
    """An imported file -> {"name", "config", "rules", "podkop", "zapret"}: our
    JSON export or podkop's UCI file. Raises ValueError."""
    text = text.lstrip("\ufeff")       # a byte order mark from some editors
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(f"not valid JSON: {e}") from None
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("not a podkop-manager config file")
        config = check_config(data.get("config"))
        rules = data.get("rules") or {}
        names = {s["name"] for s in config["sections"] if s["type"] == "section"}
        if not isinstance(rules, dict) or set(rules) - names:
            raise ValueError("rules must be for sections of this config")
        return {"name": str(data.get("name") or ""), "config": config, "podkop": str(data.get("podkop") or ""),
                "rules": {k: check_auto_rule(v) for k, v in rules.items()},
                "zapret": zapret_mod.check_rules(data.get("zapret") or {}, config)}
    return {"name": "", "config": check_config(parse_uci(text)), "rules": {}, "podkop": "", "zapret": {}}
