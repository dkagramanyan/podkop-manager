"""podkop-manager command line:

  podkop-manager serve [--listen 0.0.0.0:8080] [--data /var/lib/podkop-manager]
  podkop-manager public-key [--data DIR]

Credentials come from the environment (.env with Docker Compose):
  PODKOP_MANAGER_USER             the web login's name (default admin)
  PODKOP_MANAGER_PASSWORD         its password, at least 8 characters
  PODKOP_MANAGER_HTTPS=1          the login cookie goes over HTTPS only (behind a TLS proxy)
  PODKOP_MANAGER_HEARTBEAT_URL    requested every minute while the manager works (an uptime monitor's check-in URL)
  PODKOP_MANAGER_WATCHDOG=0       do not end the process when it hangs (default: it ends, Docker starts it again)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import asyncssh

from .db import DB


def data_dir(arg: str | None) -> Path:
    d = Path(arg or os.environ.get("PODKOP_MANAGER_DATA") or "/var/lib/podkop-manager")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def ssh_key(d: Path, name: str = "id_ed25519", comment: str = "podkop-manager") -> tuple[Path, str]:
    """The manager's own SSH key; routers allow it to run only podkop-agent.
    (id_forward: the key routers may allow to forward connections, nothing else.)"""
    path = d / name
    if not path.exists():
        key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
        key.write_private_key(str(path))
        os.chmod(path, 0o600)
    key = asyncssh.read_private_key(str(path))
    return path, key.export_public_key().decode().strip()


def source_dir() -> Path:
    """Where router/ (podkop-agent, install.sh, probe.sh) is: the repository
    this package lives in, or PODKOP_MANAGER_SRC."""
    env = os.environ.get("PODKOP_MANAGER_SRC")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="podkop-manager")
    p.add_argument("command", choices=["serve", "public-key"])
    p.add_argument("--data", help="data directory (default /var/lib/podkop-manager)")
    p.add_argument("--listen", default=os.environ.get("PODKOP_MANAGER_LISTEN", "0.0.0.0:8080"))
    a = p.parse_args(argv)
    d = data_dir(a.data)
    db = DB(d / "manager.db")

    key_path, public_key = ssh_key(d)
    ssh_key(d, "id_forward", "podkop-manager-forward")
    if a.command == "public-key":
        print(public_key)
        return 0

    import uvicorn

    from .agent import AGENT_FILES
    from .hub import Hub
    from .selfwatch import STALL_SECONDS, SelfWatch
    from .web import create_app

    src = source_dir()
    missing = [f for f in AGENT_FILES if not (src / f).exists()]
    if missing:
        print(f"cannot find {', '.join(missing)} in {src} (set PODKOP_MANAGER_SRC)", file=sys.stderr)
        return 1
    password = os.environ.get("PODKOP_MANAGER_PASSWORD", "")
    if len(password) < 8:
        print("set PODKOP_MANAGER_PASSWORD (at least 8 characters), e.g. in .env", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
    host, _, port = a.listen.rpartition(":")
    hub = Hub(db, key_path, src)
    hub.auto_engine = True       # podkop-engine for testing servers here, fetched in the background
    hub.selfwatch = SelfWatch(d / "alive", os.environ.get("PODKOP_MANAGER_HEARTBEAT_URL", "").strip(),
                              0 if os.environ.get("PODKOP_MANAGER_WATCHDOG", "") == "0" else STALL_SECONDS)
    app = create_app(db, hub, public_key, password,
                     os.environ.get("PODKOP_MANAGER_USER", "").strip() or "admin",
                     secure_cookies=os.environ.get("PODKOP_MANAGER_HTTPS", "") == "1")
    uvicorn.run(app, host=host or "0.0.0.0", port=int(port), log_level="warning", proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
