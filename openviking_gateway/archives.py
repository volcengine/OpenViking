# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Archive observation runs under the capture mailbox lease, outside HTTP prepare."""

import asyncio
import time

from .client import VikingError

POLL_SECONDS = 5
MAX_PENDING_SECONDS = 900
TERMINAL = {"completed", "failed", "abandoned"}


async def observe_archive(viking, archive, token):
    """Follow a commit until the server marks its archive done; the next commit waits for it."""
    now = time.time()
    if archive["status"] in TERMINAL or archive.get("next_check", 0) > now:
        return archive
    try:
        status = await asyncio.wait_for(
            viking.archive_state(token, archive["archive_uri"]),
            timeout=POLL_SECONDS,
        )
    except (VikingError, asyncio.TimeoutError):
        status = "unknown"
    if status not in TERMINAL and now - archive["created"] >= MAX_PENDING_SECONDS:
        status = "abandoned"
    return {**archive, "status": status, "next_check": now + POLL_SECONDS}
