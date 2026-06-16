# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Sidecar embedding-skip cache for queuefs.

Persisted alongside ``.overview.md`` as ``.embedding_cache.json``. Decouples
the "did we already embed this file?" decision from parsing per-file headers
out of the LLM-written overview, which silently produced false-misses when
the LLM wrote free-text or non-English overviews (issue #2383).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from openviking.server.identity import RequestContext
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

EMBEDDING_CACHE_FILENAME = ".embedding_cache.json"
_CACHE_VERSION = 1
_HASH_BYTE_LIMIT = 4 * 1024 * 1024  # cap sha256 fallback at 4 MB


async def compute_embedding_cache_key(
    viking_fs: Any,
    file_uri: str,
    ctx: Optional[RequestContext],
) -> Optional[str]:
    """Return a stable per-file cache key, or None if neither stat nor read works.

    Prefers ``{size}:{modTime}`` from ``viking_fs.stat()`` because it is cheap
    and consistent with how other parts of the codebase (e.g.
    ``_resolve_context_timestamps``) treat file identity. Falls back to
    sha256 over the first ``_HASH_BYTE_LIMIT`` bytes when stat omits either
    field, so backends without reliable mtime still benefit.
    """
    try:
        stat = await viking_fs.stat(file_uri, ctx=ctx)
    except Exception:
        stat = None

    if isinstance(stat, dict):
        size = stat.get("size")
        mod_time = stat.get("modTime")
        if size is not None and mod_time:
            return f"size+mtime:{size}:{mod_time}"

    try:
        content = await viking_fs.read_file(file_uri, ctx=ctx)
        if isinstance(content, str):
            content = content.encode("utf-8", errors="replace")
        if not isinstance(content, (bytes, bytearray)):
            return None
        digest = hashlib.sha256(bytes(content)[:_HASH_BYTE_LIMIT]).hexdigest()
        return f"sha256:{digest}"
    except Exception:
        return None


async def load_embedding_cache(
    viking_fs: Any,
    dir_uri: str,
    ctx: Optional[RequestContext],
) -> Dict[str, Dict[str, str]]:
    """Load the directory's ``.embedding_cache.json``; return entries map (filename -> entry)."""
    path = f"{dir_uri}/{EMBEDDING_CACHE_FILENAME}"
    try:
        raw = await viking_fs.read_file(path, ctx=ctx)
    except Exception:
        return {}
    if not raw:
        return {}
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return {}
    try:
        data = json.loads(raw)
    except Exception:
        logger.debug("embedding cache at %s is not valid JSON; ignoring", path)
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("version") != _CACHE_VERSION:
        return {}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for name, entry in entries.items():
        if isinstance(name, str) and isinstance(entry, dict):
            key = entry.get("content_hash")
            if isinstance(key, str) and key:
                out[name] = {
                    "content_hash": key,
                    "embedded_at": entry.get("embedded_at") or "",
                }
    return out


async def write_embedding_cache(
    viking_fs: Any,
    dir_uri: str,
    entries: Dict[str, Dict[str, str]],
    ctx: Optional[RequestContext],
) -> None:
    """Persist the cache atomically: write to a tmp file, then ``viking_fs.mv()``."""
    if not entries:
        return
    payload = {
        "version": _CACHE_VERSION,
        "entries": {
            name: {
                "content_hash": str(entry.get("content_hash", "")),
                "embedded_at": str(entry.get("embedded_at", "")),
            }
            for name, entry in entries.items()
            if isinstance(name, str) and isinstance(entry, dict)
        },
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    final_path = f"{dir_uri}/{EMBEDDING_CACHE_FILENAME}"
    tmp_path = f"{dir_uri}/{EMBEDDING_CACHE_FILENAME}.{uuid.uuid4().hex}.tmp"
    try:
        await viking_fs.write_file(tmp_path, serialized, ctx=ctx)
    except Exception as e:
        logger.warning("failed to write embedding cache tmp for %s: %s", dir_uri, e)
        return
    try:
        await viking_fs.mv(tmp_path, final_path, ctx=ctx)
    except Exception as e:
        logger.warning(
            "failed to atomically rename embedding cache for %s: %s",
            dir_uri,
            e,
        )
        try:
            # Best-effort fallback: write directly. Race window is small and
            # the cache is advisory — losing it just means a future re-embed.
            await viking_fs.write_file(final_path, serialized, ctx=ctx)
        except Exception as e2:
            logger.warning("fallback write of embedding cache failed for %s: %s", dir_uri, e2)


def make_cache_entry(content_hash: str) -> Dict[str, str]:
    return {
        "content_hash": content_hash,
        "embedded_at": datetime.now(timezone.utc).isoformat(),
    }


async def probe_vectors_present(
    file_uris: Iterable[str],
    ctx: Optional[RequestContext],
) -> Dict[str, bool]:
    """Cheap per-URI probe of the vector backend.

    Returns a map URI -> bool. Missing service/backend yields all-False so the
    caller falls back to re-embedding (safe default). Failures on individual
    URIs are also treated as "not present" rather than raising.
    """
    uris = [u for u in file_uris if u]
    if not uris or ctx is None:
        return {u: False for u in uris}
    try:
        from openviking.server.dependencies import get_service

        service = get_service()
    except Exception:
        return {u: False for u in uris}
    if not service or not getattr(service, "vikingdb_manager", None):
        return {u: False for u in uris}

    results: Dict[str, bool] = {}
    for uri in uris:
        try:
            record = await service.vikingdb_manager.fetch_by_uri(uri, ctx=ctx)
            results[uri] = bool(record)
        except Exception:
            results[uri] = False
    return results
