# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for persisted skill source metadata."""

import json
from typing import Any, Dict, Optional

from openviking.server.identity import RequestContext

SOURCE_METADATA_FILENAME = ".source.json"


def skill_source_metadata_uri(root_uri: str) -> str:
    return f"{root_uri.rstrip('/')}/{SOURCE_METADATA_FILENAME}"


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


async def persist_skill_source_metadata(
    service,
    ctx: RequestContext,
    result: Dict[str, Any],
    source: Optional[Dict[str, Any]],
) -> None:
    if not source:
        return

    root_uri = result.get("root_uri") or result.get("uri")
    if not root_uri:
        return

    record = dict(source)
    skill_name = result.get("name") or record.get("skill_name")
    if skill_name:
        record["skill_name"] = skill_name

    uri = skill_source_metadata_uri(root_uri)
    content = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
    viking_fs = getattr(service, "viking_fs", None)
    if viking_fs is not None:
        await viking_fs.write(uri, content, ctx=ctx)
        return

    try:
        await service.fs.write(uri=uri, content=content, ctx=ctx, mode="replace", wait=True)
    except Exception:
        await service.fs.write(uri=uri, content=content, ctx=ctx, mode="create", wait=True)
