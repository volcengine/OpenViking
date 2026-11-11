# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Skill Processor for OpenViking.

Handles skill parsing, LLM generation, and storage operations.
"""

import asyncio
import os
import re
import shutil
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import yaml

from openviking.config.vlm import VLMHandle, VLMResolver
from openviking.core.mcp_converter import is_mcp_format, mcp_to_skill
from openviking.core.namespace import canonical_user_root
from openviking.core.skill_loader import SkillLoader
from openviking.privacy.models import UserPrivacyConfigVersion
from openviking.privacy.reference import parse_privacy_field_reference
from openviking.privacy.rules import PrivacyMatch, find_privacy_matches
from openviking.privacy.sanitizer import (
    DEFAULT_SANITIZER_MAX_REPLACEMENTS,
    PrivacyReplacement,
    PrivacySanitizer,
)
from openviking.privacy.service import UserPrivacyConfigService
from openviking.privacy.skill_extractor import (
    extract_skill_privacy_values,
    find_skill_privacy_matches,
)
from openviking.privacy.skill_placeholder import build_placeholder
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import deny_direct_local_skill_input
from openviking.server.user_config import ResolvedPrivacyConfig
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.viking_fs import VikingFS
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking.telemetry import get_current_telemetry, register_telemetry
from openviking.utils.path_safety import safe_join_viking_uri
from openviking.utils.zip_safe import safe_extract_zip
from openviking_cli.exceptions import ConflictError, InvalidArgumentError, ResourceExhaustedError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

MAX_SKILL_NAME_LENGTH = 64


@dataclass
class SkillPrivacyPreparation:
    settings: ResolvedPrivacyConfig
    matches: list[PrivacyMatch]


@dataclass
class SkillProcessingPreparation:
    skill_dict: Dict[str, Any]
    auxiliary_files: List[Path]
    base_path: Optional[Path]
    cleanup_path: Optional[Path]
    privacy: SkillPrivacyPreparation | None


def validate_skill_name(name: Any) -> str:
    """Validate and normalize an Agent Skill name for storage/API addressing."""
    if name is None:
        raise InvalidArgumentError("Skill must have 'name' field", details={"field": "name"})
    if not isinstance(name, str):
        raise InvalidArgumentError(
            "Skill 'name' must be a non-empty string",
            details={"field": "name"},
        )

    normalized = name.strip()
    if not normalized:
        raise InvalidArgumentError(
            "Skill 'name' must be a non-empty string",
            details={"field": "name"},
        )
    if len(normalized) > MAX_SKILL_NAME_LENGTH:
        raise InvalidArgumentError(
            f"Skill name cannot exceed {MAX_SKILL_NAME_LENGTH} characters",
            details={
                "field": "name",
                "max_length": MAX_SKILL_NAME_LENGTH,
                "actual_length": len(normalized),
            },
        )
    if not all(ch.isascii() and (ch.isalnum() or ch in {"_", "-"}) for ch in normalized):
        raise InvalidArgumentError(
            f"Invalid skill name: {name}",
            details={
                "field": "name",
                "reason": "skill name may only contain ASCII letters, numbers, underscores, and hyphens",
            },
        )
    return normalized


class SkillProcessor:
    """
    Handles skill processing and storage.

    Workflow:
    1. Parse skill data (directory, file, string, or dict)
    2. Use skill metadata as L0 and skill instructions as L1
    3. Write skill content to VikingFS
    4. Write auxiliary files
    5. Index to vector store
    """

    def __init__(
        self,
        vikingdb: VikingDBManager,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
        vlm_resolver: Optional[VLMResolver] = None,
        privacy_sanitizer: Optional[PrivacySanitizer] = None,
    ):
        """Initialize skill processor."""
        self.vikingdb = vikingdb
        self._privacy_config_service = privacy_config_service
        self._privacy_sanitizer = privacy_sanitizer or (
            PrivacySanitizer(privacy_config_service)
            if isinstance(privacy_config_service, UserPrivacyConfigService)
            else None
        )
        self._vlm_resolver = vlm_resolver

    async def process_skill(
        self,
        data: Any,
        viking_fs: VikingFS,
        ctx: RequestContext,
        allow_local_path_resolution: bool = True,
        source_path_hint: Optional[str] = None,
        apply_privacy: bool = True,
        privacy_change_reason: str = "auto-extracted from add_skill",
        target_uri: Optional[str] = None,
        source_metadata: Optional[Dict[str, Any]] = None,
        owner_lease_ref: Optional[Dict[str, Any]] = None,
        lease_ref: Any = None,
        on_package_written: Optional[Callable[[str, Any], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """
        Process and store a skill.

        Args:
            data: Skill data (directory, file path, string, or dict)
            viking_fs: VikingFS instance for storage
            user: Username for context
            target_uri: Optional root URI override (e.g. ``viking://agent/skills``).
                When omitted, defaults to ``{user_root}/skills``.
            on_package_written: Called with the package URI and lease before queue handoff;
                failures abort processing while the package lease is still held.

        Returns:
            Processing result with status and metadata
        """

        if data is None:
            raise ValueError("Skill data cannot be None")

        parse_start = time.perf_counter()
        preparation = await self.prepare_skill_processing(
            data,
            ctx=ctx,
            allow_local_path_resolution=allow_local_path_resolution,
            source_path_hint=source_path_hint,
        )
        telemetry = get_current_telemetry()
        telemetry.set(
            "skill.parse.duration_ms", round((time.perf_counter() - parse_start) * 1000, 3)
        )
        return await self.process_prepared_skill(
            preparation,
            viking_fs=viking_fs,
            ctx=ctx,
            apply_privacy=apply_privacy,
            privacy_change_reason=privacy_change_reason,
            target_uri=target_uri,
            source_metadata=source_metadata,
            owner_lease_ref=owner_lease_ref,
            lease_ref=lease_ref,
            on_package_written=on_package_written,
        )

    async def process_prepared_skill(
        self,
        preparation: SkillProcessingPreparation,
        viking_fs: VikingFS,
        ctx: RequestContext,
        *,
        apply_privacy: bool = True,
        privacy_change_reason: str = "auto-extracted from add_skill",
        target_uri: Optional[str] = None,
        source_metadata: Optional[Dict[str, Any]] = None,
        owner_lease_ref: Optional[Dict[str, Any]] = None,
        lease_ref: Any = None,
        on_package_written: Optional[Callable[[str, Any], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Store a prepared package and apply privacy changes under its write lease."""
        cleanup_path = preparation.cleanup_path
        skill_dict = preparation.skill_dict
        auxiliary_files = preparation.auxiliary_files
        base_path = preparation.base_path
        telemetry = get_current_telemetry()
        lease = None
        # Training supplies its enclosing tree lease. Acquire a separate package
        # reference so background indexing never takes ownership of that lease.
        if owner_lease_ref is None:
            owner_lease_ref = lease_ref
        try:
            effective_root_uri = self._resolve_skill_root_uri(ctx, target_uri)
            skill_dir_uri = f"{effective_root_uri}/{skill_dict['name']}"

            async def acquire_package_lock() -> None:
                nonlocal lease
                lease = await viking_fs._async_agfs.pathlock_acquire_tree(
                    viking_fs._uri_to_path(skill_dir_uri, ctx=ctx),
                    **({"owner_lease_ref": owner_lease_ref} if owner_lease_ref is not None else {}),
                )

            await run_to_completion(acquire_package_lock)
            # Privacy changes finish under the package lease even if installation is cancelled.
            if apply_privacy:
                skill_dict = await run_to_completion(
                    lambda: self.apply_skill_privacy(
                        skill_dict,
                        preparation.privacy,
                        ctx,
                        change_reason=privacy_change_reason,
                    )
                )
            skill_abstract = self._build_skill_abstract(skill_dict)

            write_start = time.perf_counter()
            await run_to_completion(
                lambda: self._write_skill_content(
                    viking_fs=viking_fs,
                    skill_dict=skill_dict,
                    skill_dir_uri=skill_dir_uri,
                    abstract=skill_abstract,
                    overview=skill_dict.get("content", ""),
                    ctx=ctx,
                    lease_ref=lease,
                )
            )

            await run_to_completion(
                lambda: self._write_auxiliary_files(
                    viking_fs=viking_fs,
                    auxiliary_files=auxiliary_files,
                    base_path=base_path,
                    skill_dir_uri=skill_dir_uri,
                    ctx=ctx,
                    lease_ref=lease,
                )
            )
            telemetry.set(
                "skill.write.duration_ms", round((time.perf_counter() - write_start) * 1000, 3)
            )

            result = {
                "status": "success",
                "root_uri": skill_dir_uri,
                "uri": skill_dir_uri,
                "name": skill_dict["name"],
                "auxiliary_files": len(auxiliary_files),
            }
            if source_metadata:
                from openviking.server.skill_source_metadata import write_skill_source_metadata

                await run_to_completion(
                    lambda: write_skill_source_metadata(
                        viking_fs, ctx, result, source_metadata, lease_ref=lease
                    )
                )
            if on_package_written is not None:
                await run_to_completion(lambda: on_package_written(skill_dir_uri, lease))
            index_start = time.perf_counter()

            async def enqueue_package() -> None:
                nonlocal lease
                await self._enqueue_skill_package(
                    skill_dir_uri,
                    viking_fs,
                    ctx,
                    lease,
                    source_path=skill_dict.get("source_path", ""),
                )
                lease = None  # The semantic worker owns the handed-off lease.

            await run_to_completion(enqueue_package)
            telemetry.set(
                "skill.index.duration_ms", round((time.perf_counter() - index_start) * 1000, 3)
            )
            return result
        finally:
            if lease is not None:
                await run_to_completion(lambda: viking_fs._async_agfs.pathlock_release(lease))
            if cleanup_path:
                shutil.rmtree(cleanup_path, ignore_errors=True)

    async def prepare_skill_processing(
        self,
        data: Any,
        ctx: RequestContext,
        allow_local_path_resolution: bool = True,
        source_path_hint: Optional[str] = None,
    ) -> SkillProcessingPreparation:
        skill_dict, auxiliary_files, base_path, cleanup_path = self._parse_skill(
            data,
            allow_local_path_resolution=allow_local_path_resolution,
            source_path_hint=source_path_hint,
        )
        try:
            self._validate_skill_dict(skill_dict)
            privacy = await self.prepare_skill_privacy(skill_dict, ctx)
            return SkillProcessingPreparation(
                skill_dict=skill_dict,
                auxiliary_files=auxiliary_files,
                base_path=base_path,
                cleanup_path=cleanup_path,
                privacy=privacy,
            )
        except Exception:
            if cleanup_path:
                shutil.rmtree(cleanup_path, ignore_errors=True)
            raise

    def _parse_skill(
        self,
        data: Any,
        allow_local_path_resolution: bool = True,
        source_path_hint: Optional[str] = None,
    ) -> tuple[Dict[str, Any], List[Path], Optional[Path], Optional[Path]]:
        """Parse skill data from various formats."""
        if data is None:
            raise ValueError("Skill data cannot be None")

        auxiliary_files = []
        base_path = None
        cleanup_path = None

        try:
            if isinstance(data, str):
                if allow_local_path_resolution:
                    path_obj = Path(data)
                    if path_obj.exists():
                        data, cleanup_path = self._resolve_skill_path(path_obj)
                else:
                    deny_direct_local_skill_input(data)

            if isinstance(data, Path):
                data, cleanup_path = self._resolve_skill_path(data)

            if isinstance(data, Path):
                if data.is_dir():
                    # Directory containing SKILL.md
                    skill_file = data / "SKILL.md"
                    if not skill_file.exists():
                        raise ValueError(f"SKILL.md not found in {data}")

                    skill_dict = SkillLoader.load(str(skill_file))
                    base_path = data
                    for item in data.rglob("*"):
                        # Exclude only the top-level SKILL.md (the skill body);
                        # nested SKILL.md files belong to sub-skills and must be kept.
                        if item.is_file() and item != skill_file:
                            auxiliary_files.append(item)
                else:
                    # Single skill markdown file
                    skill_dict = SkillLoader.load(str(data))
            elif isinstance(data, str):
                # Raw SKILL.md content
                skill_dict = SkillLoader.parse(data)
            elif isinstance(data, dict):
                if is_mcp_format(data):
                    skill_dict = mcp_to_skill(data)
                else:
                    skill_dict = data
            else:
                raise ValueError(f"Unsupported data type: {type(data)}")

            skill_dict = self._normalize_skill_dict(skill_dict)
            if source_path_hint:
                skill_dict["source_path"] = source_path_hint
            self._validate_skill_dict(skill_dict)
            return skill_dict, auxiliary_files, base_path, cleanup_path
        except Exception:
            if cleanup_path:
                shutil.rmtree(cleanup_path, ignore_errors=True)
            raise

    @staticmethod
    def _resolve_skill_path(path_obj: Path) -> tuple[Path, Optional[Path]]:
        """Resolve uploaded/local skill path, including ZIP archives."""
        if path_obj.is_file() and (
            zipfile.is_zipfile(path_obj) or path_obj.suffix.lower() == ".zip"
        ):
            temp_dir = Path(tempfile.mkdtemp())
            try:
                with zipfile.ZipFile(path_obj, "r") as zipf:
                    safe_extract_zip(zipf, temp_dir)
            except zipfile.BadZipFile as exc:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise InvalidArgumentError(
                    f"Invalid skill ZIP archive: {path_obj}",
                    details={"path": str(path_obj), "expected": "zip"},
                ) from exc

            if not (temp_dir / "SKILL.md").exists():
                children = [child for child in temp_dir.iterdir() if child.is_dir()]
                if len(children) == 1 and (children[0] / "SKILL.md").exists():
                    return children[0], temp_dir
            return temp_dir, temp_dir

        return path_obj, None

    @staticmethod
    def _normalize_list_field(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            return value
        if isinstance(value, (tuple, set)):
            return list(value)
        return [value]

    @staticmethod
    def _normalize_skill_dict(skill_dict: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(skill_dict)

        allowed_tools = normalized.get("allowed_tools")
        if not allowed_tools:
            allowed_tools = normalized.get("allowed-tools")
        if allowed_tools is not None:
            normalized["allowed_tools"] = SkillProcessor._normalize_list_field(allowed_tools)
        normalized.pop("allowed-tools", None)

        tags = normalized.get("tags")
        if tags is not None:
            normalized["tags"] = SkillProcessor._normalize_list_field(tags)

        return normalized

    @staticmethod
    def _resolve_skill_root_uri(ctx: RequestContext, target_uri: Optional[str]) -> str:
        """Resolve the skill storage root URI.

        Defaults to the per-user private skills root.  Callers may pass
        ``viking://agent/skills`` (or a future agent-scope subpath) to publish
        the skill under the account-global ``/agent`` scope.
        """
        if target_uri:
            normalized = target_uri.rstrip("/")
            if normalized.startswith("viking://agent/skills"):
                return "viking://agent/skills"
            user_root = f"{canonical_user_root(ctx)}/skills"
            if normalized == user_root.rstrip("/"):
                return user_root
            raise InvalidArgumentError(
                f"Unsupported skill root URI: {target_uri}; use {user_root} or viking://agent/skills",
                details={
                    "field": "target_uri",
                    "allowed": [
                        f"{canonical_user_root(ctx)}/skills",
                        "viking://agent/skills",
                    ],
                },
            )
        return f"{canonical_user_root(ctx)}/skills"

    @staticmethod
    def _validate_skill_dict(skill_dict: Dict[str, Any]) -> None:
        """Validate normalized skill metadata before storage/indexing."""
        skill_dict["name"] = validate_skill_name(skill_dict.get("name"))

    @staticmethod
    def _build_skill_abstract(skill_dict: Dict[str, Any]) -> str:
        """Build the L0 skill abstract from normalized SKILL.md header metadata."""
        abstract_meta: Dict[str, Any] = {
            "name": skill_dict["name"],
            "description": skill_dict.get("description", ""),
        }

        tags = skill_dict.get("tags")
        if tags:
            abstract_meta["tags"] = tags

        allowed_tools = skill_dict.get("allowed_tools") or skill_dict.get("allowed-tools")
        if allowed_tools:
            abstract_meta["allowed_tools"] = allowed_tools

        return yaml.safe_dump(abstract_meta, allow_unicode=True, sort_keys=False).strip()

    async def prepare_skill_privacy(
        self, skill_dict: Dict[str, Any], ctx: RequestContext
    ) -> SkillPrivacyPreparation | None:
        if not self._privacy_config_service:
            return None

        settings = await self._privacy_config_service.get_settings(ctx)
        if not settings.discovery_enabled:
            return SkillPrivacyPreparation(settings, [])

        content = skill_dict.get("content", "")
        method = (
            os.environ.get("OPENVIKING_SKILL_PRIVACY_EXTRACTION_METHOD", "rule").strip().lower()
        )
        if method not in {"rule", "vlm"}:
            raise InvalidArgumentError(
                "OPENVIKING_SKILL_PRIVACY_EXTRACTION_METHOD must be 'rule' or 'vlm'"
            )
        if method == "rule":
            matches = await asyncio.to_thread(find_privacy_matches, content, settings=settings)
        else:
            if self._vlm_resolver is None:
                raise RuntimeError("SkillProcessor requires a VLM resolver for account-owned work")
            skill_uri = f"{canonical_user_root(ctx)}/skills/{skill_dict['name']}/"
            protected_content = content
            if self._privacy_sanitizer is not None:
                protected_content = (
                    await self._privacy_sanitizer.sanitize(
                        content, ctx=ctx, uri=skill_uri, settings=settings
                    )
                ).content
            extraction = await extract_skill_privacy_values(
                skill_name=skill_dict["name"],
                skill_description=skill_dict.get("description", ""),
                content=protected_content,
                vlm=await self._vlm_resolver.get_vlm(ctx.account_id),
                settings=settings,
            )
            matches = await asyncio.to_thread(
                find_skill_privacy_matches, content, extraction.values.values()
            )
        return SkillPrivacyPreparation(settings, matches)

    @staticmethod
    def _referenced_privacy_fields(skill_dict: Dict[str, Any], ctx: RequestContext) -> set[str]:
        """Find references to this Skill's own config without resolving secrets."""
        skill_name = skill_dict["name"]
        legacy_prefix = f"{{{{ov_privacy:skill:{skill_name}:"
        fields: set[str] = set()
        for text in (skill_dict.get("content", ""), skill_dict.get("description", "")):
            for placeholder in re.findall(r"\{\{[^{}\r\n]*\}\}", text or ""):
                reference = parse_privacy_field_reference(placeholder)
                if reference is not None:
                    if (
                        reference.owner_user_id == ctx.user.user_id
                        and reference.category == "skill"
                        and reference.target_key == skill_name
                    ):
                        fields.add(reference.field)
                elif placeholder.startswith(legacy_prefix):
                    field_name = placeholder[len(legacy_prefix) : -2]
                    if field_name.startswith(":"):
                        fields.add(unquote(field_name[1:]))
                    elif field_name and ":" not in field_name:
                        fields.add(field_name)
        return fields

    async def apply_skill_privacy(
        self,
        skill_dict: Dict[str, Any],
        preparation: SkillPrivacyPreparation | None,
        ctx: RequestContext,
        *,
        change_reason: str,
        owner_lease_ref: Optional[Dict[str, Any]] = None,
        previous_versions: Iterable[UserPrivacyConfigVersion] = (),
    ) -> Dict[str, Any]:
        if (
            not self._privacy_config_service
            or preparation is None
            or preparation.settings.mode == "off"
        ):
            return skill_dict

        async with self._privacy_config_service._config_lock(
            ctx, "skill", skill_dict["name"], owner_lease_ref=owner_lease_ref
        ) as lease:
            current = await self._privacy_config_service.get_current(
                ctx, "skill", skill_dict["name"]
            )
            matcher = (
                await self._privacy_sanitizer.prepare_matcher(
                    ctx,
                    scope=("skill", skill_dict["name"]),
                    settings=preparation.settings,
                    uri=f"{canonical_user_root(ctx)}/skills/{skill_dict['name']}/SKILL.md",
                    snapshot=(current or UserPrivacyConfigVersion()) if lease is not None else None,
                )
                if self._privacy_sanitizer is not None
                else lambda _: []
            )
            values: dict[str, str] = {}
            references: dict[str, str] = {}
            existing_fields = set(current.values) if current is not None else set()
            reserved_fields = existing_fields | self._referenced_privacy_fields(skill_dict, ctx)
            content = skill_dict.get("content", "")
            registered = matcher(content)
            if current is not None and preparation.settings.discovery_enabled:
                for field, value in sorted(current.values.items()):
                    if isinstance(value, str) and value:
                        references.setdefault(value, build_placeholder(skill_dict["name"], field))
            for index, replacement in enumerate(registered):
                field_reference = parse_privacy_field_reference(replacement.reference)
                if (
                    field_reference is not None
                    and field_reference.owner_user_id == ctx.user.user_id
                    and field_reference.category == "skill"
                    and field_reference.target_key == skill_dict["name"]
                ):
                    reserved_fields.add(field_reference.field)
                    registered[index] = PrivacyReplacement(
                        replacement.start,
                        replacement.end,
                        build_placeholder(skill_dict["name"], field_reference.field),
                    )
            replacements = list(registered)
            index = 0
            for match in preparation.matches:
                while index < len(registered) and registered[index].end <= match.start:
                    index += 1
                # Registered fields retain priority over overlapping discoveries.
                if index < len(registered) and registered[index].start < match.end:
                    continue
                if len(replacements) >= DEFAULT_SANITIZER_MAX_REPLACEMENTS:
                    raise ResourceExhaustedError(
                        "Content exceeds the Privacy sanitizer replacement limit"
                    )
                value = content[match.start : match.end]
                reference = references.get(value)
                if reference is None:
                    for _ in range(8):
                        key = token_urlsafe(6)
                        if key not in values and key not in reserved_fields:
                            break
                    else:
                        raise ResourceExhaustedError(
                            "Could not allocate unique Skill privacy field IDs"
                        )
                    values[key] = value
                    reference = build_placeholder(skill_dict["name"], key)
                    references[value] = reference
                replacements.append(PrivacyReplacement(match.start, match.end, reference))
            chunks: list[str] = []
            cursor = 0
            for replacement in sorted(replacements, key=lambda item: item.start):
                chunks.extend((content[cursor : replacement.start], replacement.reference))
                cursor = replacement.end
            chunks.append(content[cursor:])
            rewritten = "".join(chunks)
            if rewritten != content:
                skill_dict = {**deepcopy(skill_dict), "content": rewritten}
            if not preparation.settings.discovery_enabled:
                return skill_dict
            if values:
                if current is not None:
                    new_values = set(values.values())
                    for version in previous_versions:
                        for field, previous_value in version.values.items():
                            if (
                                field in current.values
                                and isinstance(previous_value, str)
                                and previous_value in new_values
                                and current.values[field] != previous_value
                            ):
                                raise ConflictError(
                                    "Skill update contains a previous value of a rotated field; "
                                    "use its current value or an explicit field reference.",
                                    resource=f"skill/{skill_dict['name']}/{field}",
                                )
                await self._privacy_config_service.upsert(
                    ctx=ctx,
                    category="skill",
                    target_key=skill_dict["name"],
                    values=values,
                    updated_by=ctx.user.user_id,
                    change_reason=change_reason,
                    **({"owner_lease_ref": lease} if lease is not None else {}),
                    preserve_fields=existing_fields,
                )
            return skill_dict

    async def sanitize_skill_privacy(
        self, skill_dict: Dict[str, Any], ctx: RequestContext
    ) -> Dict[str, Any]:
        preparation = await self.prepare_skill_privacy(skill_dict, ctx)
        return await self.apply_skill_privacy(
            skill_dict,
            preparation,
            ctx,
            change_reason="auto-extracted from add_skill",
        )

    async def _generate_overview(self, skill_dict: Dict[str, Any], vlm: VLMHandle) -> str:
        """Generate L1 overview using VLM."""
        from openviking.prompts import render_prompt

        prompt = render_prompt(
            "skill.overview_generation",
            {
                "skill_name": skill_dict["name"],
                "skill_description": skill_dict.get("description", ""),
                "skill_content": skill_dict.get("content", ""),
            },
        )
        return await vlm.get_completion_async(prompt)

    async def _write_skill_content(
        self,
        viking_fs: VikingFS,
        skill_dict: Dict[str, Any],
        skill_dir_uri: str,
        abstract: str,
        overview: str,
        ctx: RequestContext,
        lease_ref: Dict[str, Any],
    ):
        """Write main skill content to VikingFS."""
        from openviking.storage.abstract_overview import write_abstract_overview

        skill_md_uri = f"{skill_dir_uri}/SKILL.md"
        serialized = SkillLoader.to_skill_md(skill_dict)
        await viking_fs.write_file(
            skill_md_uri,
            serialized,
            ctx=ctx,
            lease_ref=lease_ref,
        )
        await write_abstract_overview(
            viking_fs=viking_fs,
            dir_uri=skill_dir_uri,
            abstract=abstract,
            overview=overview,
            ctx=ctx,
            lock=lease_ref,
            is_stale=lambda: False,
            metadata={"generated_by": {"component": "SkillProcessor", "trigger": "skill_ingest"}},
        )

    async def _write_auxiliary_files(
        self,
        viking_fs: VikingFS,
        auxiliary_files: List[Path],
        base_path: Optional[Path],
        skill_dir_uri: str,
        ctx: RequestContext,
        lease_ref: Optional[Dict[str, Any]] = None,
    ):
        """Write auxiliary files to VikingFS."""
        for aux_file in auxiliary_files:
            if base_path:
                rel_path = aux_file.relative_to(base_path)
                rel_uri_path = rel_path.as_posix()
            else:
                rel_uri_path = aux_file.name
            aux_uri = safe_join_viking_uri(skill_dir_uri, rel_uri_path)

            file_bytes = aux_file.read_bytes()
            try:
                file_bytes.decode("utf-8")
                is_text = True
            except UnicodeDecodeError:
                is_text = False

            if is_text:
                await viking_fs.write_file(
                    aux_uri,
                    file_bytes.decode("utf-8"),
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
            else:
                await viking_fs.write_file_bytes(
                    aux_uri,
                    file_bytes,
                    ctx=ctx,
                    lease_ref=lease_ref,
                )

    async def _enqueue_skill_package(
        self,
        uri: str,
        viking_fs: VikingFS,
        ctx: RequestContext,
        lease: Dict[str, Any],
        *,
        source_path: str = "",
    ) -> None:
        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.semantic_msg import SemanticMsg

        telemetry = get_current_telemetry()
        register_telemetry(telemetry)
        lock_guard = PathLockGuard(viking_fs._async_agfs, lease)
        msg = SemanticMsg(
            uri=uri,
            context_type="skill",
            recursive=True,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            group_ids=ctx.group_ids,
            role=str(ctx.role),
            telemetry_id=telemetry.telemetry_id,
            lock_handoff=await lock_guard.to_handoff(),
            generation_trigger="skill_ingest",
            propagate_to_parent=False,
            source={"path": source_path},
        )
        try:
            queue_manager = get_queue_manager()
            queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
            await lock_guard.handoff_and_dispatch(msg.lock_handoff, lambda: queue.enqueue(msg))
        except BaseException:
            if lock_guard.lease is not None and lock_guard.lease is not lease:
                lease.clear()
                lease.update(lock_guard.lease)
            raise
