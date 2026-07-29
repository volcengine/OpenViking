# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable batch-import manifest storage (issue #3120).

Provider-neutral: persists one manifest per batch under
``viking://resources/.batch_imports/<batch_id>.json`` (plus ``.bak``/``.tmp``
sidecars), mirroring the watch-task storage atomic-write pattern. The manifest
records the batch -> child-task mapping (``url`` / ``rel_path`` / ``task_id`` /
``to_uri``); per-item *status* is aggregated live from the ``TaskTracker``,
which is itself durable, so the manifest only needs to be written when the
batch is submitted and again once child ``task_id``s are known.

Batch ids are derived deterministically from the source URL + parent URI so
re-submitting the same wiki URL is idempotent: the existing manifest is loaded
and only children that have no ``task_id`` yet are enqueued again (the QueueFS
``AddResource`` queue + TaskTracker are themselves crash-recoverable, so an
interrupted batch resumes without duplicated work).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BATCH_IMPORT_DIR_URI = "viking://resources/.batch_imports"
BATCH_IMPORT_DIR_PREFIX = "viking://resources/.batch_imports/"


def _batch_uri(batch_id: str) -> str:
    return f"{BATCH_IMPORT_DIR_PREFIX}{batch_id}.json"


def _batch_tmp_uri(batch_id: str) -> str:
    return f"{BATCH_IMPORT_DIR_PREFIX}{batch_id}.json.tmp"


def _batch_bak_uri(batch_id: str) -> str:
    return f"{BATCH_IMPORT_DIR_PREFIX}{batch_id}.json.bak"


def is_batch_import_control_uri(uri: str) -> bool:
    """Return True when a URI points at internal batch-import control state."""
    if not isinstance(uri, str):
        return False
    normalized = uri.rstrip("/")
    if normalized == BATCH_IMPORT_DIR_URI.rstrip("/"):
        return True
    return normalized.startswith(BATCH_IMPORT_DIR_PREFIX)


def derive_batch_id(source_url: str, parent_uri: str = "") -> str:
    """Deterministic batch id from source URL + parent (idempotent re-submit)."""
    raw = f"{source_url}|{parent_uri}".encode("utf-8")
    return "bi_" + hashlib.sha1(raw).hexdigest()[:16]


def build_manifest(
    *,
    batch_id: str,
    source_url: str,
    source_kind: str,
    parent_uri: str,
    created_at: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a manifest dict ready for :func:`save_manifest`."""
    return {
        "batch_id": batch_id,
        "source_url": source_url,
        "source_kind": source_kind,
        "parent_uri": parent_uri,
        "created_at": created_at,
        "updated_at": created_at,
        "items": list(items),
    }


async def save_manifest(
    manifest: Dict[str, Any],
    viking_fs: Any,
    *,
    ctx: Any,
) -> None:
    """Atomically persist a batch manifest (tmp -> rotate bak -> rename).

    Mirrors ``WatchManager._save_tasks``: write ``.tmp``, validate the JSON
    round-trips, rotate the current file to ``.bak``, then rename ``.tmp`` into
    place. Falls back to a plain write when the FS lacks the atomic primitives.
    """
    batch_id = manifest["batch_id"]
    content = json.dumps(manifest, ensure_ascii=False, indent=2)
    if not content.strip():
        raise ValueError(f"Refusing to write empty manifest for batch {batch_id}")
    json.loads(content)  # validate round-trip before touching disk

    supports_atomic = all(hasattr(viking_fs, name) for name in ("mv", "rm", "exists", "write_file"))
    if not supports_atomic:
        await viking_fs.write_file(_batch_uri(batch_id), content, ctx=ctx)
        return

    await viking_fs.write_file(_batch_tmp_uri(batch_id), content, ctx=ctx)
    try:
        if await viking_fs.exists(_batch_bak_uri(batch_id), ctx=ctx):
            await viking_fs.rm(_batch_bak_uri(batch_id), ctx=ctx)
    except Exception as exc:  # best-effort cleanup of a stale backup
        logger.debug("[BatchManifest] failed to clear bak for %s: %s", batch_id, exc)
    try:
        if await viking_fs.exists(_batch_uri(batch_id), ctx=ctx):
            await viking_fs.mv(_batch_uri(batch_id), _batch_bak_uri(batch_id), ctx=ctx)
    except Exception as exc:
        logger.warning("[BatchManifest] failed to rotate bak for %s: %s", batch_id, exc)
    await viking_fs.mv(_batch_tmp_uri(batch_id), _batch_uri(batch_id), ctx=ctx)


async def load_manifest(
    batch_id: str,
    viking_fs: Any,
    *,
    ctx: Any,
) -> Optional[Dict[str, Any]]:
    """Load a manifest, falling back to the ``.bak`` sidecar on corruption."""
    for uri in (_batch_uri(batch_id), _batch_bak_uri(batch_id)):
        try:
            content = await viking_fs.read_file(uri, ctx=ctx)
        except Exception:
            continue
        if not content or not content.strip():
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("[BatchManifest] corrupt %s: %s", uri, exc)
            continue
        if isinstance(data, dict) and data.get("batch_id") == batch_id:
            return data
    return None


async def list_batch_ids(viking_fs: Any, *, ctx: Any) -> List[str]:
    """Return the batch ids persisted under the batch-import directory."""
    try:
        entries = await viking_fs.ls(BATCH_IMPORT_DIR_URI, ctx=ctx)
    except Exception:
        return []
    ids: List[str] = []
    for entry in entries or []:
        name = entry.get("name", "") if isinstance(entry, dict) else str(entry)
        # Only ``<batch_id>.json``; sidecars are ``.json.bak`` / ``.json.tmp``.
        if name.endswith(".json") and ".json." not in name:
            ids.append(name[: -len(".json")])
    return ids
