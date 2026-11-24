# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent ownership and append-only change history for installed Skills."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from openviking.server.identity import RequestContext
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

SKILL_HISTORY_DIRECTORY = ".skill-history"
SKILL_HISTORY_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _history_uri(root_uri: str) -> str:
    skills_root, skill_name = root_uri.rstrip("/").rsplit("/", 1)
    return f"{skills_root}/{SKILL_HISTORY_DIRECTORY}/{skill_name}.json"


def _empty_history(root_uri: str) -> Dict[str, Any]:
    return {
        "version": SKILL_HISTORY_VERSION,
        "root_uri": root_uri,
        "owner_user_id": None,
        "created_by": None,
        "created_at": None,
        "updated_by": None,
        "updated_at": None,
        "current_revision": None,
        "deleted": False,
        "events": [],
    }


async def read_skill_history(
    viking_fs, ctx: RequestContext, root_uri: str
) -> Optional[Dict[str, Any]]:
    """Read history without inventing ownership for pre-existing installations."""
    try:
        raw = await viking_fs.read(_history_uri(root_uri), ctx=ctx)
    except NotFoundError:
        return None
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("events"), list):
        return None
    return record


def skill_history_summary(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return only current ownership fields suitable for list and detail responses."""
    record = record or {}
    return {
        "owner_user_id": record.get("owner_user_id"),
        "created_by": record.get("created_by"),
        "created_at": record.get("created_at"),
        "updated_by": record.get("updated_by"),
        "updated_at": record.get("updated_at"),
        "current_revision": record.get("current_revision"),
    }


async def skill_package_hash(viking_fs, ctx: RequestContext, root_uri: str) -> str:
    """Hash package paths and bytes, excluding derived semantic summaries."""
    page = await viking_fs.tree(
        root_uri,
        ctx=ctx,
        output="original",
        show_all_hidden=True,
    )
    entries = getattr(page, "entries", page)
    manifest = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("isDir", False):
            continue
        uri = str(entry.get("uri") or "")
        if not uri or uri.rsplit("/", 1)[-1] in {".abstract.md", ".overview.md"}:
            continue
        prefix = root_uri.rstrip("/") + "/"
        path = uri[len(prefix) :] if uri.startswith(prefix) else uri.rsplit("/", 1)[-1]
        data = await viking_fs.read_file_bytes(uri, ctx=ctx)
        manifest.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})
    manifest.sort(key=lambda item: item["path"])
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def record_skill_change(
    viking_fs,
    ctx: RequestContext,
    root_uri: str,
    *,
    operation: str,
    request_id: Optional[str],
    before_hash: Optional[str],
    after_hash: Optional[str],
    lease_ref: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append one event while preserving the original creator and owner."""
    if operation not in {"create", "update", "delete"}:
        raise ValueError(f"Unsupported Skill history operation: {operation}")
    record = await read_skill_history(viking_fs, ctx, root_uri) or _empty_history(root_uri)
    occurred_at = _now()
    actor = ctx.user.user_id
    if record.get("created_by") is None and operation == "create":
        record["owner_user_id"] = actor
        record["created_by"] = actor
        record["created_at"] = occurred_at
    record["updated_by"] = actor
    record["updated_at"] = occurred_at
    record["current_revision"] = after_hash
    record["deleted"] = operation == "delete"
    record["events"].append(
        {
            "event_id": str(uuid.uuid4()),
            "operation": operation,
            "actor_user_id": actor,
            "occurred_at": occurred_at,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "request_id": request_id or str(uuid.uuid4()),
        }
    )
    # History is outside the package lease but inside a caller's skills-root lease.
    storage = (
        viking_fs if hasattr(viking_fs, "write_file") else viking_fs._ensure_initialized()  # noqa: SLF001
    )
    await storage.write_file(
        _history_uri(root_uri),
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
        ctx=ctx,
        **({"lease_ref": lease_ref} if lease_ref is not None else {}),
    )
    return record


def reject_generic_skill_mutation(uri: Optional[str], operation: str) -> None:
    """Keep public generic mutation APIs from bypassing Skill history recording."""
    if not uri:
        return
    normalized = uri.rstrip("/")
    parts = normalized.removeprefix("viking://").split("/")
    is_skill = normalized == "viking://agent/skills" or normalized.startswith(
        "viking://agent/skills/"
    )
    if len(parts) >= 3 and parts[0] == "user":
        is_skill = is_skill or parts[2] == "skills"
    if is_skill:
        raise InvalidArgumentError(
            f"Generic {operation} cannot modify Skills; use the Skill API instead",
            details={"uri": uri, "skill_api": "/api/v1/skills"},
        )
