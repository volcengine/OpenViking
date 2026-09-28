# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared skill installation for ``POST /api/v1/skills``, the MCP ``add_skill`` tool,
and signed skill uploads.

One implementation resolves the source (inline SKILL.md, Git URL, or an uploaded
directory/zip), installs every selected skill, and persists each skill's source
metadata, so the three entry points cannot drift apart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.telemetry import run_operation
from openviking.server.temp_upload_store import TempUploadStore
from openviking.service.skill_sources import describe_skill_sources, resolve_skill_source
from openviking.telemetry import TelemetryRequest


async def install_skills(
    data: Any,
    ctx: RequestContext,
    *,
    names: Optional[list[str]] = None,
    list_only: bool = False,
    wait: bool = False,
    timeout: Optional[float] = None,
    target_uri: str = "",
    source_metadata: Optional[dict[str, Any]] = None,
    allow_local_path_resolution: bool = False,
    source_path_hint: Optional[str] = None,
    telemetry: TelemetryRequest = False,
) -> dict[str, Any]:
    """Install the skills found in ``data``; return one result or ``{"installed", "total"}``.

    ``list_only`` describes the source's skills without persisting anything.
    ``target_uri`` must already be validated as a skill root by the caller.
    """
    service = get_service()
    async with resolve_skill_source(
        data,
        names=names,
        allow_local_path_resolution=allow_local_path_resolution,
        source_metadata=source_metadata,
    ) as targets:
        if list_only:
            return await asyncio.to_thread(describe_skill_sources, targets)
        installed = []
        for skill_data, skill_source in targets:

            async def _install(skill_data=skill_data, skill_source=skill_source):
                result = await service.resources.add_skill(
                    data=skill_data,
                    ctx=ctx,
                    wait=wait,
                    timeout=timeout,
                    allow_local_path_resolution=isinstance(skill_data, Path),
                    source_path_hint=source_path_hint,
                    target_uri=target_uri,
                    source_metadata=skill_source,
                )
                return result

            # Each skill owns its own queue wait tracker and task ID.
            if len(targets) == 1:
                installed.append(await _install())
            else:
                execution = await run_operation(
                    operation="resources.add_skill",
                    telemetry=telemetry,
                    fn=_install,
                )
                installed.append(execution.result)
        if len(installed) == 1:
            return installed[0]
        return {"installed": installed, "total": len(installed)}


async def ingest_temp_upload_skill(
    store: TempUploadStore,
    temp_file_id: str,
    ctx: RequestContext,
    *,
    target_uri: str = "",
    names: Optional[list[str]] = None,
    list_only: bool = False,
    source_type: str = "api",
) -> dict[str, Any]:
    """Resolve an uploaded SKILL.md, directory archive, or zip and install its skills."""
    resolved = await store.resolve_for_consume(temp_file_id, ctx)
    try:
        source_metadata: dict[str, Any] = {
            "type": source_type,
            "source": "temp_upload",
            "operation": "add",
            "upload_mode": resolved.mode,
        }
        if resolved.original_filename:
            source_metadata["original_filename"] = resolved.original_filename
        return await install_skills(
            resolved.local_path,
            ctx,
            names=names,
            list_only=list_only,
            target_uri=target_uri,
            source_metadata=source_metadata,
            allow_local_path_resolution=True,
            source_path_hint=resolved.original_filename,
        )
    finally:
        await resolved.cleanup()
