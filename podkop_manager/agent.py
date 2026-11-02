"""Talking to podkop-agent on a router over SSH.

One SSH connection per router; every command runs on its own channel.
The manager's key may run only podkop-agent there, so the command line is
just "podkop-agent <command> <args>" and requests go as JSON on stdin.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path
from typing import AsyncIterator

import asyncssh

PROTOCOL = 1


class AgentError(Exception):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def known_hosts_line(host: str, port: int, host_key: str) -> str:
    target = host if port == 22 else f"[{host}]:{port}"
    return f"{target} {host_key}\n"


# the line for the router's /etc/dropbear/authorized_keys that lets the
# manager's forwarding key open connections through the router and nothing
# else: no shell, no command (it ends at once), no terminal, no agent or X11
FORWARD_OPTIONS = 'command="/bin/false",no-agent-forwarding,no-X11-forwarding,no-pty'


def forward_line(public_key: str) -> str:
    return f"{FORWARD_OPTIONS} {public_key}"


async def check_forward(host: str, port: int, host_key: str, key: Path) -> str:
    """Logs in with the forwarding key and opens a connection through the
    router, as probes run on the manager do. Returns a short "ok" text."""
    async def go():
        async with asyncssh.connect(
                host, port=port, username="root", client_keys=[str(key)], agent_path=None, password=None,
                known_hosts=asyncssh.import_known_hosts(known_hosts_line(host, port, host_key)),
                preferred_auth="publickey") as conn:
            _, w = await conn.open_connection("ipinfo.io", 443)
            w.close()
    try:
        await asyncio.wait_for(go(), 20)
    except asyncssh.PermissionDenied:
        raise AgentError("forward_key", "the router does not accept the forwarding key: add its line "
                         "to /etc/dropbear/authorized_keys") from None
    except asyncssh.ChannelOpenError as e:
        raise AgentError("forward_denied", f"the router refuses to forward connections: {e.reason}") from None
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        raise AgentError("unreachable", f"cannot connect to {host}: {e or type(e).__name__}") from None
    return "the router forwards connections for this server"


class Agent:
    def __init__(self, host: str, port: int, host_key: str, client_key: Path):
        self.host, self.port, self.host_key, self.client_key = host, port, host_key, client_key
        self._conn: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    async def connection(self) -> asyncssh.SSHClientConnection:
        async with self._lock:
            if self._conn is not None and not self._conn.is_closed():
                return self._conn
            try:
                self._conn = await asyncio.wait_for(asyncssh.connect(
                    self.host, port=self.port, username="root",
                    client_keys=[str(self.client_key)], agent_path=None, password=None,
                    known_hosts=asyncssh.import_known_hosts(known_hosts_line(self.host, self.port, self.host_key)),
                    keepalive_interval=20, keepalive_count_max=3), timeout=15)
            except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
                raise AgentError("unreachable", f"cannot connect to {self.host}: {e or type(e).__name__}") from None
            return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def call(self, *args: str, payload: dict | None = None, timeout: float = 60):
        """Run one command; returns its data or raises AgentError."""
        conn = await self.connection()
        cmd = "podkop-agent " + " ".join(shlex.quote(a) for a in args)
        proc = None
        try:
            proc = await conn.create_process(cmd)
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(payload) if payload is not None else ""), timeout=timeout)
        except asyncio.TimeoutError:
            raise AgentError("timeout", f"{args[0]} took longer than {timeout:.0f} s") from None
        except (OSError, asyncssh.Error) as e:
            self.close()
            raise AgentError("unreachable", f"connection to {self.host} lost: {e}") from None
        finally:
            if proc is not None:
                proc.close()        # a timed-out command must not keep its channel (and process) open
        out = (stdout or "").strip().splitlines()
        try:
            msg = json.loads(out[-1]) if out else None
        except json.JSONDecodeError:
            msg = None
        if not isinstance(msg, dict) or "ok" not in msg:
            raise AgentError("bad_answer", f"unexpected answer from podkop-agent: {(stdout or stderr or '')[:200]}")
        if not msg["ok"]:
            e = msg.get("error") or {}
            raise AgentError(e.get("code", "error"), e.get("message", "error"), e.get("details"))
        return msg.get("data")

    async def watch(self, since: str = "") -> AsyncIterator[dict]:
        """Events and log lines as they come; ends when the connection drops."""
        conn = await self.connection()
        proc = await conn.create_process("podkop-agent watch " + shlex.quote(since))
        try:
            async for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        finally:
            proc.stdin.write_eof()
            proc.close()


AGENT_FILES = ("router/podkop-agent", "router/podkop-agent.init", "router/install.sh", "router/probe.sh", "router/reliability.sh")


REPO_URL = "https://github.com/dkagramanyan/podkop-manager"


def connection_support(info: dict | None) -> dict:
    """Older agents have no capability list; diagnostics first shipped in 0.8.0."""
    info = info or {}
    version = info.get("version") or ""
    parts = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    supported = None
    if "capabilities" in info:
        supported = all(c in (info["capabilities"] or []) for c in ("diagnostics", "eligibility"))
    elif parts:
        supported = tuple(map(int, parts.groups())) >= (0, 8, 0)
    if supported is False:
        message = (f"Router agent {version or '(unknown version)'} does not support connection health. "
                   "Update the router agent using Update agent; updating Manager alone does not update the router.")
    elif supported is None:
        message = "Router agent compatibility has not been checked. Refresh the router to check its version."
    else:
        message = ""
    return {"supported": supported, "version": version, "message": message}


async def require_connection_support(rt) -> None:
    # Read the executable's version each time, so an upgrade is recognised
    # immediately, without reconnecting SSH or restarting Manager.
    info = await rt.agent.call("version", timeout=15)
    rt.status = {**(rt.status or {}), "agent": info}
    support = connection_support(info)
    if support["supported"] is not True:
        raise AgentError("agent_upgrade", support["message"], support)


def upgrade_command(tag: str) -> str:
    """Update an already paired router without replacing keys or firewall rules."""
    base = f"{REPO_URL}/releases/download/{tag}" if tag != "latest" else f"{REPO_URL}/releases/latest/download"
    release = f" --release {shlex.quote(tag)}" if tag != "latest" else ""
    return (f"wget -qO /tmp/podkop-install.sh {shlex.quote(base + '/install.sh')} && "
            f"sh /tmp/podkop-install.sh --upgrade{release}")


def release_tag(src: Path) -> str:
    """The release with this manager's router files: podkop-probe's VERSION."""
    m = re.search(r'^VERSION="([^"]+)"', (src / "router" / "probe.sh").read_text(), re.M)
    return f"v{m.group(1)}" if m else "latest"


def install_command(tag: str, public_key: str, forward_key: str = "", allow_clash: list[str] = (),
                    close_clash: bool = True) -> str:
    """The command to run on the router (as root): installs podkop-agent and
    podkop-probe of this release and the manager's key (and, if given, the
    forwarding-only key), then prints the line for Add router."""
    url = f"{REPO_URL}/releases/download/{tag}/install.sh"
    cmd = f"wget -qO /tmp/podkop-install.sh {url} && sh /tmp/podkop-install.sh --release {tag} --key {shlex.quote(public_key)}"
    if forward_key:
        cmd += f" --forward-key {shlex.quote(forward_key)}"
    if not close_clash:
        cmd += " --keep-clash-open"
    for ip in allow_clash:
        cmd += f" --allow-clash {shlex.quote(ip)}"
    return cmd


HOST_KEY_RE = re.compile(r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+) [A-Za-z0-9+/]+=*")


def parse_pairing(text: str) -> dict:
    """"podkop-router <address> <port> <host key type> <host key>", printed by
    install.sh -> {host, port, host_key}. Raises ValueError."""
    f = text.split()
    if len(f) != 5 or f[0] != "podkop-router" or not f[2].isdigit() or not HOST_KEY_RE.fullmatch(f"{f[3]} {f[4]}"):
        raise ValueError("paste the whole line the command printed: podkop-router <address> <port> <key>")
    return {"host": f[1], "port": int(f[2]), "host_key": f"{f[3]} {f[4]}"}


async def pair(host: str, port: int, host_key: str, key: Path) -> dict:
    """The router has this host key and lets the manager's key run
    podkop-agent: returns its version answer."""
    async def go():
        async with asyncssh.connect(
                host, port=port, username="root", client_keys=[str(key)], agent_path=None, password=None,
                known_hosts=asyncssh.import_known_hosts(known_hosts_line(host, port, host_key)),
                preferred_auth="publickey") as conn:
            r = await conn.run("podkop-agent version", check=False)
            return r.stdout or ""
    try:
        out = await asyncio.wait_for(go(), 20)
    except asyncssh.PermissionDenied:
        raise AgentError("auth", "the router does not accept this manager's key: run the command on it first") from None
    except asyncssh.HostKeyNotVerifiable:
        raise AgentError("host_key", f"{host} has another SSH host key than the pasted line: is it the right address?") from None
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        raise AgentError("unreachable", f"cannot connect to {host}: {e or type(e).__name__}") from None
    try:
        msg = json.loads(out.strip().splitlines()[-1])
        assert msg["ok"]
        return msg["data"]
    except (ValueError, IndexError, KeyError, TypeError, AssertionError):
        raise AgentError("no_agent", "podkop-agent does not answer on the router: run the command on it again") from None
