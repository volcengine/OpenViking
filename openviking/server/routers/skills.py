# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Agent-scope skill management endpoints for OpenViking HTTP Server."""

import asyncio
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml
from fastapi import APIRouter, Depends, Request
from fastapi import Path as ApiPath
from pydantic import BaseModel, ConfigDict, model_validator

from openviking.core.namespace import canonical_user_root
from openviking.core.path_variables import resolve_path_variables
from openviking.core.skill_loader import SkillLoader
from openviking.privacy.service import UserPrivacyConfigVersion
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.skill_source_metadata import (
    SOURCE_METADATA_FILENAME,
    persist_skill_source_metadata,
    read_skill_source_metadata,
)
from openviking.server.telemetry import run_operation
from openviking.server.temp_upload_store import TempUploadStore
from openviking.telemetry import TelemetryRequest
from openviking.utils.skill_processor import validate_skill_name
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class UpdateSkillRequest(BaseModel):
    """Replace an existing agent skill with new skill content."""

    model_config = ConfigDict(extra="forbid")

    data: Any = None
    temp_file_id: Optional[str] = None
    wait: bool = False
    timeout: Optional[float] = None
    source_metadata: Optional[Dict[str, Any]] = None
    telemetry: TelemetryRequest = False
    target_uri: Optional[str] = None

    @model_validator(mode="after")
    def check_data_or_temp_file_id(self):
        if self.data is None and not self.temp_file_id:
            raise ValueError("Either 'data' or 'temp_file_id' must be provided")
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
    resolved_uri = resolve_path_variables(target_uri).rstrip("/")
    if resolved_uri == "viking://agent/skills" or resolved_uri.startswith(
        "viking://agent/skills/"
    ):
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


async def _list_skills_from_root(service, ctx: RequestContext, root_uri: str) -> list[Dict[str, Any]]:
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
            node_limit=1000,
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


async def _entry_looks_like_skill(
    service, ctx: RequestContext, entry: Dict[str, Any]
) -> bool:
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
        skill_md_stat = await service.fs.stat(_skill_md_uri(entry_uri), ctx=ctx)
    except Exception:
        return False
    if not skill_md_stat or skill_md_stat.get("isDir", False):
        return False
    return True


def _merge_skills(skills_list: list[list[Dict[str, Any]]]) -> list[Dict[str, Any]]:
    """Merge skills from multiple sources, user skills take precedence."""
    seen_names = set()
    result = []
    for skills in skills_list:
        for skill in skills:
            name = skill.get("name")
            if name not in seen_names:
                seen_names.add(name)
                result.append(skill)
    return result


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


_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _validation_issue(rule: str, message: str, field: str = "") -> Dict[str, str]:
    issue = {"rule": rule, "message": message}
    if field:
        issue["field"] = field
    return issue


def _parse_skill_for_validation(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        parsed = dict(data)
        parsed["content"] = parsed.get("content") or ""
    elif isinstance(data, str):
        frontmatter, body = SkillLoader._split_frontmatter(data)
        if not frontmatter:
            raise ValueError("SKILL.md must have YAML frontmatter")
        try:
            meta = yaml.safe_load(frontmatter)
        except Exception as exc:
            raise ValueError(f"Invalid YAML frontmatter: {exc}") from exc
        if not isinstance(meta, dict):
            raise ValueError("Invalid YAML frontmatter")
        parsed = dict(meta)
        parsed["content"] = body.strip()
    else:
        raise ValueError(f"Unsupported data type: {type(data)}")

    allowed_tools = parsed.get("allowed_tools")
    if not allowed_tools:
        allowed_tools = parsed.get("allowed-tools")
    if allowed_tools is not None:
        parsed["allowed_tools"] = (
            allowed_tools if isinstance(allowed_tools, list) else [allowed_tools]
        )
    parsed.pop("allowed-tools", None)

    tags = parsed.get("tags")
    if tags is not None and not isinstance(tags, list):
        parsed["tags"] = [tags]

    return parsed


def _validate_skill_format(
    service,
    data: Any,
    *,
    strict: bool,
    skill_dir_name: Optional[str],
    source_path: Optional[str],
) -> Dict[str, Any]:
    errors: list[Dict[str, str]] = []
    warnings: list[Dict[str, str]] = []

    try:
        parsed = _parse_skill_for_validation(data)
    except Exception as exc:
        return {
            "valid": False,
            "strict": strict,
            "errors": [
                _validation_issue(
                    "yaml_format",
                    str(exc),
                    "data",
                )
            ],
            "warnings": [],
            "source_path": source_path or "",
        }

    name = parsed.get("name")
    description = parsed.get("description")
    content = parsed.get("content") or ""

    if not isinstance(name, str) or not name.strip():
        errors.append(_validation_issue("name_required", "name is required", "name"))
    if not isinstance(description, str) or not description.strip():
        errors.append(
            _validation_issue("description_required", "description is required", "description")
        )

    def add_mode_issue(rule: str, message: str, field: str):
        issue = _validation_issue(rule, message, field)
        if strict:
            errors.append(issue)
        else:
            warnings.append(issue)

    if isinstance(name, str) and name.strip():
        normalized_name = name.strip()
        normalized_dir_name = (skill_dir_name or "").strip()
        if normalized_dir_name and normalized_name != normalized_dir_name:
            add_mode_issue(
                "name_matches_directory",
                f"name '{normalized_name}' does not match directory name '{normalized_dir_name}'",
                "name",
            )
        if len(normalized_name) > 64:
            add_mode_issue("name_max_length", "name must not exceed 64 characters", "name")
        if not _SKILL_NAME_PATTERN.match(normalized_name):
            add_mode_issue(
                "name_allowed_characters",
                "name may only contain letters, numbers, underscores, and hyphens",
                "name",
            )

    if isinstance(description, str) and len(description) > 1024:
        add_mode_issue(
            "description_max_length",
            "description must not exceed 1024 characters",
            "description",
        )

    body_lines = len(content.splitlines())
    if strict and body_lines > 500:
        warnings.append(
            _validation_issue(
                "body_max_lines",
                "SKILL.md body exceeds 500 lines",
                "content",
            )
        )

    return {
        "valid": not errors,
        "strict": strict,
        "name": name or "",
        "description": description or "",
        "tags": parsed.get("tags") or [],
        "allowed_tools": parsed.get("allowed_tools") or [],
        "body_lines": body_lines,
        "source_path": source_path or "",
        "skill_dir_name": skill_dir_name or "",
        "errors": errors,
        "warnings": warnings,
    }


def _skill_summary_from_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    root_uri = entry.get("uri", "")
    name = entry.get("name") or _skill_name_from_uri(root_uri)
    return _skill_summary_from_meta(name, root_uri, _parse_abstract_meta(entry.get("abstract", "")))


def _skill_summary_from_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
    hit_uri = hit.get("uri", "")
    root_uri = _skill_root_from_hit_uri(hit_uri)
    name = _skill_name_from_uri(root_uri) if root_uri else _skill_name_from_uri(hit_uri)
    summary = _skill_summary_from_meta(
        name, root_uri or hit_uri, _parse_abstract_meta(hit.get("abstract", ""))
    )
    summary["score"] = hit.get("score", 0.0)
    summary["match_reason"] = hit.get("match_reason", "")
    summary["level"] = hit.get("level", 0)
    summary["abstract"] = hit.get("abstract", "")
    return summary


def _skill_root_from_hit_uri(hit_uri: str) -> str:
    """Strip a trailing chunk filename (e.g. ``.abstract.md``) from a hit URI.

    Search results point at the indexed chunk file (typically ``.abstract.md``
    sitting alongside ``SKILL.md`` inside the skill directory).  The summary
    consumed by the CLI expects the URI to identify the skill directory itself,
    so we trim a single trailing filename when one is present.
    """
    if not hit_uri:
        return ""
    trimmed = hit_uri.rstrip("/")
    last_segment = trimmed.rsplit("/", 1)[-1]
    if "." in last_segment:
        parent = trimmed.rsplit("/", 1)[0]
        if parent:
            return parent
    return trimmed


async def _require_skill(service, ctx: RequestContext, skill_name: str, target_uri: Optional[str] = None) -> str:
    if target_uri:
        resolved_uri = resolve_path_variables(target_uri)
        root_uri = _skill_root_uri(ctx, skill_name, resolved_uri)
        try:
            stat = await service.fs.stat(root_uri, ctx=ctx)
            if stat and stat.get("isDir", False):
                return root_uri
        except NotFoundError:
            pass
        except Exception as exc:
            raise NotFoundError(root_uri, "skill") from exc

    user_root_uri = _skill_root_uri(ctx, skill_name)
    try:
        stat = await service.fs.stat(user_root_uri, ctx=ctx)
        if stat and stat.get("isDir", False):
            return user_root_uri
    except NotFoundError:
        pass
    except Exception as exc:
        raise NotFoundError(user_root_uri, "skill") from exc

    agent_root_uri = _skill_root_uri(ctx, skill_name, "viking://agent/skills")
    try:
        stat = await service.fs.stat(agent_root_uri, ctx=ctx)
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


def _parse_skill_data(
    service,
    data: Any,
    *,
    allow_local_path_resolution: bool,
    source_path_hint: Optional[str] = None,
) -> Dict[str, Any]:
    skill_processor = getattr(service.resources, "_skill_processor", None)
    if skill_processor is None:
        raise RuntimeError("SkillProcessor is required for skill validation")
    skill_dict, _, _, _ = skill_processor._parse_skill(  # noqa: SLF001 - keep parser authority centralized.
        data,
        allow_local_path_resolution=allow_local_path_resolution,
        source_path_hint=source_path_hint,
    )
    return skill_dict


def _validate_data_matches_name(
    service,
    data: Any,
    skill_name: str,
    *,
    allow_local_path_resolution: bool,
    source_path_hint: Optional[str] = None,
) -> Dict[str, Any]:
    parsed = _parse_skill_data(
        service,
        data,
        allow_local_path_resolution=allow_local_path_resolution,
        source_path_hint=source_path_hint,
    )
    expected_name = _validate_skill_name(skill_name)
    if parsed.get("name") != expected_name:
        raise InvalidArgumentError(
            f"Skill name mismatch: path name is '{expected_name}', content name is '{parsed.get('name')}'",
            details={"expected": expected_name, "actual": parsed.get("name")},
        )
    return parsed


async def _restore_skill_privacy(
    service,
    ctx: RequestContext,
    skill_name: str,
    previous_privacy: Optional[UserPrivacyConfigVersion],
) -> None:
    privacy = service.privacy_configs
    if privacy is None:
        return
    if previous_privacy is None:
        await privacy.delete(ctx, "skill", skill_name)
        return
    await privacy.activate_version(
        ctx,
        "skill",
        skill_name,
        previous_privacy.version,
        updated_by=ctx.user.user_id,
    )


@router.get("")
async def list_skills(
    node_limit: int = 1000,
    target_uri: Optional[str] = None,
    _ctx: RequestContext = Depends(get_request_context),
):
    """List installed agent skills."""
    service = get_service()
    if target_uri:
        resolved_uri = resolve_path_variables(target_uri)
        skills = await _list_skills_from_root(service, _ctx, resolved_uri)
        return Response(
            status="ok", result={"root_uri": resolved_uri, "skills": skills, "total": len(skills)}
        )
    else:
        user_skills = await _list_skills_from_root(
            service, _ctx, f"{canonical_user_root(_ctx)}/skills"
        )
        agent_skills = await _list_skills_from_root(service, _ctx, "viking://agent/skills")
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
        resolved_uri = resolve_path_variables(target_uri)
        execution = await run_operation(
            operation="skills.find",
            telemetry=request.telemetry,
            fn=lambda: service.search.find(
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
        hits = [_skill_summary_from_hit(hit) for hit in result_dict.get("skills", [])]
        return Response(
            status="ok",
            result={"root_uri": resolved_uri, "skills": hits, "total": len(hits)},
            telemetry=execution.telemetry,
        ).model_dump(exclude_none=True)
    else:
        user_root = f"{canonical_user_root(_ctx)}/skills"
        agent_root = "viking://agent/skills"

        user_execution, agent_execution = await asyncio.gather(
            run_operation(
                operation="skills.find",
                telemetry=request.telemetry,
                fn=lambda: service.search.find(
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
                fn=lambda: service.search.find(
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
        user_result_dict = user_result.to_dict() if hasattr(user_result, "to_dict") else dict(user_result or {})
        user_hits = [_skill_summary_from_hit(hit) for hit in user_result_dict.get("skills", [])]

        agent_result = agent_execution.result
        agent_result_dict = agent_result.to_dict() if hasattr(agent_result, "to_dict") else dict(agent_result or {})
        agent_hits = [_skill_summary_from_hit(hit) for hit in agent_result_dict.get("skills", [])]

        merged_hits = [*user_hits, *agent_hits]
        # Sort merged hits by score descending if available
        if merged_hits and "score" in merged_hits[0]:
            merged_hits.sort(key=lambda x: x.get("score", 0), reverse=True)

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
    service = get_service()
    result = _validate_skill_format(
        service,
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
    abstract = await service.fs.abstract(root_uri, ctx=_ctx)
    result = _skill_summary_from_meta(skill_name, root_uri, _parse_abstract_meta(abstract))
    if level is None or level == 0:
        result["abstract"] = abstract
    if level is None or level == 1:
        result["overview"] = await service.fs.overview(root_uri, ctx=_ctx)
    if level == 2 or include_content is True or (level is None and include_content is not False):
        result["content"] = await service.fs.read(_skill_md_uri(root_uri), ctx=_ctx)
    if include_files:
        entries = await _list_skill_files(service, _ctx, root_uri)
        result["files"] = [
            {
                "name": entry.get("name") or _skill_name_from_uri(entry.get("uri", "")),
                "uri": entry.get("uri", ""),
                "path": _relative_skill_path(root_uri, entry.get("uri", "")),
                "is_dir": entry.get("isDir", False),
                "kind": _skill_file_kind(
                    _relative_skill_path(root_uri, entry.get("uri", "")),
                    entry.get("isDir", False),
                ),
            }
            for entry in entries
            if isinstance(entry, dict)
            and _relative_skill_path(root_uri, entry.get("uri", "")) != SOURCE_METADATA_FILENAME
        ]
    if include_source:
        result["source"] = await read_skill_source_metadata(service, _ctx, root_uri)
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
        store = TempUploadStore.build(http_request.app.state.config)
        resolved = await store.resolve_for_consume(request.temp_file_id, _ctx)
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

    source_path_hint = resolved.original_filename if resolved else None
    store = TempUploadStore.build(http_request.app.state.config) if resolved else None

    async def _update() -> Dict[str, Any]:
        # Derive backup root from the actual skill root URI to keep backup in the same scope.
        skill_root_parent = root_uri.rsplit("/", 1)[0]
        backup_uri = f"{skill_root_parent}/.{skill_name}.update-backup-{uuid.uuid4().hex}"
        backup_created = False
        privacy_update_attempted = False
        previous_privacy = None
        preparation = None
        privacy = service.privacy_configs
        try:
            if privacy is not None:
                previous_privacy = await privacy.get_current(_ctx, "skill", skill_name)
            preparation = await service.resources._skill_processor.prepare_skill_processing(  # noqa: SLF001
                data,
                ctx=_ctx,
                allow_local_path_resolution=allow_local_path_resolution,
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
            await service.fs.mv(root_uri, backup_uri, ctx=_ctx)
            backup_created = True
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
            )
            await persist_skill_source_metadata(service, _ctx, result, source_metadata)
            privacy_update_attempted = True
            await service.resources._skill_processor.apply_skill_privacy(  # noqa: SLF001
                preparation.skill_dict,
                preparation.privacy_values,
                _ctx,
                change_reason="auto-extracted from update_skill",
                delete_if_empty=True,
            )
        except Exception:
            if backup_created:
                try:
                    await service.fs.rm(root_uri, ctx=_ctx, recursive=True)
                except Exception:
                    pass
                try:
                    await service.fs.mv(backup_uri, root_uri, ctx=_ctx)
                except Exception:
                    pass
            if privacy_update_attempted:
                try:
                    await _restore_skill_privacy(service, _ctx, skill_name, previous_privacy)
                except Exception:
                    pass
            if resolved and store:
                await store.mark_failed(resolved, _ctx)
            raise
        else:
            if backup_created:
                await service.fs.rm(backup_uri, ctx=_ctx, recursive=True)
            if resolved and store:
                await store.mark_consumed(resolved, _ctx)
            result["action"] = "update"
            return result
        finally:
            if preparation and preparation.cleanup_path:
                shutil.rmtree(preparation.cleanup_path, ignore_errors=True)
            if resolved:
                await resolved.cleanup()

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
