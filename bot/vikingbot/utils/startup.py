"""Readiness handshake with the parent OpenViking process."""

import asyncio
import json
import os
from pathlib import Path
from typing import Callable, Optional


def report_startup(status: str, *, timeout: int = 600, error: str = "") -> None:
    destination = os.environ.get("VIKINGBOT_STARTUP_STATUS")
    if not destination:
        return
    path = Path(destination)
    if not path.parent.exists():
        return  # The parent removes the handshake directory after readiness.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"pid": os.getpid(), "status": status, "timeout": timeout, "error": error}),
        encoding="utf-8",
    )
    temporary.replace(path)


def lifeline_fd() -> Optional[int]:
    """Read end of a pipe whose only writer is the OpenViking server that launched us."""
    if os.name == "nt":
        # shortcut: no lifeline on Windows (the Proactor loop cannot watch pipe fds),
        # add a Job Object if orphaned gateways show up there.
        return None
    try:
        # Popped so a gateway started by our own children cannot pick up a stale fd number.
        fd = int(os.environ.pop("VIKINGBOT_LIFELINE_FD", ""))
        os.set_inheritable(fd, False)  # also rejects a closed or bogus fd
    except (ValueError, OSError):
        return None
    return fd


def watch_lifeline(fd: int, on_gone: Callable[[], None]) -> None:
    """Call ``on_gone`` once the server exits, however it dies: the kernel closes its write end."""
    loop = asyncio.get_running_loop()

    def readable() -> None:
        try:
            if os.read(fd, 4096):  # the server never writes; only EOF means it is gone
                return
        except OSError:
            pass  # an unreadable lifeline is as good as a dead server
        loop.remove_reader(fd)
        on_gone()

    loop.add_reader(fd, readable)
