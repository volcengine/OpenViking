# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for persisted skill source metadata."""

import json
from typing import Any, Dict, Optional

from openviking.server.identity import RequestContext

SOURCE_METADATA_FILENAME = ".source.json"


def skill_source_metadata_uri(root_uri: str) -> str:
    return f"{root_uri.rstrip('/')}/{SOURCE_METADATA_FILENAME}"


def _source_record(
    result: Dict[str, Any], source: Optional[Dict[str, Any]]
) -> Optional[tuple[str, str]]:
    root_uri = result.get("root_uri") or result.get("uri")
    if not source or not root_uri:
        return None
    record = dict(source)
    skill_name = result.get("name") or record.get("skill_name")
    if skill_name:
        record["skill_name"] = skill_name
    return (
        skill_source_metadata_uri(root_uri),
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
    )


async def write_skill_source_metadata(
    viking_fs,
    ctx: RequestContext,
    result: Dict[str, Any],
    source: Optional[Dict[str, Any]],
    *,
    lease_ref: Optional[Dict[str, Any]] = None,
) -> None:
    """Save the source before handing the package lock to background processing."""
    record = _source_record(result, source)
    if record is not None:
        uri, content = record
        await viking_fs.write_file(uri, content, ctx=ctx, lease_ref=lease_ref)


async def read_skill_source_metadata(
    service,
    ctx: RequestContext,
    root_uri: str,
) -> Dict[str, Any]:
    uri = skill_source_metadata_uri(root_uri)
    try:
        raw = await service.fs.read(uri, ctx=ctx)
    except Exception:
        return {
            "tracked": False,
            "message": "Skill source metadata is not tracked yet.",
        }

    try:
        metadata = json.loads(raw)
    except Exception:
        return {
            "tracked": False,
            "message": "Skill source metadata is invalid.",
        }

    if not isinstance(metadata, dict):
        return {
            "tracked": False,
            "message": "Skill source metadata is invalid.",
        }
    metadata["tracked"] = True
    metadata["metadata_uri"] = uri
    return metadata
