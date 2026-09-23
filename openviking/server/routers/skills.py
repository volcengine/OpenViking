# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Agent-scope skill management endpoints for OpenViking HTTP Server."""

import asyncio
import hashlib
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import yaml
from fastapi import APIRouter, Depends, Request
from fastapi import Path as ApiPath
from pydantic import BaseModel, ConfigDict, model_validator

from openviking.core.namespace import canonical_user_root
from openviking.core.path_variables import resolve_path_variables
from openviking.core.skill_loader import validate_skill_format
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.models.embedder.base import query_embed_cache_scope
from openviking.privacy.helpers import version_filename
from openviking.privacy.service import UserPrivacyConfigVersion
from openviking.retrieve.skill_results import skill_root_uri
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.skill_package_update import (
    clear_skill_package_contents,
    transfer_skill_package,
)
from openviking.server.skill_source_metadata import (
    SOURCE_METADATA_FILENAME,
    read_skill_source_metadata,
)
from openviking.server.telemetry import run_operation
from openviking.server.temp_upload_store import TempUploadStore
from openviking.service.skill_sources import resolve_skill_source
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.telemetry import TelemetryRequest
from openviking.utils.skill_processor import validate_skill_name
from openviking_cli.exceptions import (
    InternalError,
    InvalidArgumentError,
    NotFoundError,
    ResourceExhaustedError,
)
from openviking_cli.utils import get_logger

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])
logger = get_logger(__name__)

_DEFAULT_SKILL_LIST_LIMIT = 1000
_SKILL_INTEGRITY_MAX_ENTRIES = 512
_SKILL_INTEGRITY_MAX_FILE_BYTES = 16 * 1024 * 1024
_SKILL_INTEGRITY_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_SKILL_INTEGRITY_READ_CONCURRENCY = 8


class UpdateSkillRequest(BaseModel):
    """Replace an existing agent skill with new skill content."""

    model_config = ConfigDict(extra="forbid")

    data: Any = None
    temp_file_id: Optional[str] = None
    from_source: bool = False
    wait: bool = False
    timeout: Optional[float] = None
    source_metadata: Optional[Dict[str, Any]] = None
    telemetry: TelemetryRequest = False
    target_uri: Optional[str] = None

    @model_validator(mode="after")
    def check_data_or_temp_file_id(self):
        if self.from_source:
            if self.data is not None or self.temp_file_id:
                raise ValueError("from_source cannot be combined with data or temp_file_id")
        elif self.data is None and not self.temp_file_id:
            raise ValueError("Either data, temp_file_id, or from_source must be provided")
        return self


class FindSkillsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int = 10
    score_threshold: Optional[float] = None
    level: Optional[list[int]] = None
    telemetry: TelemetryRequest = False
    target_uri: Optional[str] = None


class ValidateSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: Any
    strict: bool = False
    source_path: Optional[str] = None
    skill_dir_name: Optional[str] = None
    target_uri: Optional[str] = None


def _agent_skills_root(ctx: RequestContext, target_uri: Optional[str] = None) -> str:
    user_root = f"{canonical_user_root(ctx)}/skills"
    if not target_uri:
        return user_root
    resolved_uri = validate_request_viking_uri(
        resolve_path_variables(target_uri), ctx, field_name="target_uri"
    ).rstrip("/")
    if resolved_uri == "viking://agent/skills" or resolved_uri.startswith("viking://agent/skills/"):
        return "viking://agent/skills"
    if resolved_uri == user_root or resolved_uri.startswith(f"{user_root}/"):
        return user_root
    raise InvalidArgumentError(
        f"Unsupported skill target URI: {target_uri}",
        details={
            "field": "target_uri",
            "allowed": [user_root, "viking://agent/skills"],
        },
    )


async def _list_skills_from_root(
    service, ctx: RequestContext, root_uri: str, node_limit: int = _DEFAULT_SKILL_LIST_LIMIT
) -> list[Dict[str, Any]]:
    """List skills from a specific root URI.

    Filters out directory entries that do not look like a valid skill — i.e.
    their abstract metadata does not yield a valid ``name`` (matching the same
    rules ``add_skill`` enforces via ``validate_skill_name``).  If the abstract
    is missing or unreadable we fall back to checking whether a SKILL.md file
    exists under the entry; only then is the directory accepted.  This keeps
    nested directories like ``<skill>/scripts`` out of the listing.
    """
    try:
        entries = await service.fs.ls(
            root_uri,
            ctx=ctx,
            output="agent",
            abs_limit=1024,
            node_limit=node_limit,
        )
    except NotFoundError:
        return []

    results: list[Dict[str, Any]] = []
    for entry in entries:
        if not (isinstance(entry, dict) and entry.get("isDir", False)):
            continue
        if not await _entry_looks_like_skill(service, ctx, entry):
            continue
        results.append(_skill_summary_from_entry(entry))
    return results


async def _entry_looks_like_skill(service, ctx: RequestContext, entry: Dict[str, Any]) -> bool:
    """Decide whether a directory entry from ``ls`` represents a real skill."""
    entry_uri = entry.get("uri", "")
    if not entry_uri:
        return False

    meta = _parse_abstract_meta(entry.get("abstract", ""))
    if meta:
        try:
            validate_skill_name(meta.get("name"))
        except Exception:
            return False
        description = meta.get("description")
        if not isinstance(description, str) or not description.strip():
            return False
        return True

    # Abstract is missing or unparsable — fall back to checking that the
    # directory actually contains a SKILL.md file before listing it.  Any
    # error (including NotFound) means we cannot confirm it is a skill.
    try:
        skill_md_stat = await service.fs.stat(_skill_md_uri(entry_uri), ctx=ctx, skip_count=True)
    except Exception:
        return False
    if not skill_md_stat or skill_md_stat.get("isDir", False):
        return False
    return True


def _validate_skill_name(skill_name: str) -> str:
    return validate_skill_name(skill_name)


def _skill_root_uri(ctx: RequestContext, skill_name: str, target_uri: Optional[str] = None) -> str:
    return f"{_agent_skills_root(ctx, target_uri)}/{_validate_skill_name(skill_name)}"


def _skill_md_uri(root_uri: str) -> str:
    return f"{root_uri.rstrip('/')}/SKILL.md"


def _skill_name_from_uri(uri: str) -> str:
    return uri.rstrip("/").split("/")[-1]


def _relative_skill_path(root_uri: str, uri: str) -> str:
    prefix = root_uri.rstrip("/") + "/"
    if uri.startswith(prefix):
        return uri[len(prefix) :]
    return _skill_name_from_uri(uri)


def _skill_file_kind(path: str, is_dir: bool) -> str:
    if is_dir:
        return "directory"
    if path == "SKILL.md":
        return "definition"
    if path in {".abstract.md", ".overview.md"}:
        return "summary"
    return "auxiliary"


def _parse_abstract_meta(abstract: str) -> Dict[str, Any]:
    try:
        parsed = yaml.safe_load(abstract or "") or {}
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _skill_summary_from_meta(name: str, root_uri: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "skill",
        "name": name,
        "uri": root_uri,
        "root_uri": root_uri,
        "skill_md_uri": _skill_md_uri(root_uri),
        "description": meta.get("description", ""),
        "tags": meta.get("tags") or [],
        "allowed_tools": meta.get("allowed_tools") or meta.get("allowed-tools") or [],
    }


def _skill_summary_from_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    root_uri = entry.get("uri", "")
    name = entry.get("name") or _skill_name_from_uri(root_uri)
    return _skill_summary_from_meta(name, root_uri, _parse_abstract_meta(entry.get("abstract", "")))


async def _skill_summary_from_hit(
    hit: Dict[str, Any], service, ctx: RequestContext
) -> Dict[str, Any]:
    hit_uri = hit.get("uri", "")
    root_uri = _skill_root_from_hit_uri(hit_uri)
    meta = _parse_abstract_meta(await service.fs.abstract(root_uri, ctx=ctx))
    name = meta.get("name") or _skill_name_from_uri(root_uri)
    summary = _skill_summary_from_meta(name, root_uri, meta)
    summary["uri"] = hit_uri
    summary["score"] = hit.get("score", 0.0)
    summary["match_reason"] = hit.get("match_reason", "")
    summary["level"] = hit.get("level", 0)
    summary["abstract"] = hit.get("abstract", "")
    return summary


def _skill_root_from_hit_uri(hit_uri: str) -> str:
    """Resolve the package root even when a hit is several directories deep."""
    return skill_root_uri(hit_uri) or hit_uri.rstrip("/")


async def _require_skill(
    service, ctx: RequestContext, skill_name: str, target_uri: Optional[str] = None
) -> str:
    if target_uri:
        root_uri = _skill_root_uri(ctx, skill_name, target_uri)
        try:
            stat = await service.fs.stat(root_uri, ctx=ctx, skip_count=True)
            if stat and stat.get("isDir", False):
                return root_uri
        except NotFoundError:
            pass
        except Exception as exc:
            raise NotFoundError(root_uri, "skill") from exc

    user_root_uri = _skill_root_uri(ctx, skill_name)
    try:
        stat = await service.fs.stat(user_root_uri, ctx=ctx, skip_count=True)
        if stat and stat.get("isDir", False):
            return user_root_uri
    except NotFoundError:
        pass
    except Exception as exc:
        raise NotFoundError(user_root_uri, "skill") from exc

    agent_root_uri = _skill_root_uri(ctx, skill_name, "viking://agent/skills")
    try:
        stat = await service.fs.stat(agent_root_uri, ctx=ctx, skip_count=True)
        if stat and stat.get("isDir", False):
            return agent_root_uri
    except NotFoundError:
        pass
    except Exception as exc:
        raise NotFoundError(agent_root_uri, "skill") from exc

    raise NotFoundError(_skill_root_uri(ctx, skill_name), "skill")


async def _list_skill_files(
    service,
    ctx: RequestContext,
    root_uri: str,
    *,
    node_limit: int = 10000,
    level_limit: int = 10,
) -> list[Dict[str, Any]]:
    entries: list[Dict[str, Any]] = []
    queue: list[tuple[str, int]] = [(root_uri, 0)]
    visited_dirs = {root_uri.rstrip("/")}

    while queue and len(entries) < node_limit:
        current_uri, depth = queue.pop(0)
        child_limit = max(node_limit - len(entries), 0)
        if child_limit <= 0:
            break
        children = await service.fs.ls(
            current_uri,
            ctx=ctx,
            output="agent",
            abs_limit=1024,
            show_all_hidden=True,
            node_limit=child_limit,
        )
        for entry in children:
            if not isinstance(entry, dict):
                continue
            entry_uri = entry.get("uri", "")
            if not entry_uri:
                continue
            entries.append(entry)
            if len(entries) >= node_limit:
                break
            if not entry.get("isDir", False) or depth + 1 >= level_limit:
                continue
            normalized_uri = entry_uri.rstrip("/")
            if normalized_uri in visited_dirs:
                continue
            visited_dirs.add(normalized_uri)
            queue.append((entry_uri, depth + 1))
    return entries


async def _skill_manifest_with_integrity(
    service,
    ctx: RequestContext,
    root_uri: str,
    *,
    include_integrity: bool = True,
    max_entries: int = _SKILL_INTEGRITY_MAX_ENTRIES,
    max_file_bytes: int = _SKILL_INTEGRITY_MAX_FILE_BYTES,
    max_total_bytes: int = _SKILL_INTEGRITY_MAX_TOTAL_BYTES,
) -> tuple[list[Dict[str, Any]], str | None]:
    """Build a Skill manifest, optionally content-addressed for remote consumers."""
    entries = await _list_skill_files(
        service,
        ctx,
        root_uri,
        node_limit=max_entries + 2 if include_integrity else 10000,
    )
    visible_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and _relative_skill_path(root_uri, str(entry.get("uri") or "")) != SOURCE_METADATA_FILENAME
    ]
    if include_integrity and len(visible_entries) > max_entries:
        raise ResourceExhaustedError(f"Skill integrity manifest exceeds {max_entries} entries")
    if include_integrity:
        declared_total = 0
        for entry in visible_entries:
            if bool(entry.get("isDir", False)) or entry.get("size") is None:
                continue
            declared_size = int(entry["size"])
            path = _relative_skill_path(root_uri, str(entry.get("uri") or ""))
            if declared_size > max_file_bytes:
                raise ResourceExhaustedError(f"Skill file exceeds integrity size limit: {path}")
            declared_total += declared_size
            if declared_total > max_total_bytes:
                raise ResourceExhaustedError(
                    f"Skill integrity manifest exceeds {max_total_bytes} total bytes"
                )

    async def build_entry(entry: Dict[str, Any]) -> Dict[str, Any] | None:
        uri = str(entry.get("uri") or "")
        path = _relative_skill_path(root_uri, uri)
        if not uri or path == SOURCE_METADATA_FILENAME:
            return None
        is_dir = bool(entry.get("isDir", False))
        item: Dict[str, Any] = {
            "name": entry.get("name") or _skill_name_from_uri(uri),
            "uri": uri,
            "path": path,
            "is_dir": is_dir,
            "kind": _skill_file_kind(path, is_dir),
        }
        if not is_dir and include_integrity:
            declared_size = entry.get("size")
            if declared_size is not None and int(declared_size) > max_file_bytes:
                raise ResourceExhaustedError(f"Skill file exceeds integrity size limit: {path}")
            data = await service.fs.read_file_bytes(uri, ctx=ctx)
            if len(data) > max_file_bytes:
                raise ResourceExhaustedError(f"Skill file exceeds integrity size limit: {path}")
            item["size"] = len(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
        return item

    built: list[Dict[str, Any] | None] = []
    total_bytes = 0
    for offset in range(0, len(visible_entries), _SKILL_INTEGRITY_READ_CONCURRENCY):
        batch = visible_entries[offset : offset + _SKILL_INTEGRITY_READ_CONCURRENCY]
        batch_items = await asyncio.gather(*(build_entry(entry) for entry in batch))
        for item in batch_items:
            if item is not None and not item["is_dir"]:
                total_bytes += int(item.get("size") or 0)
                if total_bytes > max_total_bytes:
                    raise ResourceExhaustedError(
                        f"Skill integrity manifest exceeds {max_total_bytes} total bytes"
                    )
        built.extend(batch_items)
    files = [item for item in built if item is not None]
    if not include_integrity:
        return files, None
    revision_payload = [
        {
            "path": item["path"],
            "is_dir": item["is_dir"],
            "size": item.get("size"),
            "sha256": item.get("sha256"),
        }
        for item in sorted(files, key=lambda value: value["path"])
    ]
    revision = hashlib.sha256(
        json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return files, revision


@asynccontextmanager
async def _skill_snapshot_lock(
    service,
    ctx: RequestContext,
    root_uri: str,
) -> AsyncIterator[None]:
    """Hold the storage tree lock while one content-addressed Skill view is read."""
    viking_fs = service.fs._ensure_initialized()  # noqa: SLF001
    await viking_fs._ensure_access(root_uri, ctx)  # noqa: SLF001
    path = viking_fs._uri_to_path(root_uri, ctx=ctx)  # noqa: SLF001
    fs_ctx = {"account_id": ctx.account_id}
    lease = await viking_fs._async_agfs.pathlock_acquire_tree(  # noqa: SLF001
        path,
        fs_ctx=fs_ctx,
    )
    try:
        yield
    finally:
        await viking_fs._async_agfs.pathlock_release(lease, fs_ctx=fs_ctx)  # noqa: SLF001


async def _read_skill_detail(
    service,
    ctx: RequestContext,
    *,
    skill_name: str,
    root_uri: str,
    include_content: Optional[bool],
    include_files: bool,
    include_integrity: bool,
    include_source: bool,
    level: Optional[int],
) -> Dict[str, Any]:
    """Read one Skill; integrity requests observe a storage-locked package snapshot."""

    async def read_fields() -> Dict[str, Any]:
        abstract = await service.fs.abstract(root_uri, ctx=ctx)
        result = _skill_summary_from_meta(skill_name, root_uri, _parse_abstract_meta(abstract))
        if level is None or level == 0:
            result["abstract"] = abstract
        if level is None or level == 1:
            result["overview"] = await service.fs.overview(root_uri, ctx=ctx)
        if (
            level == 2
            or include_content is True
            or (level is None and include_content is not False)
        ):
            content = await service.fs.read(_skill_md_uri(root_uri), ctx=ctx)
            result["content"] = content
            result["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if include_files:
            files, revision = await _skill_manifest_with_integrity(
                service,
                ctx,
                root_uri,
                include_integrity=include_integrity,
            )
            result["files"] = files
            if revision is not None:
                result["revision"] = revision
        if include_source:
            result["source"] = await read_skill_source_metadata(service, ctx, root_uri)
        return result

    if include_integrity:
        async with _skill_snapshot_lock(service, ctx, root_uri):
            return await read_fields()
    return await read_fields()


async def _restore_skill_privacy(
    service,
    ctx: RequestContext,
    skill_name: str,
    previous_privacy: Optional[UserPrivacyConfigVersion],
    deleted_snapshot: Optional[Dict[str, bytes]] = None,
    owner_lease_ref: Optional[Dict[str, Any]] = None,
) -> None:
    privacy = service.privacy_configs
    if privacy is None:
        return
    if deleted_snapshot is not None:
        # Empty configuration deletes the whole history. Restore the saved
        # files as well as current values if the package update then fails.
        viking_fs = service.fs._ensure_initialized()  # noqa: SLF001
        root = privacy.get_config_root(ctx, "skill", skill_name)
        async with privacy._config_lock(  # noqa: SLF001
            ctx, "skill", skill_name, owner_lease_ref=owner_lease_ref
        ) as lease:
            # Keep the config root: removing it would also remove the tree
            # lock and allow a successful concurrent save to be overwritten.
            await privacy._clear_contents(ctx, "skill", skill_name, lease)  # noqa: SLF001
            for path, content in deleted_snapshot.items():
                await viking_fs.write_file_bytes(
                    f"{root}/{path}", content, ctx=ctx, lease_ref=lease
                )
        return
    if previous_privacy is None:
        await privacy.delete(ctx, "skill", skill_name, owner_lease_ref=owner_lease_ref)
        return
    await privacy.activate_version(
        ctx,
        "skill",
        skill_name,
        previous_privacy.version,
        updated_by=ctx.user.user_id,
        owner_lease_ref=owner_lease_ref,
    )


@router.get("")
async def list_skills(
    node_limit: int = _DEFAULT_SKILL_LIST_LIMIT,
    target_uri: Optional[str] = None,
    _ctx: RequestContext = Depends(get_request_context),
):
    """List installed agent skills; ``node_limit`` caps each skill root (0 keeps the default)."""
    service = get_service()
    limit = node_limit if node_limit > 0 else _DEFAULT_SKILL_LIST_LIMIT
    if target_uri:
        resolved_uri = validate_request_viking_uri(
            resolve_path_variables(target_uri), _ctx, field_name="target_uri"
        )
        skills = await _list_skills_from_root(service, _ctx, resolved_uri, limit)
        return Response(
            status="ok", result={"root_uri": resolved_uri, "skills": skills, "total": len(skills)}
        )
    else:
        user_skills = await _list_skills_from_root(
            service, _ctx, f"{canonical_user_root(_ctx)}/skills", limit
        )
        agent_skills = await _list_skills_from_root(service, _ctx, "viking://agent/skills", limit)
        # Intentionally concatenate without deduplication: when the same skill
        # name exists in both the user-private and the account-shared agent
        # scope, both entries should be visible so the caller can tell them
        # apart by ``root_uri``.
        merged_skills = [*user_skills, *agent_skills]
        return Response(
            status="ok",
            result={
                "root_uris": [f"{canonical_user_root(_ctx)}/skills", "viking://agent/skills"],
                "skills": merged_skills,
                "total": len(merged_skills),
            },
        )


@router.post("/find")
async def find_skills(
    request: FindSkillsRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Find agent skills by semantic search."""
    service = get_service()
    target_uri = request.target_uri
    if target_uri:
        resolved_uri = validate_request_viking_uri(
            resolve_path_variables(target_uri), _ctx, field_name="target_uri"
        )
        execution = await run_operation(
            operation="skills.find",
            telemetry=request.telemetry,
            fn=lambda: service.search.find_skills(
                query=request.query,
                ctx=_ctx,
                target_uri=resolved_uri,
                limit=request.limit,
                score_threshold=request.score_threshold,
                level=request.level,
            ),
        )
        result = execution.result
        result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
        hits = [
            await _skill_summary_from_hit(hit, service, _ctx)
            for hit in result_dict.get("skills", [])
        ]
        return Response(
            status="ok",
            result={"root_uri": resolved_uri, "skills": hits, "total": len(hits)},
            telemetry=execution.telemetry,
        ).model_dump(exclude_none=True)
    else:
        user_root = f"{canonical_user_root(_ctx)}/skills"
        agent_root = "viking://agent/skills"

        # Both finds embed the same query text, so wrap the fan-out in the
        # request-scoped cache to reuse the first in-flight embed.
        with query_embed_cache_scope():
            user_execution, agent_execution = await asyncio.gather(
                run_operation(
                    operation="skills.find",
                    telemetry=request.telemetry,
                    fn=lambda: service.search.find_skills(
                        query=request.query,
                        ctx=_ctx,
                        target_uri=user_root,
                        limit=request.limit,
                        score_threshold=request.score_threshold,
                        level=request.level,
                    ),
                ),
                run_operation(
                    operation="skills.find",
                    telemetry=request.telemetry,
                    fn=lambda: service.search.find_skills(
                        query=request.query,
                        ctx=_ctx,
                        target_uri=agent_root,
                        limit=request.limit,
                        score_threshold=request.score_threshold,
                        level=request.level,
                    ),
                ),
            )

        user_result = user_execution.result
        user_result_dict = (
            user_result.to_dict() if hasattr(user_result, "to_dict") else dict(user_result or {})
        )
        user_hits = [
            await _skill_summary_from_hit(hit, service, _ctx)
            for hit in user_result_dict.get("skills", [])
        ]

        agent_result = agent_execution.result
        agent_result_dict = (
            agent_result.to_dict() if hasattr(agent_result, "to_dict") else dict(agent_result or {})
        )
        agent_hits = [
            await _skill_summary_from_hit(hit, service, _ctx)
            for hit in agent_result_dict.get("skills", [])
        ]

        merged_hits = [*user_hits, *agent_hits]
        merged_hits.sort(key=lambda hit: (-hit.get("score", 0), hit["root_uri"]))
        merged_hits = merged_hits[: request.limit]

        return Response(
            status="ok",
            result={
                "root_uris": [user_root, agent_root],
                "skills": merged_hits,
                "total": len(merged_hits),
            },
            telemetry=user_execution.telemetry,
        ).model_dump(exclude_none=True)


@router.post("/validate")
async def validate_skill(
    request: ValidateSkillRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Validate a SKILL.md payload using Agent Skills formatting rules."""
    del _ctx
    result = validate_skill_format(
        request.data,
        strict=request.strict,
        skill_dir_name=request.skill_dir_name,
        source_path=request.source_path,
    )
    return Response(status="ok", result=result)


@router.get("/{skill_name}")
async def get_skill(
    skill_name: str = ApiPath(..., description="Skill name"),
    target_uri: Optional[str] = None,
    include_content: Optional[bool] = None,
    include_files: bool = True,
    include_integrity: bool = False,
    include_source: bool = False,
    level: Optional[int] = None,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Show one installed agent skill."""
    if level is not None and level not in {0, 1, 2}:
        raise InvalidArgumentError(
            "Skill show level must be 0, 1, or 2",
            details={"field": "level", "allowed": [0, 1, 2]},
        )
    service = get_service()
    root_uri = await _require_skill(service, _ctx, skill_name, target_uri)
    result = await _read_skill_detail(
        service,
        _ctx,
        skill_name=skill_name,
        root_uri=root_uri,
        include_content=include_content,
        include_files=include_files,
        include_integrity=include_integrity,
        include_source=include_source,
        level=level,
    )
    return Response(status="ok", result=result)


@router.put("/{skill_name}")
async def update_skill(
    http_request: Request,
    request: UpdateSkillRequest,
    skill_name: str = ApiPath(..., description="Skill name"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Replace an existing agent skill with new content."""
    service = get_service()
    root_uri = await _require_skill(service, _ctx, skill_name, request.target_uri)

    data = request.data
    allow_local_path_resolution = False
    resolved = None
    source_metadata = request.source_metadata or {
        "type": "api",
        "source": "inline_content",
        "operation": "update",
    }
    if request.temp_file_id:
        resolved = await TempUploadStore.build(http_request.app.state.config).resolve_for_consume(
            request.temp_file_id, _ctx
        )
        data = Path(resolved.local_path)
        allow_local_path_resolution = True
        if request.source_metadata is None:
            source_metadata = {
                "type": "api",
                "source": "temp_upload",
                "operation": "update",
                "upload_mode": resolved.mode,
            }
        if resolved.original_filename and request.source_metadata is None:
            source_metadata["original_filename"] = resolved.original_filename

    git_source = None
    if request.from_source:
        git_source = await read_skill_source_metadata(service, _ctx, root_uri)
        if git_source.get("type") != "git":
            raise InvalidArgumentError(
                "Skill has no recorded Git source; supply content or an upload"
            )
        git_source = {
            key: git_source.get(key)
            for key in ("type", "source", "clone_url", "ref_name", "subdir")
        }
        data = git_source.get("clone_url")

    source_path_hint = resolved.original_filename if resolved else None

    async def _update() -> Dict[str, Any]:
        # Derive backup root from the actual skill root URI to keep backup in the same scope.
        skill_root_parent = root_uri.rsplit("/", 1)[0]
        backup_uri = f"{skill_root_parent}/.{skill_name}.update-backup-{uuid.uuid4().hex}"
        backup_created = False
        privacy_update_attempted = False
        previous_privacy = None
        preparation = None
        privacy = service.privacy_configs
        viking_fs = service.fs._ensure_initialized()  # noqa: SLF001
        update_lease = None
        privacy_lease = None
        task_id = str(uuid.uuid4())
        deleted_privacy_snapshot = None
        cleanup_warnings = []

        def report_cleanup_failure(location: str, exc: Exception) -> None:
            warning = f"Skill update cleanup failed at {location}: {exc}"
            cleanup_warnings.append(warning)
            logger.warning(warning, exc_info=True)

        async def discard_backup() -> None:
            # Called only after commit or successful restoration. Failure to
            # discard this now-unused copy must not change that outcome.
            if backup_created:
                try:
                    await viking_fs.rm(backup_uri, ctx=_ctx, recursive=True, lease_ref=update_lease)
                except Exception as exc:
                    report_cleanup_failure(backup_uri, exc)

        async def back_up_package() -> None:
            nonlocal update_lease, privacy_lease, backup_created, previous_privacy
            update_lease = await viking_fs._async_agfs.pathlock_acquire_tree_batch(
                [viking_fs._uri_to_path(uri, ctx=_ctx) for uri in (root_uri, backup_uri)]
            )
            # Tree acquisition may recreate a missing directory for its token.
            # Recheck the installed definition and snapshot privacy under the lock.
            if not await viking_fs.exists(f"{root_uri}/SKILL.md", ctx=_ctx):
                raise NotFoundError(root_uri, "skill")
            if privacy is not None:
                # Always acquire package then config. Keep config ownership
                # through commit/rollback so a separate successful save cannot
                # be silently replaced by restoration of this request's state.
                privacy_lease = await viking_fs._async_agfs.pathlock_acquire_tree(
                    viking_fs._uri_to_path(
                        privacy.get_config_root(_ctx, "skill", skill_name), ctx=_ctx
                    ),
                    timeout_secs=30.0,
                )
                previous_privacy = await privacy.get_current(_ctx, "skill", skill_name)
            try:
                await transfer_skill_package(
                    viking_fs, root_uri, backup_uri, ctx=_ctx, lease_ref=update_lease
                )
            except Exception as exc:
                raise InternalError(
                    f"Skill backup failed; backup location: {backup_uri}", cause=exc
                ) from exc
            backup_created = True
            await clear_skill_package_contents(
                viking_fs, root_uri, ctx=_ctx, lease_ref=update_lease
            )

        async def restore_package() -> None:
            # Do not restore while either queued or active work can write back.
            await service.resources.cancel_skill_processing(task_id, _ctx)
            failures = []
            if backup_created:
                try:
                    await clear_skill_package_contents(
                        viking_fs, root_uri, ctx=_ctx, lease_ref=update_lease
                    )
                    await transfer_skill_package(
                        viking_fs, backup_uri, root_uri, ctx=_ctx, lease_ref=update_lease
                    )
                except Exception as exc:
                    failures.append(f"package: {exc}")
            if privacy_update_attempted:
                try:
                    await _restore_skill_privacy(
                        service,
                        _ctx,
                        skill_name,
                        previous_privacy,
                        deleted_privacy_snapshot,
                        owner_lease_ref=privacy_lease,
                    )
                except Exception as exc:
                    failures.append(f"privacy: {exc}")
            if failures:
                raise InternalError(
                    f"Skill update rollback failed; backup location: {backup_uri}; "
                    + "; ".join(failures)
                )
            await discard_backup()

        async def release_update_locks() -> None:
            try:
                if privacy_lease is not None:
                    try:
                        # Deletion inside the update preserved the locked
                        # directory. Remove an empty config only now, after
                        # all possible config writes and rollback have ended.
                        if (
                            await privacy.get_meta(_ctx, "skill", skill_name) is None
                            and await privacy.get_current(_ctx, "skill", skill_name) is None
                            and not await privacy.list_versions(_ctx, "skill", skill_name)
                        ):
                            await viking_fs.rm(
                                privacy.get_config_root(_ctx, "skill", skill_name),
                                recursive=True,
                                ctx=_ctx,
                                lease_ref=privacy_lease,
                            )
                    except Exception as exc:
                        # Config data has already been committed/restored.
                        # Retain the result (or original error), but still
                        # release both locks and report the leftover directory.
                        report_cleanup_failure(
                            privacy.get_config_root(_ctx, "skill", skill_name), exc
                        )
                    finally:
                        await viking_fs._async_agfs.pathlock_release(privacy_lease)
            finally:
                if update_lease is not None:
                    await viking_fs._async_agfs.pathlock_release(update_lease)

        try:
            async with resolve_skill_source(
                data,
                allow_local_path_resolution=allow_local_path_resolution,
                source_metadata=source_metadata,
                git_source=git_source,
            ) as targets:
                if len(targets) > 1:
                    targets = [
                        (item, metadata) for item, metadata in targets if item.name == skill_name
                    ]
                if len(targets) != 1:
                    raise InvalidArgumentError("Update source must identify exactly one skill")
                skill_data, skill_source = targets[0]
                preparation = await service.resources._skill_processor.prepare_skill_processing(  # noqa: SLF001
                    skill_data,
                    ctx=_ctx,
                    allow_local_path_resolution=isinstance(skill_data, Path),
                    source_path_hint=source_path_hint,
                )
                expected_name = _validate_skill_name(skill_name)
                if preparation.skill_dict.get("name") != expected_name:
                    raise InvalidArgumentError(
                        f"Skill name mismatch: path name is '{expected_name}', content name is '{preparation.skill_dict.get('name')}'",
                        details={
                            "expected": expected_name,
                            "actual": preparation.skill_dict.get("name"),
                        },
                    )
                await run_to_completion(back_up_package)
                # All synchronous configuration changes must finish before the
                # package can produce background summary or embedding work.
                if (
                    privacy is not None
                    and previous_privacy is not None
                    and not preparation.privacy_values
                ):
                    privacy_root = privacy.get_config_root(_ctx, "skill", skill_name)
                    privacy_paths = [".meta.json", "current.json"]
                    privacy_paths.extend(
                        f"history/{version_filename(version)}"
                        for version in await privacy.list_versions(_ctx, "skill", skill_name)
                    )
                    deleted_privacy_snapshot = {
                        path: await viking_fs.read_file_bytes(f"{privacy_root}/{path}", ctx=_ctx)
                        for path in privacy_paths
                    }
                privacy_update_attempted = True
                await run_to_completion(
                    lambda: service.resources._skill_processor.apply_skill_privacy(  # noqa: SLF001
                        preparation.skill_dict,
                        preparation.privacy_values,
                        _ctx,
                        change_reason="auto-extracted from update_skill",
                        delete_if_empty=True,
                        owner_lease_ref=privacy_lease,
                    )
                )
                result = await service.resources.add_skill(
                    data=preparation,
                    ctx=_ctx,
                    wait=request.wait,
                    timeout=request.timeout,
                    allow_local_path_resolution=False,
                    source_path_hint=source_path_hint,
                    apply_privacy=False,
                    privacy_change_reason="auto-extracted from update_skill",
                    target_uri=skill_root_parent,
                    source_metadata=skill_source,
                    task_id=task_id,
                    owner_lease_ref=update_lease,
                )
        except BaseException as update_error:
            try:
                await run_to_completion(restore_package)
            except Exception as rollback_error:
                raise InternalError(
                    "Skill update failed and rollback could not complete", cause=rollback_error
                ) from update_error
            raise
        else:
            await run_to_completion(discard_backup)
            result["action"] = "update"
        finally:
            try:
                await run_to_completion(release_update_locks)
            finally:
                if preparation and preparation.cleanup_path:
                    shutil.rmtree(preparation.cleanup_path, ignore_errors=True)
                if resolved:
                    try:
                        await run_to_completion(resolved.cleanup)
                    except Exception as exc:
                        report_cleanup_failure(resolved.local_path, exc)
        if cleanup_warnings:
            result.setdefault("warnings", []).extend(cleanup_warnings)
        return result

    execution = await run_operation(
        operation="skills.update",
        telemetry=request.telemetry,
        fn=_update,
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.delete("/{skill_name}")
async def delete_skill(
    skill_name: str = ApiPath(..., description="Skill name"),
    target_uri: Optional[str] = None,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Remove one installed agent skill."""
    service = get_service()
    root_uri = await _require_skill(service, _ctx, skill_name, target_uri)
    result = await service.fs.rm(root_uri, ctx=_ctx, recursive=True)
    privacy_deleted = False
    privacy = service.privacy_configs
    if privacy is not None:
        privacy_deleted = await privacy.delete(_ctx, "skill", skill_name)
    response_result: Dict[str, Any] = {"name": skill_name, "uri": root_uri, "root_uri": root_uri}
    if isinstance(result, dict) and "estimated_deleted_count" in result:
        response_result["estimated_deleted_count"] = result["estimated_deleted_count"]
    response_result["privacy_deleted"] = privacy_deleted
    return Response(status="ok", result=response_result)
