# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session skill extraction provider for ReAct-based skill asset updates."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from openviking.core.namespace import canonical_user_root
from openviking.core.skill_loader import SkillLoader
from openviking.prompts.manager import PromptManager
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.session.memory.utils import add_line_numbers, line_count, slice_content_lines
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

SESSION_SKILL_MEMORY_TYPE = "session_skills"


def resolve_skill_extract_templates_dir() -> Path:
    """Resolve the session skill schema directory."""
    return PromptManager._resolve_templates_dir(None) / "skill_extract"


def load_skill_extract_registry() -> MemoryTypeRegistry:
    registry = MemoryTypeRegistry(load_schemas=False)
    loaded = registry.load_from_directory(str(resolve_skill_extract_templates_dir()))
    if loaded == 0:
        raise RuntimeError("No session skill schemas loaded from skill_extract templates")
    return registry


def parse_skill_extract_file(raw_content: str) -> Dict[str, Any]:
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


def build_skill_read_result(
    raw_content: str,
    *,
    uri: str,
    offset: int = 0,
    limit: int = -1,
) -> Tuple[Dict[str, Any], MemoryFile]:
    parsed = parse_skill_extract_file(raw_content)
    stored = MemoryFile.from_parsed(uri=uri, parsed=dict(parsed))

    llm_result = {
        "name": parsed.get("name", ""),
        "description": parsed.get("description", ""),
        "allowed_tools": parsed.get("allowed_tools", []),
        "tags": parsed.get("tags", []),
    }
    plain_content = parsed.get("content", "") or ""
    visible_content = slice_content_lines(plain_content, offset=offset, limit=limit)
    if visible_content:
        llm_result["content"] = add_line_numbers(visible_content, start_line=offset + 1)
    elif line_count(plain_content) == 0:
        llm_result["content"] = (
            "<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>"
        )
    else:
        llm_result["content"] = (
            "<system-reminder>Warning: the file exists but is shorter than the provided "
            f"offset ({offset + 1}). The file has {line_count(plain_content)} lines.</system-reminder>"
        )
    return llm_result, stored


class SessionSkillContextProvider(SessionExtractContextProvider):
    """Provider that reuses session ReAct extraction for real skill assets."""

    def instruction(self) -> str:
        return (
            "You are an extraction agent. Analyze the archived conversation, use read when "
            "needed, and output only JSON that matches the schema descriptions."
        )

    async def prefetch(self) -> List[Dict[str, Any]]:
        pre_fetch_messages = [self._build_conversation_message()]
        if not self._ctx or not self._viking_fs:
            return pre_fetch_messages

        skill_root_uri = f"{canonical_user_root(self._ctx)}/skills"
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
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", -1)

        if not uri.endswith("/SKILL.md"):
            return await super().execute_tool(tool_call)

        try:
            raw_content = await self._viking_fs.read_file(uri, ctx=self._ctx)
        except NotFoundError as exc:
            logger.info("Session skill read not found: %s", uri)
            return {"error": str(exc)}
        except Exception as exc:
            logger.warning("Session skill read failed for %s: %s", uri, exc)
            return {"error": str(exc)}

        result, stored = build_skill_read_result(
            raw_content,
            uri=uri,
            offset=offset,
            limit=limit,
        )
        self._read_file_contents[uri] = stored
        return result

    def get_tools(self) -> List[str]:
        return ["read"]

    def get_schema_directories(self) -> List[str]:
        return [str(resolve_skill_extract_templates_dir())]

    def _get_registry(self) -> MemoryTypeRegistry:
        if self._registry is None:
            self._registry = load_skill_extract_registry()
        return self._registry
