# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Project canonical session activity timestamps onto session directory entries."""

import asyncio
import json
from typing import Any, Dict, Iterable, List

from openviking.core.namespace import canonical_session_uri
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import VikingFS
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

SESSION_LIST_LIMIT = 1000
_META_READ_CONCURRENCY = 16


def is_session_root_uri(uri: str, ctx: RequestContext) -> bool:
    """Return whether *uri* is a canonical or legacy session collection root."""
    normalized = uri.rstrip("/")
    return normalized in {canonical_session_uri(ctx), "viking://session"}


def session_activity_value(entry: Dict[str, Any]) -> str:
    """Return the logical activity timestamp, falling back to filesystem mtime."""
    for key in ("activityTime", "modTime", "mod_time"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def session_activity_sort_key(entry: Dict[str, Any]) -> tuple[bool, float]:
    """Build a key that keeps missing/invalid timestamps below valid ones."""
    value = session_activity_value(entry)
    if value:
        try:
            return True, parse_iso_datetime(value).timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    return False, 0.0


def sort_session_entries_by_activity(
    entries: Iterable[Dict[str, Any]], *, descending: bool = True
) -> List[Dict[str, Any]]:
    """Sort session entries by logical activity while preserving stable ties."""
    return sorted(entries, key=session_activity_sort_key, reverse=descending)


async def project_session_activity(
    entries: Iterable[Dict[str, Any]],
    *,
    root_uri: str,
    viking_fs: VikingFS,
    ctx: RequestContext,
) -> List[Dict[str, Any]]:
    """Attach ``activityTime`` using ``.meta.json.updated_at`` when available.

    Per-entry failures deliberately fall back to the directory's real mtime so
    a corrupt or legacy metadata file cannot make session listing fail.
    """
    semaphore = asyncio.Semaphore(_META_READ_CONCURRENCY)

    async def project(entry: Dict[str, Any]) -> Dict[str, Any]:
        projected = dict(entry)
        fallback = session_activity_value(projected)
        name = str(projected.get("name") or "")
        if not projected.get("isDir") or name in {"", ".", ".."}:
            projected["activityTime"] = fallback
            return projected

        entry_uri = str(projected.get("uri") or f"{root_uri.rstrip('/')}/{name}")
        projected["uri"] = entry_uri
        try:
            async with semaphore:
                raw = await viking_fs.read_file(f"{entry_uri}/.meta.json", ctx=ctx)
            payload = json.loads(raw or "")
            updated_at = payload.get("updated_at") if isinstance(payload, dict) else None
            if isinstance(updated_at, str) and updated_at:
                parse_iso_datetime(updated_at)
                projected["activityTime"] = updated_at
                return projected
        except Exception:
            logger.debug("Failed to read session activity for %s", entry_uri, exc_info=True)

        projected["activityTime"] = fallback
        return projected

    return list(await asyncio.gather(*(project(entry) for entry in entries)))
