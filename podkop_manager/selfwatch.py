"""podkop-manager watches itself. Every alert about a router comes from this
process, so when it hangs or its server is down nobody is told. Three things:

- a stalled event loop (nothing answers, the process still runs) ends the
  process, and Docker's restart policy starts it again;
- after a start the time it was not running is known from a file touched
  every minute, and a notification says so (alerts in that time were lost);
- a heartbeat URL (PODKOP_MANAGER_HEARTBEAT_URL: healthchecks.io, Uptime
  Kuma push, ...) is requested every minute; that service raises the alarm
  when the requests stop, which also covers a server that is switched off.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("podkop_manager.selfwatch")

TICK = 60               # s between signs of life
STALL_SECONDS = 180     # the event loop gave none for this long: the process ends
DOWN_SECONDS = 300      # a gap shorter than this before a start is a normal restart


def ping(url: str) -> None:
    try:
        urllib.request.urlopen(url, timeout=10).close()  # noqa: S310 (the operator's own URL)
    except Exception as e:  # noqa: BLE001
        log.info("heartbeat not sent: %s", e)


class SelfWatch:
    def __init__(self, stamp: Path, heartbeat_url: str = "", stall: int = STALL_SECONDS):
        self.stamp, self.url, self.stall = stamp, heartbeat_url, stall
        self.seen = time.monotonic()

    def down_for(self) -> float | None:
        """Seconds without a running manager before this start (None: a first
        start, or a normal restart)."""
        try:
            gap = time.time() - self.stamp.stat().st_mtime
        except OSError:
            return None
        return gap if gap > DOWN_SECONDS else None

    async def run(self) -> None:
        if self.stall:
            threading.Thread(target=self._guard, daemon=True, name="selfwatch").start()
        while True:
            self.seen = time.monotonic()
            self.stamp.touch()
            if self.url:
                await asyncio.to_thread(ping, self.url)
            await asyncio.sleep(TICK)

    def stalled(self) -> bool:
        return time.monotonic() - self.seen > self.stall

    def _guard(self) -> None:
        while True:
            time.sleep(10)
            if self.stalled():
                log.critical("the event loop gave no sign of life for %s s: exiting, to be started again", self.stall)
                logging.shutdown()
                os._exit(70)
