# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Recover a saved Session completion before applying its frozen TTL."""

from __future__ import annotations

import json
import re
from typing import Any

from openviking.core.ttl import hidden_by_ttl
from openviking.server.error_mapping import is_storage_not_found
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime


async def reconcile_session_ttl(
    viking_fs: Any,
    ctx: Any,
    *,
    session_uri: str,
    lease_ref: Any,
    archive_uri: str = "",
) -> dict[str, Any] | None:
    """Repair a durable Phase 2 completion before deciding that a session expired.

    The caller holds the existing session and metadata exact locks. Completion is recorded in the
    archive before root metadata, so both recovery and deletion must replay
    that record. Never renew from enqueue/Phase 1 timestamps or retry time.
    """
    meta_uri = f"{session_uri}/.meta.json"
    try:
        metadata = json.loads(await viking_fs.read_file(meta_uri, ctx=ctx, include_expired=True))
    except Exception as exc:
        if is_storage_not_found(exc):
            return None
        raise
    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid session metadata: {session_uri}")
    if not metadata.get("ttl_days") or not metadata.get("expires_at"):
        return metadata
    if not archive_uri and not hidden_by_ttl(metadata.get("expires_at")):
        return metadata

    from datetime import timedelta

    expiry = parse_iso_datetime(metadata["expires_at"])
    received = parse_iso_datetime(metadata["received_at"])
    renewed = expiry
    latest_content = received

    async def read_completion(uri: str) -> None:
        nonlocal renewed, latest_content
        for name in (".meta.json", ".done"):
            try:
                marker = json.loads(
                    await viking_fs.read_file(f"{uri}/{name}", ctx=ctx, include_expired=True)
                )
            except Exception as exc:
                if is_storage_not_found(exc):
                    continue
                raise
            if not isinstance(marker, dict):
                raise ValueError(f"Invalid session completion marker: {uri}/{name}")
            completed_at = marker.get("phase2_completed_at")
            if not completed_at:
                continue
            completed = parse_iso_datetime(completed_at)
            if completed < received:
                continue
            renewed = max(renewed, completed + timedelta(days=metadata["ttl_days"]))
            latest_content = max(latest_content, completed)

    if archive_uri:
        await read_completion(archive_uri)
    else:
        # Cleanup only scans this expired session's own history, in bounded
        # pages. Public ls intentionally hides expired sessions.
        path = viking_fs._uri_to_path(f"{session_uri}/history", ctx=ctx)
        offset = 0
        while True:
            try:
                entries = await viking_fs._async_agfs.ls(
                    path, offset=offset, limit=128, sort_by="name"
                )
            except Exception as exc:
                if is_storage_not_found(exc):
                    break
                raise
            for entry in entries:
                name = str(entry.get("name", ""))
                if re.fullmatch(r"archive_\d+", name):
                    await read_completion(f"{session_uri}/history/{name}")
            if len(entries) < 128:
                break
            offset += len(entries)
    if renewed > expiry:
        metadata["expires_at"] = format_iso8601(renewed)
        metadata["received_at"] = format_iso8601(latest_content)
        await viking_fs.write_file(meta_uri, json.dumps(metadata), ctx=ctx, lease_ref=lease_ref)
    return metadata
