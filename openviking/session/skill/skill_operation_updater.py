# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Apply session skill extraction operations to real skill assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from openviking.core.skill_loader import SkillLoader
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_op import MergeOpFactory
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


@dataclass
class SkillOperationUpdateResult:
    written_uris: List[str] = field(default_factory=list)
    edited_uris: List[str] = field(default_factory=list)
    errors: List[Tuple[str, Exception]] = field(default_factory=list)
    operation_results: List[Dict[str, Any]] = field(default_factory=list)

    def add_written(self, uri: str) -> None:
        self.written_uris.append(uri)

    def add_edited(self, uri: str) -> None:
        self.edited_uris.append(uri)

    def add_error(self, uri: str, error: Exception) -> None:
        self.errors.append((uri, error))

    def add_result(self, result: Dict[str, Any]) -> None:
        self.operation_results.append(result)


class SkillOperationUpdater:
    """Create or update agent skills from resolved ReAct operations."""

    def __init__(
        self,
        registry: MemoryTypeRegistry,
        skill_processor: SkillProcessor,
        viking_fs: Optional[VikingFS] = None,
    ):
        self._registry = registry
        self._skill_processor = skill_processor
        self._viking_fs = viking_fs or get_viking_fs()

    async def apply_operations(
        self,
        operations: ResolvedOperations,
        ctx: RequestContext,
    ) -> SkillOperationUpdateResult:
        result = SkillOperationUpdateResult()
        if not self._viking_fs:
            raise RuntimeError("VikingFS is required for session skill updates")

        if operations.has_errors():
            for error in operations.errors:
                result.add_error("unknown", ValueError(error))
            return result

        for operation in operations.upsert_operations:
            try:
                op_result = await self._apply_upsert(operation, ctx)
                result.add_result(op_result)
                if op_result.get("action") == "create":
                    result.add_written(op_result["skill_md_uri"])
                else:
                    result.add_edited(op_result["skill_md_uri"])
            except Exception as exc:
                target_uri = (operation.uris or ["unknown"])[0]
                logger.error("Failed to apply session skill operation for %s: %s", target_uri, exc)
                result.add_error(target_uri, exc)

        return result

    async def _apply_upsert(
        self,
        operation: ResolvedOperation,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        if not operation.uris:
            raise ValueError("Session skill operation does not have a target URI")

        skill_md_uri = operation.uris[0]
        root_uri = self._root_uri_from_skill_md(skill_md_uri)
        existing_skill = await self._load_existing_skill(skill_md_uri, ctx)
        merged_skill = self._merge_skill(operation, existing_skill)

        if existing_skill is None:
            processor_result = await self._skill_processor.process_skill(
                data=merged_skill,
                viking_fs=self._viking_fs,
                ctx=ctx,
                allow_local_path_resolution=False,
            )
            created_root_uri = (
                processor_result.get("root_uri") or processor_result.get("uri") or root_uri
            )
            return {
                "status": "success",
                "action": "create",
                "root_uri": created_root_uri,
                "uri": created_root_uri,
                "skill_md_uri": f"{created_root_uri.rstrip('/')}/SKILL.md",
                "name": merged_skill["name"],
            }

        merged_skill = await self._skill_processor.sanitize_skill_privacy(merged_skill, ctx)
        serialized = SkillLoader.to_skill_md(merged_skill)
        write_result = await ContentWriteCoordinator(self._viking_fs).write(
            uri=skill_md_uri,
            content=serialized,
            ctx=ctx,
            mode="replace",
        )
        updated_root_uri = write_result.get("root_uri") or root_uri
        return {
            "status": "success",
            "action": "update",
            "root_uri": updated_root_uri,
            "uri": updated_root_uri,
            "skill_md_uri": skill_md_uri,
            "name": merged_skill["name"],
        }

    def _merge_skill(
        self,
        operation: ResolvedOperation,
        existing_skill: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        schema = self._registry.get(operation.memory_type)
        if not schema:
            raise ValueError(f"Unknown session skill schema: {operation.memory_type}")

        skill = {
            "name": (existing_skill or {}).get("name")
            or operation.memory_fields.get("skill_name", ""),
            "description": (existing_skill or {}).get("description", ""),
            "content": (existing_skill or {}).get("content"),
            "allowed_tools": list((existing_skill or {}).get("allowed_tools") or []),
            "tags": list(
                (existing_skill or {}).get("tags")
                or (["session-derived"] if existing_skill is None else [])
            ),
        }

        for schema_field in schema.fields:
            if schema_field.name not in operation.memory_fields:
                continue
            merge_op = MergeOpFactory.from_field(schema_field)
            patch_value = operation.memory_fields[schema_field.name]
            target_key = self._target_skill_key(schema_field.name)
            current_value = skill.get(target_key)
            skill[target_key] = merge_op.apply(current_value, patch_value)

        if not skill["name"]:
            raise ValueError("Session skill operation is missing skill_name")
        if not skill.get("description"):
            raise ValueError(f"Session skill '{skill['name']}' is missing description")
        if not skill.get("content"):
            raise ValueError(f"Session skill '{skill['name']}' is missing content")
        return skill

    async def _load_existing_skill(
        self,
        skill_md_uri: str,
        ctx: RequestContext,
    ) -> Optional[Dict[str, Any]]:
        try:
            raw_content = await self._viking_fs.read_file(skill_md_uri, ctx=ctx)
        except (FileNotFoundError, NotFoundError):
            return None

        try:
            return SkillLoader.parse(raw_content)
        except Exception:
            abstract_meta = await self._load_abstract_meta(
                self._root_uri_from_skill_md(skill_md_uri), ctx
            )
            if abstract_meta is None:
                raise
            return {
                "name": abstract_meta.get("name")
                or self._root_uri_from_skill_md(skill_md_uri).rstrip("/").split("/")[-1],
                "description": abstract_meta.get("description", ""),
                "content": raw_content.strip(),
                "allowed_tools": abstract_meta.get("allowed_tools")
                or abstract_meta.get("allowed-tools")
                or [],
                "tags": abstract_meta.get("tags") or [],
            }

    async def _load_abstract_meta(
        self,
        root_uri: str,
        ctx: RequestContext,
    ) -> Optional[Dict[str, Any]]:
        try:
            abstract = await self._viking_fs.read_file(
                f"{root_uri.rstrip('/')}/.abstract.md", ctx=ctx
            )
        except (FileNotFoundError, NotFoundError):
            return None
        parsed = yaml.safe_load(abstract)
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _target_skill_key(field_name: str) -> str:
        if field_name == "skill_name":
            return "name"
        return field_name

    @staticmethod
    def _root_uri_from_skill_md(skill_md_uri: str) -> str:
        suffix = "/SKILL.md"
        if skill_md_uri.endswith(suffix):
            return skill_md_uri[: -len(suffix)]
        return skill_md_uri.rstrip("/")
