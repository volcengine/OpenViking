# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Skill Processor for OpenViking.

Handles skill parsing, LLM generation, and storage operations.
"""

import tempfile
import time
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from openviking.core.context import Context, ContextType, Vectorize
from openviking.core.mcp_converter import is_mcp_format, mcp_to_skill
from openviking.core.namespace import agent_space_fragment, canonical_agent_root
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
from openviking.utils.zip_safe import safe_extract_zip
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


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
    ) -> Dict[str, Any]:
        """
        Process and store a skill.

        Args:
            data: Skill data (directory path, file path, string, or dict)
            viking_fs: VikingFS instance for storage
            user: Username for context

        Returns:
            Processing result with status and metadata
        """

        if data is None:
            raise ValueError("Skill data cannot be None")

        config = get_openviking_config()
        telemetry = get_current_telemetry()

        parse_start = time.perf_counter()
        skill_dict, auxiliary_files, base_path = self._parse_skill(
            data,
            allow_local_path_resolution=allow_local_path_resolution,
        )
        telemetry.set(
            "skill.parse.duration_ms", round((time.perf_counter() - parse_start) * 1000, 3)
        )

        skill_dict = await self._sanitize_skill_privacy(skill_dict, ctx)

        context = Context(
            uri=f"{canonical_agent_root(ctx)}/skills/{skill_dict['name']}",
            parent_uri=f"{canonical_agent_root(ctx)}/skills",
            is_leaf=False,
            abstract=skill_dict.get("description", ""),
            context_type=ContextType.SKILL.value,
            user=ctx.user,
            account_id=ctx.account_id,
            owner_space=agent_space_fragment(ctx),
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

        skill_dir_uri = f"viking://agent/skills/{context.meta['name']}"

        write_start = time.perf_counter()
        await self._write_skill_content(
            viking_fs=viking_fs,
            skill_dict=skill_dict,
            skill_dir_uri=skill_dir_uri,
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

    def _parse_skill(
        self,
        data: Any,
        allow_local_path_resolution: bool = True,
    ) -> tuple[Dict[str, Any], List[Path], Optional[Path]]:
        """Parse skill data from various formats."""
        if data is None:
            raise ValueError("Skill data cannot be None")

        auxiliary_files = []
        base_path = None

        if isinstance(data, str):
            if allow_local_path_resolution:
                path_obj = Path(data)
                if path_obj.exists():
                    if zipfile.is_zipfile(path_obj):
                        temp_dir = Path(tempfile.mkdtemp())
                        with zipfile.ZipFile(path_obj, "r") as zipf:
                            safe_extract_zip(zipf, temp_dir)
                        data = temp_dir
                    else:
                        data = path_obj
            else:
                deny_direct_local_skill_input(data)

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
                # Single SKILL.md file
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

        return skill_dict, auxiliary_files, base_path

    async def _sanitize_skill_privacy(
        self, skill_dict: Dict[str, Any], ctx: RequestContext
    ) -> Dict[str, Any]:
        if not self._privacy_config_service:
            return skill_dict

        content = skill_dict.get("content", "")
        extraction_result = await extract_skill_privacy_values(
            skill_name=skill_dict.get("name", ""),
            skill_description=skill_dict.get("description", ""),
            content=content,
        )
        if not extraction_result.values:
            return skill_dict

        sanitized = deepcopy(skill_dict)
        sanitized["content"] = extraction_result.sanitized_content
        await self._privacy_config_service.upsert(
            ctx=ctx,
            category="skill",
            target_key=sanitized["name"],
            values=extraction_result.values,
            updated_by=ctx.user.user_id,
            change_reason="auto-extracted from add_skill",
        )
        return sanitized

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
        overview: str,
        ctx: RequestContext,
    ):
        """Write main skill content to VikingFS."""
        await viking_fs.write_context(
            uri=skill_dir_uri,
            content=skill_dict.get("content", ""),
            abstract=skill_dict.get("description", ""),
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
                aux_uri = f"{skill_dir_uri}/{rel_path}"
            else:
                aux_uri = f"{skill_dir_uri}/{aux_file.name}"

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
        context.parent_uri = "viking://agent/skills"
        context.is_leaf = False
        context.level = 0

        context.set_vectorize(Vectorize(text=context.abstract))
        embedding_msg = EmbeddingMsgConverter.from_context(context)
        if embedding_msg:
            enqueued = await self.vikingdb.enqueue_embedding_msg(embedding_msg)
            if enqueued and embedding_msg.telemetry_id:
                get_request_wait_tracker().register_embedding_root(
                    embedding_msg.telemetry_id, embedding_msg.id
                )
