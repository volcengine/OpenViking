# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Extract Context Provider - 会话提取 Provider 实现

从会话消息中提取记忆的实现。
"""

import json
import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.message.part import TextPart, ToolPart
from openviking.prompts.manager import PromptManager
from openviking.server.identity import RequestContext, ToolContext
from openviking.session.memory.core import ExtractContextProvider
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_isolation_handler import (
    MemoryIsolationHandler,
    RoleScope,
    peer_user_space,
)
from openviking.session.memory.memory_type_registry import (
    MemoryTypeRegistry,
    resolve_memory_templates_dir,
)
from openviking.session.memory.tools import (
    add_tool_call_pair_to_messages,
    get_tool,
)
from openviking.session.memory.utils.resource_refs import contains_resource_uri
from openviking.session.memory.utils.uri import render_template
from openviking.session.memory.vision_message_normalizer import (
    replace_image_parts_with_descriptions,
)
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import tracer
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.session.memory.memory_updater import ExtractContext

logger = get_logger(__name__)

_PREFETCH_SEARCH_QUERY_MAX_CHARS = 5000
_PREFETCH_SEARCH_TEXT_PART_MAX_CHARS = 1000
_PREFETCH_SEARCH_ASSISTANT_TEXT_PART_MAX_CHARS = 500
_PREFETCH_SEARCH_TOOL_FIELD_MAX_CHARS = 500
_RESOURCE_REASON_LANGUAGE_RE = re.compile(
    r"(?im)^\s*(?:User reason|用户说明|用户原因|用户理由)[:：]\s*(.+?)\s*$"
)


class SessionExtractContextProvider(ExtractContextProvider):
    """会话提取 Provider - 从会话消息中提取记忆"""

    def __init__(
        self,
        messages: Any,
        latest_archive_overview: str = "",
        isolation_handler: MemoryIsolationHandler = None,
        ctx: RequestContext = None,
        viking_fs: VikingFS = None,
        transaction_handle=None,
    ):
        self.messages = list(messages) if isinstance(messages, list) else messages
        self.latest_archive_overview = latest_archive_overview
        self._output_language = self._detect_language()
        self._registry = None  # 延迟加载
        self._schema_directories = None
        self._extract_context = None  # 缓存 ExtractContext 实例
        self._isolation_handler = isolation_handler
        self._read_file_contents: Dict[str, MemoryFile] = {}
        # 读取 eager_prefetch 配置
        config = get_openviking_config()
        self._eager_prefetch = config.memory.eager_prefetch if config.memory else False
        self._prefetch_search_topn = config.memory.prefetch_search_topn if config.memory else 5
        self._ctx = ctx
        self._viking_fs = viking_fs
        self._transaction_handle = transaction_handle
        self._link_enabled = config.memory.link_enabled if config.memory else False
        self._vision_messages_prepared = False
        self._vision_vlm = None

    @property
    def read_file_contents(self) -> Dict[str, MemoryFile]:
        return self._read_file_contents

    def get_conversation_text(self) -> str:
        """Get the full conversation text for match_text validation."""
        text_parts = []
        for message in self.messages or []:
            for part in getattr(message, "parts", []):
                if isinstance(part, TextPart) and part.text:
                    text_parts.append(part.text)
        return "\n".join(text_parts)

    def set_transaction_handle(self, handle):
        """Set transaction handle after lock is acquired."""
        self._transaction_handle = handle

    def get_extract_context(self) -> "ExtractContext":
        """获取或创建 ExtractContext 实例（缓存）"""
        from openviking.session.memory.memory_updater import ExtractContext

        if self._extract_context is None:
            self._extract_context = ExtractContext(
                self.messages if isinstance(self.messages, list) else []
            )
        return self._extract_context

    async def prepare_extraction_messages(self) -> None:
        """Prepare extraction-only messages before ranges and prompts are built."""
        if self._vision_messages_prepared:
            return
        if isinstance(self.messages, list):
            self.messages = await replace_image_parts_with_descriptions(
                self.messages,
                get_vlm=self._get_vision_vlm,
                logger=logger,
            )
            self._extract_context = None
            self._output_language = self._detect_language()
        self._vision_messages_prepared = True

    def _get_vision_vlm(self):
        if self._vision_vlm is not None:
            return self._vision_vlm
        vlm_config = get_openviking_config().vlm
        if not (vlm_config and vlm_config.is_available()):
            return None
        self._vision_vlm = vlm_config.get_vlm_instance()
        return self._vision_vlm

    def _detect_language(self) -> str:
        """检测输出语言"""
        from openviking.session.memory.utils import (
            resolve_output_language,
            strip_language_detection_noise,
        )

        user_text_parts = []
        all_text_parts = []
        for message in self.messages or []:
            for part in getattr(message, "parts", []):
                if isinstance(part, TextPart) and part.text:
                    text = self._language_signal_text(
                        part.text,
                        strip_language_detection_noise=strip_language_detection_noise,
                    )
                    all_text_parts.append(text)
                    if getattr(message, "role", "") == "user":
                        user_text_parts.append(text)

        text_parts = user_text_parts or all_text_parts
        return resolve_output_language("\n".join(text_parts))

    @staticmethod
    def _language_signal_text(text: str, *, strip_language_detection_noise) -> str:
        """Keep user-authored language signal and drop machine-oriented URI noise."""
        reason_lines = [
            match.group(1).strip()
            for match in _RESOURCE_REASON_LANGUAGE_RE.finditer(text or "")
            if match.group(1).strip()
        ]
        if reason_lines:
            return "\n".join(reason_lines)
        return strip_language_detection_noise(text)

    def get_output_language(self) -> str:
        return self._output_language

    def _conversation_contains_resource_uri(self) -> bool:
        for message in self.messages or []:
            content = getattr(message, "content", None)
            if content and contains_resource_uri(content):
                return True
            for part in getattr(message, "parts", []) or []:
                text = getattr(part, "text", None)
                if text and contains_resource_uri(text):
                    return True
        return False

    def instruction(self) -> str:
        output_language = self._output_language
        contains_resource_uri = self._conversation_contains_resource_uri()
        resource_uri_handling = (
            """

## Resource URI Handling
- If the conversation contains a resource URI (`viking://resources/...`, `viking://user/{user_id}/resources/...`, or `viking://user/{user_id}/peers/{peer_id}/resources/...`) and the user says a durable fact, judgment, preference, or event about it, extract that memory into the appropriate normal memory type such as entities, events, or preferences.
- Preserve resource references as markdown links in visible memory content when useful. Example: user said "The user saved a Ryoma Echizen photo viking://resources/images/ryoma" -> write "The user saved a [Ryoma Echizen photo](viking://resources/images/ryoma)".
- For `## Resource Addition` blocks, use `User reason` as the user's intent and `Resource abstract` only as optional context. Do not copy raw fields such as `Resource URI`, `Added at`, `Resource abstract`, or `User reason` into visible memory content.
- For `## Resource Deletion` blocks, update existing mutable memories that mention or depend on the deleted resource. Do not create a new event solely for this maintenance action.
- Use descriptive link text such as `[Ryoma Echizen photo](viking://resources/...)`; avoid visible wording like `resource URI is` or `Resource URI`.
- If the user already wrote a markdown link to a resource URI, keep the same resource link intent.
- Do NOT claim you inspected, summarized, OCRed, or opened the resource file unless the conversation explicitly provides that fact.
"""
            if contains_resource_uri
            else ""
        )
        resource_deletion_read_source = (
            ", or listed under the system-generated `## Resource Deletion` block's `Affected memory URIs`"
            if contains_resource_uri
            else ""
        )
        goal = f"""You are a memory extraction agent. Your task is to analyze conversations and update memories.

## Workflow
1. Analyze the conversation and pre-fetched context
2. If you need more information, use the available tools (read/search)
3. When you have enough information, output ONLY a JSON object (no extra text before or after)

## Critical
- ONLY read and search tools are available - DO NOT use write tool
- Before editing ANY existing memory file, you MUST first read its complete content
- ONLY read URIs that are explicitly listed in ls/search tool results, returned by previous tool calls{resource_deletion_read_source}

## Target Output Language
All memory content MUST be written in {output_language}.

## URI Handling
The system automatically generates URIs based on memory_type and fields. Just provide correct memory_type and fields.
{resource_uri_handling}

## Self and Peer Memory
When a memory item describes the current user, omit peer_id.
When a memory item describes a peer, set peer_id to one of the peer_id values allowed by
the output schema. Do not invent peer_id values.
For events with ranges, the system derives self/peer targets from the message range.
Message role is authoritative: user-role content is the source for profile/preferences/entities/events,
and assistant-role content is the source for cases/patterns/tools/skills. Do not infer ownership
from neighboring messages.
"""

        return goal

    def _build_conversation_message(self) -> Dict[str, Any]:
        """构建包含 Conversation History 的 user message"""
        from datetime import datetime

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

        # 检查是否需要显示范围
        if last_msg_time and last_msg_time != first_msg_time:
            last_time = parse_iso_datetime(last_msg_time)
            time_display = f"{session_time_str} - {last_time.strftime('%Y-%m-%d %H:%M')}"
        else:
            time_display = session_time_str

        extract_context = self.get_extract_context()
        conversation = self._assemble_conversation(extract_context.messages)

        return {
            "role": "user",
            "content": f"""## Conversation History
**Session Time:** {time_display} ({day_of_week})
Relative times (e.g., 'last week', 'next month') are based on Session Time, not today.

{conversation}

After exploring, analyze the conversation and output ALL memory write/edit/delete operations in a single response. Do not output operations one at a time - gather all changes first, then return them together.""",
        }

    def _assemble_conversation(self, messages: Any) -> str:
        """Assemble conversation string from messages.

        Args:
            messages: List of Message objects
            latest_archive_overview: Optional overview from previous archive for context

        Returns:
            Formatted conversation string
        """
        from openviking.message import Message

        conversation_sections: List[str] = []

        def format_message_with_parts(msg: Message) -> str:
            """Format message with text parts and ToolCall details."""
            parts = getattr(msg, "parts", [])
            formatted_parts: List[str] = []
            for part in parts:
                if hasattr(part, "text") and part.text:
                    formatted_parts.append(part.text)
                elif isinstance(part, ToolPart):
                    tool_info = {
                        "type": "tool_call",
                        "tool_name": part.tool_name,
                        "tool_input": part.tool_input,
                        "tool_output": part.tool_output[:500] if part.tool_output else "",
                        "tool_status": part.tool_status,
                        "duration_ms": part.duration_ms,
                    }
                    if part.skill_uri:
                        tool_info["skill_name"] = part.skill_uri.rstrip("/").split("/")[-1]
                    formatted_parts.append(
                        f"[ToolCall] {json.dumps(tool_info, ensure_ascii=False)}"
                    )
            return "\n".join(formatted_parts) if formatted_parts else msg.content

        def format_message_header(msg: Message, idx: int) -> str:
            """Format message header with role and stable interaction peer when present."""
            speaker = msg.peer_id or msg.role
            return f"[{idx}][{msg.role}][{speaker}]: {format_message_with_parts(msg)}"

        conversation_sections.append(
            "\n".join([format_message_header(msg, idx) for idx, msg in enumerate(messages)])
        )

        return "\n\n".join(section for section in conversation_sections if section)

    def _truncate_prefetch_query_text(self, text: Any, max_chars: int) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3].rstrip() + "..."

    def _format_tool_part_for_search(self, part: ToolPart) -> str:
        fields = []
        if part.tool_name:
            fields.append(f"tool_name={part.tool_name}")
        if part.skill_uri:
            skill_name = part.skill_uri.rstrip("/").split("/")[-1]
            fields.append(f"skill_name={skill_name}")
        if part.tool_status:
            fields.append(f"status={part.tool_status}")
        if part.tool_input:
            fields.append(
                "input="
                + self._truncate_prefetch_query_text(
                    json.dumps(part.tool_input, ensure_ascii=False),
                    _PREFETCH_SEARCH_TOOL_FIELD_MAX_CHARS,
                )
            )
        if part.tool_output and part.tool_status == "error":
            fields.append(
                "error="
                + self._truncate_prefetch_query_text(
                    part.tool_output,
                    _PREFETCH_SEARCH_TOOL_FIELD_MAX_CHARS,
                )
            )
        return "ToolCall: " + "; ".join(fields)

    def _build_prefetch_search_query(self) -> str:
        """Build a compact semantic query from raw conversation messages.

        The LLM already receives the full conversation via pre_fetch_messages.
        Search only needs topical recall signals, so use the raw message content
        instead of the prompt-wrapped conversation.
        """
        if not isinstance(self.messages, list):
            return ""

        primary_sections: List[str] = []
        supporting_sections: List[str] = []

        for msg in self.messages:
            role = getattr(msg, "role", "")
            speaker = getattr(msg, "peer_id", "") or role
            parts = getattr(msg, "parts", [])

            text_parts: List[str] = []
            tool_parts: List[str] = []

            for part in parts:
                if hasattr(part, "text") and part.text:
                    limit = (
                        _PREFETCH_SEARCH_TEXT_PART_MAX_CHARS
                        if role == "user"
                        else _PREFETCH_SEARCH_ASSISTANT_TEXT_PART_MAX_CHARS
                    )
                    text_parts.append(self._truncate_prefetch_query_text(part.text, limit))
                elif isinstance(part, ToolPart):
                    tool_part = self._format_tool_part_for_search(part)
                    if tool_part != "ToolCall: ":
                        tool_parts.append(tool_part)

            if text_parts:
                section = f"{speaker}: " + "\n".join(text_parts)
                if role == "user":
                    primary_sections.append(section)
                else:
                    supporting_sections.append(section)

            if tool_parts:
                supporting_sections.append(f"{speaker}: " + "\n".join(tool_parts))

        query = "\n\n".join(primary_sections + supporting_sections)
        if not query.strip():
            query = self._assemble_conversation(self.messages)

        return self._truncate_prefetch_query_text(query, _PREFETCH_SEARCH_QUERY_MAX_CHARS)

    def create_tool_context(self, default_search_uris=[]):
        extract_context = self.get_extract_context()
        tool_ctx = ToolContext(
            viking_fs=self._viking_fs,
            request_ctx=self._ctx,
            transaction_handle=self._transaction_handle,
            default_search_uris=default_search_uris,
            read_file_contents=self._read_file_contents,
            page_id_map=extract_context.page_id_map,
        )
        return tool_ctx

    async def read_file(self, uri: str) -> Optional[Dict]:
        """Read a file via MemoryReadTool (auto-registers page_id, fills read_file_contents)."""
        read_tool = get_tool("read")
        if not read_tool:
            return None
        try:
            result = await read_tool.execute(self.create_tool_context(), uri=uri)
            if isinstance(result, dict) and "error" in result:
                tracer.info(f"Failed to read {uri}: {result['error']}")
                return None
            return result
        except Exception as e:
            tracer.error(f"Failed to read {uri}: {e}")
            return None

    async def search_files(
        self, query: str, search_uris: List[str] = None, limit: int = 5
    ) -> List[str]:
        """Search via MemorySearchTool, returns list of URIs."""
        search_tool = get_tool("search")
        if not search_tool:
            return []
        try:
            result = await search_tool.execute(
                viking_fs=self._viking_fs,
                ctx=self.create_tool_context(search_uris or []),
                query=query,
                limit=limit,
            )
            if isinstance(result, list):
                return [m.get("uri", "") for m in result if m.get("uri")]
            elif isinstance(result, dict) and "memories" in result:
                return [m.get("uri", "") for m in result.get("memories", []) if m.get("uri")]
            return []
        except Exception as e:
            tracer.error(f"Failed to search: {e}")
            return []

    async def _append_structured_read_result(
        self,
        messages: List[Dict[str, Any]],
        call_id: int,
        file_uri: str,
    ) -> int:
        result = await self.read_file(file_uri)
        if result is not None:
            add_tool_call_pair_to_messages(
                messages=messages,
                call_id=call_id,
                tool_name="read",
                params={"uri": file_uri},
                result=result,
            )
            return call_id + 1
        return call_id

    async def prefetch(self) -> List[Dict]:
        """
        执行 prefetch - 从会话消息中提取相关记忆上下文

        Returns:
            预取的消息列表，第一个元素是 Conversation History user message，后续是 tool call messages
        """
        messages = self.messages

        if not isinstance(messages, list):
            tracer.error(f"Expected List[Message], got {type(messages)}")
            return []

        # 先构建 Conversation History user message
        pre_fetch_messages = []
        pre_fetch_messages.append(self._build_conversation_message())

        # 触发 registry 加载，过滤掉 agent_only 的 schema（trajectory/experience 由执行提取处理）
        schemas = [
            s
            for s in self._get_registry().list_all(include_disabled=False)
            if not getattr(s, "agent_only", False)
        ]
        if self._isolation_handler:
            schemas = [s for s in schemas if self._isolation_handler.allows_schema(s)]

        # Step 1: Separate schemas into multi-file (ls) and single-file (direct read)
        ls_dirs = set()  # directories to ls (for multi-file schemas)
        read_files = set()  # files to read directly (for single-file schemas)

        rolescope: RoleScope = self._isolation_handler.get_read_scope()

        for schema in schemas:
            if not schema.directory:
                continue

            # 根据 operation_mode 决定是否需要 ls 和读取其他文件
            if schema.operation_mode == "add_only":
                continue

            schema_dirs = set()
            if self._isolation_handler:
                schema_dirs.update(self._isolation_handler.render_schema_directories(schema))
            else:
                for user_id in rolescope.user_ids:
                    dir_path = render_template(
                        schema.directory,
                        {"user_space": user_id},
                    )
                    schema_dirs.add(dir_path)
                    for peer_id in rolescope.peer_ids:
                        dir_path = render_template(
                            schema.directory,
                            {"user_space": peer_user_space(user_id, peer_id)},
                        )
                        schema_dirs.add(dir_path)
            if schema.filename_has_variables():
                for dir_path in schema_dirs:
                    ls_dirs.add(dir_path)
            else:
                for dir_path in schema_dirs:
                    file_uri = f"{dir_path}/{schema.filename_template}"
                    read_files.add(file_uri)

        call_id_seq = 0
        # Step 2: Execute search for each ls directory (instead of ls)

        # 首先读取所有 .overview.md 文件（截断以避免窗口过大）
        # 为 overview 读取创建一个基本的 tool_ctx

        # 在每个之前 ls 的目录内执行 search（替换原来的 ls操作）
        files_to_read_from_search = []  # 收集需要读取的文件（eager_prefetch 模式）

        # 批量 search：所有目录一次搜索
        if ls_dirs:
            dir_list = list(ls_dirs)
            search_query = self._build_prefetch_search_query()
            if not search_query:
                search_query = "conversation"
            search_uris = await self.search_files(
                query=search_query,
                search_uris=dir_list,
            )
            result_value = search_uris
            if self._eager_prefetch:
                files_to_read_from_search.extend(search_uris)

            add_tool_call_pair_to_messages(
                messages=pre_fetch_messages,
                call_id=call_id_seq,
                tool_name="search",
                params={"query": "[Keywords]", "search_uri": dir_list},
                result=result_value,
            )
            call_id_seq += 1

        # 读取单文件 schema 的文件（只对非 add_only 模式）
        for file_uri in read_files:
            call_id_seq = await self._append_structured_read_result(
                messages=pre_fetch_messages,
                call_id=call_id_seq,
                file_uri=file_uri,
            )

        # eager_prefetch 模式：读取搜索结果 top-N
        if self._eager_prefetch:
            topn_files = files_to_read_from_search[: self._prefetch_search_topn]
            for file_uri in topn_files:
                if not file_uri:
                    continue
                call_id_seq = await self._append_structured_read_result(
                    messages=pre_fetch_messages,
                    call_id=call_id_seq,
                    file_uri=file_uri,
                )

        return pre_fetch_messages

    @tracer("execute_tool", ignore_result=False)
    async def execute_tool(
        self,
        tool_call,
    ) -> Any:
        tool = get_tool(tool_call.name)
        if not tool:
            return {"error": f"Unknown tool: {tool_call.name}"}
        tracer.info(f"tool_call.arguments={tool_call.arguments}")
        result = await tool.execute(self.create_tool_context(), **tool_call.arguments)
        return result

    def get_tools(self) -> List[str]:
        """获取可用的工具列表"""
        if self._eager_prefetch:
            # eager_prefetch 模式下不提供工具，所有内容已在 prefetch 中加载
            return []
        return ["read"]

    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        """获取需要参与的 memory schemas（内部自动加载）"""
        schemas = [
            s
            for s in self._get_registry().list_all(include_disabled=False)
            if not getattr(s, "agent_only", False)
        ]
        if self._isolation_handler:
            schemas = [s for s in schemas if self._isolation_handler.allows_schema(s)]
        return schemas

    def get_schema_directories(self) -> List[str]:
        """返回需要加载的 schema 目录"""
        if self._schema_directories is None:
            memory_templates_dir = str(PromptManager._get_bundled_templates_dir() / "memory")
            config = get_openviking_config()
            custom_dir = config.memory.custom_templates_dir
            self._schema_directories = [memory_templates_dir]
            if getattr(config.memory, "experimental_memory_switch", False):
                experimental_memory_dir = os.path.join(memory_templates_dir, "experimental_memory")
                if os.path.exists(experimental_memory_dir):
                    self._schema_directories.append(experimental_memory_dir)
            if custom_dir:
                custom_dir_expanded = os.path.expanduser(custom_dir)
                if os.path.exists(custom_dir_expanded):
                    self._schema_directories.append(custom_dir_expanded)
            else:
                memory_templates_dir = str(resolve_memory_templates_dir())
                if memory_templates_dir != str(
                    PromptManager._get_bundled_templates_dir() / "memory"
                ) and os.path.exists(memory_templates_dir):
                    self._schema_directories.append(memory_templates_dir)
        return self._schema_directories

    def _get_registry(self) -> MemoryTypeRegistry:
        """内部获取 registry（自动在初始化时加载）"""
        if self._registry is None:
            # MemoryTypeRegistry 在 __init__ 时自动加载 schemas
            self._registry = MemoryTypeRegistry(load_schemas=True)
        return self._registry
