# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Completion records for reusing successful DingTalk imports."""

import hashlib
import json
from typing import Any

from openviking.resource.dingtalk_import import DINGTALK_SYNC_SIDECAR
from openviking.storage.internal_names import is_storage_internal_name
from openviking_cli.utils.config import get_openviking_config


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def processing_key(source: str, options: dict[str, Any]) -> str:
    config = get_openviking_config()
    fields = (
        "vlm",
        "embedding",
        "pdf",
        "code",
        "image",
        "audio",
        "video",
        "markdown",
        "text",
        "html",
        "anydoc",
        "directory",
        "parser_api",
        "semantic",
        "dingtalk",
    )
    # Store only a digest: provider credentials never enter the sync record.
    settings = config.model_dump(mode="json", include=set(fields))
    clean_options = {
        key: value
        for key, value in options.items()
        if key not in {"request_validator", "resource_lock", "create_parent"}
        and not key.startswith("_dingtalk_")
        and value is not None
    }
    # HTTP imports include parser defaults explicitly, while scheduled refreshes
    # omit them. They are the same processing request and must share one key.
    if clean_options.get("strict") is False:
        clean_options.pop("strict")
    if clean_options.get("directly_upload_media") is True:
        clean_options.pop("directly_upload_media")
    return digest({"schema": 2, "source": source, "options": clean_options, "settings": settings})


async def read_state(fs: Any, target: str, ctx: Any) -> dict[str, Any]:
    uri = f"{target.rstrip('/')}/{DINGTALK_SYNC_SIDECAR}"
    if not await fs.exists(uri, ctx=ctx):
        return {}
    state = json.loads(await fs.read_file(uri, ctx=ctx))
    if not isinstance(state, dict) or not isinstance(state.get("manifest"), list):
        raise ValueError("Invalid DingTalk sync record")
    return state


async def artifact_hashes(fs: Any, target: str, ctx: Any) -> dict[str, str]:
    entries = await fs.tree(
        target, output="original", show_all_hidden=True, node_limit=None, level_limit=None, ctx=ctx
    )
    hashes = {}
    for entry in entries:
        path = entry.get("rel_path", "")
        if entry.get("isDir") or not path or path == DINGTALK_SYNC_SIDECAR:
            continue
        if any(is_storage_internal_name(part) for part in path.split("/")):
            continue
        data = await fs.read_file_bytes(entry["uri"], ctx=ctx)
        hashes[path] = hashlib.sha256(data).hexdigest()
    return hashes


async def previous_state(fs: Any, target: str, key: str, ctx: Any) -> dict[str, Any]:
    state = await read_state(fs, target, ctx)
    if not state.get("complete") or state.get("processing_key") != key:
        return {}
    artifacts = state.get("artifacts")
    if not artifacts or artifacts != await artifact_hashes(fs, target, ctx):
        return {}
    return state


async def mark_complete(fs: Any, target: str, run_id: str, ctx: Any) -> None:
    if not run_id:
        return
    lock = await fs._async_agfs.pathlock_acquire_tree(fs._uri_to_path(target, ctx=ctx))
    try:
        state = await read_state(fs, target, ctx)
        if state.get("run_id") != run_id:
            return
        state["artifacts"] = await artifact_hashes(fs, target, ctx)
        state["complete"] = True
        await fs.write_file(
            f"{target}/{DINGTALK_SYNC_SIDECAR}", json.dumps(state), ctx=ctx, lease_ref=lock
        )
    finally:
        await fs._async_agfs.pathlock_release(lock)
