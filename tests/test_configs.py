import json
from pathlib import Path

import asyncssh
import pytest

from podkop_manager import backup, configs, podkop
from podkop_manager.db import DB

UCI = """package podkop

config settings 'settings'
	option dns_type 'doh'
	list source_network_interfaces 'br-lan'
	option shutdown_correctly '0'

# a comment
config section 'main'
	option connection_type 'proxy'
	option user_domain_list_type 'text'
	option user_domains_text 'anthropic.com
it'\\''s.example.org'
	list community_lists 'russia_inside'
	list community_lists "meta"
	list urltest_proxy_links 'vless://a@h:1?x=1#A B'
"""


def test_parse_uci():
    cfg = configs.check_config(configs.parse_uci(UCI))
    settings, main = cfg["sections"]
    assert settings == {"name": "settings", "type": "settings", "anonymous": False,
                        "options": {"dns_type": "doh", "source_network_interfaces": ["br-lan"]}}   # internal dropped
    o = main["options"]
    assert o["user_domains_text"] == "anthropic.com\nit's.example.org"
    assert o["community_lists"] == ["russia_inside", "meta"] and o["urltest_proxy_links"] == ["vless://a@h:1?x=1#A B"]


@pytest.mark.parametrize("text, msg", [
    ("package network\nconfig interface 'lan'\n", "not podkop"),
    ("config section 'main'\n\toption a 'b\n", "quote"),
    ("hello world\n", "not UCI"),
    ("config section 'main'\n", "no settings"),
    ("{\"format\": \"other\"}", "not a podkop-manager"),
])
def test_bad_files(text, msg):
    with pytest.raises(ValueError, match=msg):
        configs.read(text)


def test_export_and_import_keep_rules():
    cfg = configs.check_config(configs.parse_uci(UCI))
    rule = podkop.check_auto_rule({"countries": ["DE"], "top": 3})
    f = configs.read(json.dumps(configs.to_file("prod", cfg, {"main": rule}, "0.7.21")))
    assert (f["name"], f["podkop"], f["config"], f["rules"]) == ("prod", "0.7.21", cfg, {"main": rule})
    with pytest.raises(ValueError, match="rules must be"):
        configs.read(json.dumps(configs.to_file("x", cfg, {"nope": rule})))


def test_a_config_file_carries_its_rules_zapret_first():
    cfg = configs.check_config(configs.parse_uci(UCI))
    zap = {"main": {"args": ["--payload=tls_client_hello", "--lua-desync=fake:blob=fake_default_tls"],
                    "url": "https://www.instagram.com/", "name": "fake", "service": "meta"}}
    text = json.dumps(configs.to_file("prod", cfg, {}, "0.7.24", zap))
    assert configs.read(text)["zapret"] == zap
    # a file without it (older ones, podkop's own) has none; the key is left out when empty
    assert configs.read(json.dumps(configs.to_file("x", cfg, {})))["zapret"] == {}
    assert "zapret" not in configs.to_file("x", cfg, {}) and configs.read(UCI)["zapret"] == {}
    for bad, msg in ((dict(zap, nope=zap["main"]), "proxy rules of this config only, not nope"),
                     ({"main": dict(zap["main"], args=["--qnum=1 2"])}, "nfqws2 --options"),
                     ({"main": dict(zap["main"], url="ftp://x")}, "http"),
                     ({"main": dict(zap["main"], service="tiktok")}, "unknown service"),
                     ([1], "zapret must be")):
        with pytest.raises(ValueError, match=msg):
            configs.read(json.dumps(dict(configs.to_file("x", cfg, {}), zapret=bad)))


def test_backup_round_trip(tmp_path: Path):
    key = tmp_path / "id_ed25519"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(key))
    db = DB(tmp_path / "manager.db")
    db.add_router("home", "192.168.1.1", 22, "ssh-ed25519 AAAA")
    db.add_config("c", {"sections": []}, {}, "test")
    fwd = tmp_path / "id_forward"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(fwd))
    data = backup.make(db, key)
    old_key = key.read_bytes()

    other = tmp_path / "new"
    other.mkdir()
    key2 = other / "id_ed25519"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(key2))
    db2 = DB(other / "manager.db")
    db2.add_router("other", "10.0.0.1", 22, "x")
    pub = backup.restore(db2, key2, data)
    assert [r["name"] for r in db2.routers()] == ["home"] and [c["name"] for c in db2.configs()] == ["c"]
    assert key2.read_bytes() == old_key and pub.startswith("ssh-ed25519 ")
    assert (other / "id_forward").read_bytes() == fwd.read_bytes()
    with pytest.raises(ValueError, match="not a podkop-manager backup"):
        backup.restore(db2, key2, b"junk")
    assert [r["name"] for r in db2.routers()] == ["home"]


@pytest.mark.parametrize("rule", ["x", {"countries": 5}, {"protocols": [[1]]}, {"top": 1e400}])
def test_bad_rules_in_a_file_are_refused(rule):
    cfg = configs.check_config(configs.parse_uci(UCI))
    with pytest.raises(ValueError):
        configs.read(json.dumps(configs.to_file("x", cfg, {"main": rule})).replace("Infinity", "1e400"))


def test_not_a_backup(tmp_path: Path):
    import io
    import tarfile
    key = tmp_path / "id_ed25519"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(key))
    db = DB(tmp_path / "manager.db")
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in (("backup.json", b"[]"), ("manager.db", b"x"), ("id_ed25519", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="not a podkop-manager backup"):
        backup.restore(db, key, out.getvalue())
