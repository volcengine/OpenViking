# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Skill Processor for OpenViking.

Handles skill parsing, LLM generation, and storage operations.
"""

import shutil
import tempfile
import time
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from openviking.core.context import Context, ContextType, Vectorize
from openviking.core.mcp_converter import is_mcp_format, mcp_to_skill
from openviking.core.namespace import canonical_user_root, user_space_fragment
from openviking.core.skill_loader import SkillLoader
from openviking.privacy import (
    UserPrivacyConfigService,
    extract_skill_privacy_values,
)
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import deny_direct_local_skill_input
from openviking.storage import VikingDBManager
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.path_safety import safe_join_viking_uri
from openviking.utils.zip_safe import safe_extract_zip
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

MAX_SKILL_NAME_LENGTH = 64


@dataclass
class SkillProcessingPreparation:
    skill_dict: Dict[str, Any]
    auxiliary_files: List[Path]
    base_path: Optional[Path]
    cleanup_path: Optional[Path]
    privacy_values: Dict[str, str]


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
    2. Generate L1 overview using VLM
    3. Write skill content to VikingFS
    4. Write auxiliary files
    5. Index to vector store
    """

    def __init__(
        self,
        vikingdb: VikingDBManager,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
    ):
        """Initialize skill processor."""
        self.vikingdb = vikingdb
        self._privacy_config_service = privacy_config_service

    async def process_skill(
        self,
        data: Any,
        viking_fs: VikingFS,
        ctx: RequestContext,
        allow_local_path_resolution: bool = True,
        source_path_hint: Optional[str] = None,
        apply_privacy: bool = True,
        privacy_change_reason: str = "auto-extracted from add_skill",
    ) -> Dict[str, Any]:
        """
        Process and store a skill.

        Args:
            data: Skill data (directory, file path, string, or dict)
            viking_fs: VikingFS instance for storage
            user: Username for context

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
        )

    async def process_prepared_skill(
        self,
        preparation: SkillProcessingPreparation,
        viking_fs: VikingFS,
        ctx: RequestContext,
        *,
        apply_privacy: bool = True,
        privacy_change_reason: str = "auto-extracted from add_skill",
    ) -> Dict[str, Any]:
        config = get_openviking_config()
        cleanup_path = preparation.cleanup_path
        skill_dict = preparation.skill_dict
        auxiliary_files = preparation.auxiliary_files
        base_path = preparation.base_path
        telemetry = get_current_telemetry()
        try:
            if apply_privacy:
                skill_dict = await self.apply_skill_privacy(
                    skill_dict,
                    preparation.privacy_values,
                    ctx,
                    change_reason=privacy_change_reason,
                    delete_if_empty=False,
                )
            skill_abstract = self._build_skill_abstract(skill_dict)

            skill_root_uri = f"{canonical_user_root(ctx)}/skills"
            context = Context(
                uri=f"{skill_root_uri}/{skill_dict['name']}",
                parent_uri=skill_root_uri,
                is_leaf=False,
                abstract=skill_abstract,
                context_type=ContextType.SKILL.value,
                user=ctx.user,
                account_id=ctx.account_id,
                owner_space=user_space_fragment(ctx),
                meta={
                    "name": skill_dict["name"],
                    "description": skill_dict.get("description", ""),
                    "allowed_tools": skill_dict.get("allowed_tools", []),
                    "tags": skill_dict.get("tags", []),
                    "source_path": skill_dict.get("source_path", ""),
                },
            )
            context.set_vectorize(Vectorize(text=context.abstract))

            overview_start = time.perf_counter()
            overview = await self._generate_overview(skill_dict, config)
            telemetry.set(
                "skill.overview.duration_ms",
                round((time.perf_counter() - overview_start) * 1000, 3),
            )

            skill_dir_uri = context.uri

            write_start = time.perf_counter()
            await self._write_skill_content(
                viking_fs=viking_fs,
                skill_dict=skill_dict,
                skill_dir_uri=skill_dir_uri,
                abstract=skill_abstract,
                overview=overview,
                ctx=ctx,
            )

            await self._write_auxiliary_files(
                viking_fs=viking_fs,
                auxiliary_files=auxiliary_files,
                base_path=base_path,
                skill_dir_uri=skill_dir_uri,
                ctx=ctx,
            )
            telemetry.set(
                "skill.write.duration_ms", round((time.perf_counter() - write_start) * 1000, 3)
            )

            index_start = time.perf_counter()
            await self._index_skill(
                context=context,
                skill_dir_uri=skill_dir_uri,
            )
            telemetry.set(
                "skill.index.duration_ms", round((time.perf_counter() - index_start) * 1000, 3)
            )
            return {
                "status": "success",
                "root_uri": skill_dir_uri,
                "uri": skill_dir_uri,
                "name": skill_dict["name"],
                "auxiliary_files": len(auxiliary_files),
            }
        finally:
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
            skill_dict, privacy_values = await self.prepare_skill_privacy(skill_dict, ctx)
            return SkillProcessingPreparation(
                skill_dict=skill_dict,
                auxiliary_files=auxiliary_files,
                base_path=base_path,
                cleanup_path=cleanup_path,
                privacy_values=privacy_values,
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
                        if item.is_file() and item.name != "SKILL.md":
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
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        del ctx
        if not self._privacy_config_service:
            return skill_dict, {}

        content = skill_dict.get("content", "")
        extraction_result = await extract_skill_privacy_values(
            skill_name=skill_dict.get("name", ""),
            skill_description=skill_dict.get("description", ""),
            content=content,
        )
        if not extraction_result.values:
            return skill_dict, {}

        sanitized = deepcopy(skill_dict)
        sanitized["content"] = extraction_result.sanitized_content
        return sanitized, extraction_result.values

    async def apply_skill_privacy(
        self,
        skill_dict: Dict[str, Any],
        privacy_values: Dict[str, str],
        ctx: RequestContext,
        *,
        change_reason: str,
        delete_if_empty: bool,
    ) -> Dict[str, Any]:
        if not self._privacy_config_service:
            return skill_dict

        if privacy_values:
            await self._privacy_config_service.upsert(
                ctx=ctx,
                category="skill",
                target_key=skill_dict["name"],
                values=privacy_values,
                updated_by=ctx.user.user_id,
                change_reason=change_reason,
            )
            return skill_dict

        if delete_if_empty:
            await self._privacy_config_service.delete(ctx, "skill", skill_dict["name"])
        return skill_dict

    async def sanitize_skill_privacy(
        self, skill_dict: Dict[str, Any], ctx: RequestContext
    ) -> Dict[str, Any]:
        return await self._sanitize_skill_privacy(ctx=ctx, skill_dict=skill_dict)

    async def _sanitize_skill_privacy(
        self,
        skill_dict: Dict[str, Any],
        ctx: RequestContext,
        *,
        change_reason: str = "auto-extracted from add_skill",
        delete_if_empty: bool = False,
    ) -> Dict[str, Any]:
        sanitized, privacy_values = await self.prepare_skill_privacy(skill_dict, ctx)
        return await self.apply_skill_privacy(
            sanitized,
            privacy_values,
            ctx,
            change_reason=change_reason,
            delete_if_empty=delete_if_empty,
        )

    async def _generate_overview(self, skill_dict: Dict[str, Any], config) -> str:
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
        return await config.vlm.get_completion_async(prompt)

    async def _write_skill_content(
        self,
        viking_fs: VikingFS,
        skill_dict: Dict[str, Any],
        skill_dir_uri: str,
        abstract: str,
        overview: str,
        ctx: RequestContext,
    ):
        """Write main skill content to VikingFS."""
        await viking_fs.write_context(
            uri=skill_dir_uri,
            content=SkillLoader.to_skill_md(skill_dict),
            abstract=abstract,
            overview=overview,
            content_filename="SKILL.md",
            is_leaf=False,
            ctx=ctx,
        )

    async def _write_auxiliary_files(
        self,
        viking_fs: VikingFS,
        auxiliary_files: List[Path],
        base_path: Optional[Path],
        skill_dir_uri: str,
        ctx: RequestContext,
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
                await viking_fs.write_file(aux_uri, file_bytes.decode("utf-8"), ctx=ctx)
            else:
                await viking_fs.write_file_bytes(aux_uri, file_bytes, ctx=ctx)

    async def _index_skill(self, context: Context, skill_dir_uri: str):
        """Write skill directory vector via async queue as L0."""
        context.uri = skill_dir_uri
        context.is_leaf = False
        context.level = 0

        context.set_vectorize(Vectorize(text=context.abstract))
        embedding_msg = EmbeddingMsgConverter.from_context(context)
        if embedding_msg:
            if embedding_msg.telemetry_id:
                get_request_wait_tracker().register_embedding_root(
                    embedding_msg.telemetry_id, embedding_msg.id
                )
            enqueued = await self.vikingdb.enqueue_embedding_msg(embedding_msg)
            if not enqueued and embedding_msg.telemetry_id:
                get_request_wait_tracker().mark_embedding_failed(
                    embedding_msg.telemetry_id,
                    embedding_msg.id,
                    "embedding enqueue returned false",
                )
