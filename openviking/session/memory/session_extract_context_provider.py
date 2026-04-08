# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Extract Context Provider - 会话提取 Provider 实现

从会话消息中提取记忆的实现。
"""

import json
import os
from typing import Any, Dict, List

from openviking.server.identity import RequestContext
from openviking.session.memory.core import ExtractContextProvider
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.tools import (
    add_tool_call_pair_to_messages,
    get_tool,
)
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


class SessionExtractContextProvider(ExtractContextProvider):
    """会话提取 Provider - 从会话消息中提取记忆"""

    def __init__(self, messages: Any, latest_archive_overview: str = ""):
        self.messages = messages
        self.latest_archive_overview = latest_archive_overview
        self._output_language = self._detect_language()
        self._registry = None  # 延迟加载
        self._schema_directories = None
        self._extract_context = None  # 缓存 ExtractContext 实例

    def get_extract_context(self) -> "ExtractContext":
        """获取或创建 ExtractContext 实例（缓存）"""
        from openviking.session.memory.memory_updater import ExtractContext

        if self._extract_context is None and self.messages:
            self._extract_context = ExtractContext(self.messages)
        return self._extract_context

    def _detect_language(self) -> str:
        """检测输出语言"""
        from openviking.session.memory.utils import detect_language_from_conversation

        conversation = self._assemble_conversation(self.messages)
        config = get_openviking_config()
        fallback_language = (config.language_fallback or "en").strip() or "en"
        return detect_language_from_conversation(conversation, fallback_language=fallback_language)

    def instruction(self) -> str:
        output_language = self._output_language
        goal = f"""You are a memory extraction agent. Your task is to analyze conversations and update memories.

## Workflow
1. Analyze the conversation and pre-fetched context
2. If you need more information, use the available tools (read/search)
3. When you have enough information, output ONLY a JSON object (no extra text before or after)

## Critical
- ONLY read and search tools are available - DO NOT use write tool
- Before editing ANY existing memory file, you MUST first read its complete content
- ONLY read URIs that are explicitly listed in ls tool results or returned by previous tool calls

## Target Output Language
All memory content MUST be written in {output_language}.

## URI Handling
The system automatically generates URIs based on memory_type and fields. Just provide correct memory_type and fields.

"""

        return goal

        """
    ## Edit Overview Files
    After writing new memories, you MUST also update the corresponding .overview.md file.
    - Provide memory_type to identify which directory's overview to update

    ## Overview Format
    Two options:
    1. **PREFERRED: Direct string** - Just provide the complete new overview content:
       {{"memory_type": "events", "overview": "# Events Overview\n- [event1](event1.md) - Description"}}
    2. **SEARCH/REPLACE** - Only use if you must modify a small portion:
       {{"memory_type": "events", "overview": {{"blocks": [{{"search": "exact line to change", "replace": "new line"}}]}}}}

    See GenericOverviewEdit in the JSON Schema below.
        """


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
            session_time = first_msg_time
        else:
            session_time = datetime.now()

        session_time_str = session_time.strftime("%Y-%m-%d %H:%M")
        day_of_week = session_time.strftime("%A")

        # 检查是否需要显示范围
        if last_msg_time and last_msg_time != first_msg_time:
            time_display = f"{session_time_str} - {last_msg_time.strftime('%Y-%m-%d %H:%M')}"
        else:
            time_display = session_time_str

        conversation = self._assemble_conversation(self.messages)

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
        from openviking.message.part import ToolPart

        conversation_sections: List[str] = []

        def format_message_with_parts(msg: Message) -> str:
            """Format message with text and tool parts."""
            parts = getattr(msg, "parts", [])
            has_tool_parts = any(isinstance(p, ToolPart) for p in parts)

            if not has_tool_parts:
                return msg.content

            tool_lines = []
            text_lines = []
            for part in parts:
                if hasattr(part, "text") and part.text:
                    text_lines.append(part.text)
                elif isinstance(part, ToolPart):
                    tool_info = {
                        "type": "tool_call",
                        "tool_name": part.tool_name,
                        "tool_input": part.tool_input,
                        "tool_status": part.tool_status,
                    }
                    if part.skill_uri:
                        tool_info["skill_name"] = part.skill_uri.rstrip("/").split("/")[-1]
                    tool_lines.append(f"[ToolCall] {json.dumps(tool_info, ensure_ascii=False)}")

            all_lines = tool_lines + text_lines
            return "\n".join(all_lines) if all_lines else msg.content

        conversation_sections.append(
            "\n".join(
                [
                    f"[{idx}][{msg.role}]: {format_message_with_parts(msg)}"
                    for idx, msg in enumerate(messages)
                ]
            )
        )

        return "\n\n".join(section for section in conversation_sections if section)

    async def prefetch(
        self,
        ctx: RequestContext,
        viking_fs: VikingFS,
        transaction_handle,
        vlm,
    ) -> List[Dict]:
        """
        执行 prefetch - 从会话消息中提取相关记忆上下文

        Args:
            ctx: RequestContext
            viking_fs: VikingFS
            transaction_handle: 事务句柄
            vlm: VLM 实例

        Returns:
            预取的消息列表，第一个元素是 Conversation History user message，后续是 tool call messages
        """
        messages = self.messages

        if not isinstance(messages, list):
            logger.warning(f"Expected List[Message], got {type(messages)}")
            return []

        # 先构建 Conversation History user message
        pre_fetch_messages = []
        pre_fetch_messages.append(self._build_conversation_message())

        # 触发 registry 加载
        schemas = self._get_registry().list_all(include_disabled=False)

        from openviking.server.identity import ToolContext

        # Step 1: Separate schemas into multi-file (ls) and single-file (direct read)
        ls_dirs = set()  # directories to ls (for multi-file schemas)
        read_files = set()  # files to read directly (for single-file schemas)
        overview_files = set()  # .overview.md files to read

        for schema in schemas:
            if not schema.directory:
                continue

            # Replace variables in directory path with actual user/agent space
            user_space = ctx.user.user_space_name() if ctx and ctx.user else "default"
            agent_space = ctx.user.agent_space_name() if ctx and ctx.user else "default"
            import jinja2

            env = jinja2.Environment(autoescape=False)
            template = env.from_string(schema.directory)
            dir_path = template.render(user_space=user_space, agent_space=agent_space)

            # Always add .overview.md to read list
            overview_files.add(f"{dir_path}/.overview.md")

            # 根据 operation_mode 决定是否需要 ls 和读取其他文件
            if schema.operation_mode == "add_only":
                # 只新增，不需要查看之前的记忆列表，只需要读取 .overview.md
                continue

            # Check if filename_template has variables (contains {{ xxx }})
            has_variables = False
            if schema.filename_template:
                has_variables = (
                    "{{" in schema.filename_template and "}}" in schema.filename_template
                )

            if has_variables or not schema.filename_template:
                # Multi-file schema or no filename template: ls the directory
                ls_dirs.add(dir_path)
            else:
                # Single-file schema: directly read the specific file
                file_uri = f"{dir_path}/{schema.filename_template}"
                read_files.add(file_uri)

        call_id_seq = 0
        # Step 2: Execute search for each ls directory (instead of ls)
        read_tool = get_tool("read")
        search_tool = get_tool("search")

        # 首先读取所有 .overview.md 文件（截断以避免窗口过大）
        # 为 overview 读取创建一个基本的 tool_ctx
        tool_ctx = ToolContext(
            request_ctx=ctx, transaction_handle=transaction_handle, default_search_uris=[]
        )

        # for overview_uri in overview_files:
        #     try:
        #         result_str = await read_tool.execute(viking_fs, tool_ctx, uri=overview_uri)
        #         add_tool_call_pair_to_messages(
        #             messages=pre_fetch_messages,
        #             call_id=call_id_seq,
        #             tool_name="read",
        #             params={"uri": overview_uri},
        #             result=result_str,
        #         )
        #         call_id_seq += 1
        #     except Exception as e:
        #         logger.warning(f"Failed to read .overview.md: {e}")

        # 在每个之前 ls 的目录内执行 search（替换原来的 ls 操作）
        if search_tool and viking_fs and ls_dirs:
            for dir_uri in ls_dirs:
                # 创建只在该目录搜索的 tool_ctx
                tool_ctx_dir = ToolContext(
                    request_ctx=ctx,
                    transaction_handle=transaction_handle,
                    default_search_uris=[dir_uri],
                )
                try:
                    search_result = await search_tool.execute(
                        viking_fs=viking_fs,
                        ctx=tool_ctx_dir,
                        query="[Keywords]",
                    )
                    # 处理搜索结果
                    if isinstance(search_result, list):
                        result_value = [m.get("uri", "") for m in search_result]
                    elif isinstance(search_result, dict):
                        if "error" in search_result:
                            result_value = f"Error: {search_result.get('error')}"
                        else:
                            result_value = [
                                m.get("uri", "") for m in search_result.get("memories", [])
                            ]
                    else:
                        result_value = []

                    add_tool_call_pair_to_messages(
                        messages=pre_fetch_messages,
                        call_id=call_id_seq,
                        tool_name="search",
                        params={"query": "[Keywords]", "search_uri": dir_uri},
                        result=result_value,
                    )
                    call_id_seq += 1
                except Exception as e:
                    logger.warning(f"Failed to search in {dir_uri}: {e}")

        # 读取单文件 schema 的文件（只对非 add_only 模式）
        for file_uri in read_files:
            try:
                result_str = await read_tool.execute(viking_fs, tool_ctx, uri=file_uri)
                add_tool_call_pair_to_messages(
                    messages=pre_fetch_messages,
                    call_id=call_id_seq,
                    tool_name="read",
                    params={"uri": file_uri},
                    result=result_str,
                )
                call_id_seq += 1
            except Exception as e:
                logger.warning(f"Failed to read {file_uri}: {e}")

        return pre_fetch_messages

    def get_tools(self) -> List[str]:
        """获取可用的工具列表 - 会话场景只使用 read"""
        return ["read"]

    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        """获取需要参与的 memory schemas（内部自动加载）"""
        return self._get_registry().list_all(include_disabled=False)

    def get_schema_directories(self) -> List[str]:
        """返回需要加载的 schema 目录"""
        if self._schema_directories is None:
            builtin_dir = os.path.join(
                os.path.dirname(__file__), "..", "..", "prompts", "templates", "memory"
            )
            config = get_openviking_config()
            custom_dir = config.memory.custom_templates_dir
            self._schema_directories = [builtin_dir]
            if custom_dir:
                custom_dir_expanded = os.path.expanduser(custom_dir)
                if os.path.exists(custom_dir_expanded):
                    self._schema_directories.append(custom_dir_expanded)
        return self._schema_directories

    def _get_registry(self) -> MemoryTypeRegistry:
        """内部获取 registry（自动在初始化时加载）"""
        if self._registry is None:
            # MemoryTypeRegistry 在 __init__ 时自动加载 schemas
            self._registry = MemoryTypeRegistry(load_schemas=True)
        return self._registry
