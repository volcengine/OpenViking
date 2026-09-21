"""Readiness handshake with the parent OpenViking process."""

import json
import os
from pathlib import Path


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
