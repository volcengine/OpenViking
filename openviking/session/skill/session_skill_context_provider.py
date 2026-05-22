# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session skill extraction provider for ReAct-based skill asset updates."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from openviking.core.namespace import canonical_agent_root
from openviking.core.skill_loader import SkillLoader
from openviking.prompts.manager import PromptManager
from openviking.session.memory.dataclass import MemoryFileContent
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def resolve_skill_extract_templates_dir() -> Path:
    """Resolve the session skill schema directory."""
    return PromptManager._resolve_templates_dir(None) / "skill_extract"


class SessionSkillContextProvider(SessionExtractContextProvider):
    """Provider that reuses session ReAct extraction for real skill assets."""

    def instruction(self) -> str:
        return f"""You are a skill extraction agent. Your task is to analyze the archived conversation and update executable SKILL assets.

## Workflow
1. Analyze the conversation and the pre-fetched skill catalog.
2. If you need the full content of an existing skill, use the read tool on its SKILL.md.
3. When you have enough information, output ONLY a JSON object (no extra text before or after).

## Critical
- Extract executable skills under viking://agent/.../skills/<skill_name>/SKILL.md, not agent/.../memories/skills.
- Before updating an existing skill, you MUST read its full SKILL.md first.
- The content field must contain only the SKILL.md body, without YAML frontmatter.
- When updating an existing skill, keep the exact existing skill_name.
- Prefer updating an existing overlapping skill instead of creating a new one, and never output duplicate operations for the same workflow.
- If the conversation does not provide enough evidence for a reusable skill create/update, return an empty result.

## Target Output Language
All descriptions and skill content MUST be written in {self._output_language}.
"""

    def _build_conversation_message(self) -> Dict[str, Any]:
        if self.messages:
            first_msg_time = getattr(self.messages[0], "created_at", None)
            last_msg_time = getattr(self.messages[-1], "created_at", None)
        else:
            first_msg_time = None
            last_msg_time = None

        if first_msg_time:
            session_time = parse_iso_datetime(first_msg_time)
        else:
            session_time = datetime.now()

        session_time_str = session_time.strftime("%Y-%m-%d %H:%M")
        day_of_week = session_time.strftime("%A")
        if last_msg_time and last_msg_time != first_msg_time:
            last_time = parse_iso_datetime(last_msg_time)
            time_display = f"{session_time_str} - {last_time.strftime('%Y-%m-%d %H:%M')}"
        else:
            time_display = session_time_str

        conversation = self._assemble_conversation(self.messages)
        latest_overview = ""
        if self.latest_archive_overview:
            latest_overview = (
                f"## Latest Completed Archive Overview\n{self.latest_archive_overview}\n\n"
            )

        return {
            "role": "user",
            "content": (
                f"## Conversation History\n"
                f"**Session Time:** {time_display} ({day_of_week})\n"
                "Relative times (e.g., 'last week', 'next month') are based on Session Time, not today.\n\n"
                f"{latest_overview}"
                f"{conversation}"
            ),
        }

    async def prefetch(self) -> List[Dict[str, Any]]:
        pre_fetch_messages = [self._build_conversation_message()]
        if not self._ctx or not self._viking_fs:
            return pre_fetch_messages

        skill_root_uri = f"{canonical_agent_root(self._ctx)}/skills"
        try:
            entries = await self._viking_fs.ls(
                skill_root_uri,
                output="agent",
                abs_limit=256,
                show_all_hidden=False,
                node_limit=1000,
                ctx=self._ctx,
            )
            listed_skills = []
            for entry in entries:
                if not entry.get("isDir", False):
                    continue
                skill_root = (
                    entry.get("uri") or f"{skill_root_uri.rstrip('/')}/{entry.get('name', '')}"
                )
                skill_name = entry.get("name") or skill_root.rstrip("/").split("/")[-1]
                listed_skills.append(
                    {
                        "skill_name": skill_name,
                        "uri": f"{skill_root.rstrip('/')}/SKILL.md",
                        "abstract": entry.get("abstract", ""),
                    }
                )
            add_tool_call_pair_to_messages(
                messages=pre_fetch_messages,
                call_id=0,
                tool_name="ls",
                params={"uri": skill_root_uri},
                result=listed_skills
                if listed_skills
                else "Directory is empty. You can create a new skill if the conversation shows a reusable workflow.",
            )
        except Exception as exc:
            add_tool_call_pair_to_messages(
                messages=pre_fetch_messages,
                call_id=0,
                tool_name="ls",
                params={"uri": skill_root_uri},
                result={"error": str(exc)},
            )
        return pre_fetch_messages

    async def execute_tool(self, tool_call) -> Any:
        if tool_call.name != "read":
            return {"error": f"Unknown tool: {tool_call.name}"}
        arguments = tool_call.arguments or {}
        uri = arguments.get("uri", "")
        try:
            raw_content = await self._viking_fs.read_file(uri, ctx=self._ctx)
        except NotFoundError as exc:
            logger.info("Session skill read not found: %s", uri)
            return {"error": str(exc)}
        except Exception as exc:
            logger.warning("Session skill read failed for %s: %s", uri, exc)
            return {"error": str(exc)}

        if uri.endswith("/SKILL.md"):
            parsed = self._parse_skill_file(raw_content)
        else:
            parsed = parse_memory_file_with_fields(raw_content)
        self._read_file_contents[uri] = MemoryFileContent(
            uri=uri,
            plain_content=raw_content,
            memory_fields=parsed,
        )
        return parsed

    def get_tools(self) -> List[str]:
        return ["read"]

    def get_schema_directories(self) -> List[str]:
        return [str(resolve_skill_extract_templates_dir())]

    def _get_registry(self) -> MemoryTypeRegistry:
        if self._registry is None:
            registry = MemoryTypeRegistry(load_schemas=False)
            loaded = registry.load_from_directory(str(resolve_skill_extract_templates_dir()))
            if loaded == 0:
                raise RuntimeError("No session skill schemas loaded from skill_extract templates")
            self._registry = registry
        return self._registry

    @staticmethod
    def _parse_skill_file(raw_content: str) -> Dict[str, Any]:
        try:
            parsed = SkillLoader.parse(raw_content)
            return {
                "name": parsed.get("name", ""),
                "description": parsed.get("description", ""),
                "content": parsed.get("content", ""),
                "allowed_tools": parsed.get("allowed_tools", []),
                "tags": parsed.get("tags", []),
            }
        except Exception:
            return parse_memory_file_with_fields(raw_content)
