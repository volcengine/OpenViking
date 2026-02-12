# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Skill Processor for OpenViking.

Handles skill parsing, LLM generation, and storage operations.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from openviking.core.context import Context, ContextType, Vectorize
from openviking.core.mcp_converter import is_mcp_format, mcp_to_skill
from openviking.core.skill_loader import SkillLoader
from openviking.storage import VikingDBManager
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
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

    def __init__(self, vikingdb: VikingDBManager):
        """Initialize skill processor."""
        self.vikingdb = vikingdb

    async def process_skill(
        self,
        data: Any,
        viking_fs: VikingFS,
        user: Optional[UserIdentifier] = None,
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

        config = get_openviking_config()

        skill_dict, auxiliary_files, base_path = self._parse_skill(data)

        context = Context(
            uri=f"viking://agent/skills/{skill_dict['name']}",
            parent_uri="viking://agent/skills",
            is_leaf=False,
            abstract=skill_dict.get("description", ""),
            context_type=ContextType.SKILL.value,
            meta={
                "name": skill_dict["name"],
                "description": skill_dict.get("description", ""),
                "allowed_tools": skill_dict.get("allowed_tools", []),
                "tags": skill_dict.get("tags", []),
                "source_path": skill_dict.get("source_path", ""),
            },
        )
        context.set_vectorize(Vectorize(text=context.abstract))

        overview = await self._generate_overview(skill_dict, config)

        skill_dir_uri = f"viking://agent/skills/{context.meta['name']}"

        await self._write_skill_content(
            viking_fs=viking_fs,
            skill_dict=skill_dict,
            skill_dir_uri=skill_dir_uri,
            overview=overview,
        )

        await self._write_auxiliary_files(
            viking_fs=viking_fs,
            auxiliary_files=auxiliary_files,
            base_path=base_path,
            skill_dir_uri=skill_dir_uri,
        )

        await self._index_skill(
            context=context,
            skill_dir_uri=skill_dir_uri,
        )

        return {
            "status": "success",
            "uri": skill_dir_uri,
            "name": skill_dict["name"],
            "auxiliary_files": len(auxiliary_files),
        }

    def _parse_skill(self, data: Any) -> tuple[Dict[str, Any], List[Path], Optional[Path]]:
        """Parse skill data from various formats."""
        auxiliary_files = []
        base_path = None

        # Convert string paths to Path objects
        if isinstance(data, str):
            path_obj = Path(data)
            if path_obj.exists():
                data = path_obj

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
    ):
        """Write main skill content to VikingFS."""
        await viking_fs.write_context(
            uri=skill_dir_uri,
            content=skill_dict.get("content", ""),
            abstract=skill_dict.get("description", ""),
            overview=overview,
            content_filename="SKILL.md",
            is_leaf=False,
        )

    async def _write_auxiliary_files(
        self,
        viking_fs: VikingFS,
        auxiliary_files: List[Path],
        base_path: Optional[Path],
        skill_dir_uri: str,
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
                await viking_fs.write_file(aux_uri, file_bytes.decode("utf-8"))
            else:
                await viking_fs.write_file_bytes(aux_uri, file_bytes)

    async def _index_skill(self, context: Context, skill_dir_uri: str):
        """Write skill to vector store via async queue."""
        context.uri = skill_dir_uri

        embedding_msg = EmbeddingMsgConverter.from_context(context)
        await self.vikingdb.enqueue_embedding_msg(embedding_msg)
