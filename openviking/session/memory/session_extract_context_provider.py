# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Extract Context Provider - 会话提取 Provider 实现

从会话消息中提取记忆的实现。
"""

import json
import os
from typing import TYPE_CHECKING, Any, Dict, List

from openviking.core.namespace import to_user_space, to_agent_space
from openviking.server.identity import RequestContext, ToolContext
from openviking.session.memory.dataclass import MemoryFileContent
from openviking.session.memory.utils.uri import render_template
from openviking.telemetry import tracer
from openviking.utils.time_utils import parse_iso_datetime
from openviking.session.memory.core import ExtractContextProvider
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler, RoleScope
from openviking.session.memory.memory_type_registry import (
    MemoryTypeRegistry,
    resolve_memory_templates_dir,
)
from openviking.session.memory.tools import (
    add_tool_call_pair_to_messages,
    get_tool,
)
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.session.memory.memory_updater import ExtractContext

logger = get_logger(__name__)


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
        self.messages = messages
        self.latest_archive_overview = latest_archive_overview
        self._output_language = self._detect_language()
        self._registry = None  # 延迟加载
        self._schema_directories = None
        self._extract_context = None  # 缓存 ExtractContext 实例
        self._isolation_handler = isolation_handler
        self._read_file_contents: Dict[str, MemoryFileContent] = {}
        # 读取 eager_prefetch 配置
        config = get_openviking_config()
        self._eager_prefetch = config.memory.eager_prefetch if config.memory else False
        self._prefetch_search_topn = config.memory.prefetch_search_topn if config.memory else 5
        self._ctx = ctx
        self._viking_fs = viking_fs
        self._transaction_handle = transaction_handle

    @property
    def read_file_contents(self) -> Dict[str, MemoryFileContent]:
        return self._read_file_contents

    def set_transaction_handle(self, handle):
        """Set transaction handle after lock is acquired."""
        self._transaction_handle = handle

    def get_extract_context(self) -> "ExtractContext":
        """获取或创建 ExtractContext 实例（缓存）"""
        from openviking.session.memory.memory_updater import ExtractContext

        if self._extract_context is None and self.messages:
            self._extract_context = ExtractContext(self.messages)
        return self._extract_context

    def _detect_language(self) -> str:
        """检测输出语言"""
        from openviking.session.memory.utils import resolve_output_language_from_conversation

        conversation = self._assemble_conversation(self.messages)
        return resolve_output_language_from_conversation(conversation)

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

        conversation_sections: List[str] = []

        def format_message_with_parts(msg: Message) -> str:
            """Format message with text parts only, skipping tool call details."""
            parts = getattr(msg, "parts", [])
            text_lines = [part.text for part in parts if hasattr(part, "text") and part.text]
            return "\n".join(text_lines) if text_lines else msg.content

        def format_message_header(msg: Message, idx: int) -> str:
            """Format message header with role and role_id."""
            role_id_display = msg.role_id if msg.role_id else msg.role
            return f"[{idx}][{msg.role}][{role_id_display}]: {format_message_with_parts(msg)}"

        conversation_sections.append(
            "\n".join([format_message_header(msg, idx) for idx, msg in enumerate(messages)])
        )

        return "\n\n".join(section for section in conversation_sections if section)

    def create_tool_context(self, default_search_uris=[]):
        tool_ctx = ToolContext(
            viking_fs=self._viking_fs,
            request_ctx=self._ctx,
            transaction_handle=self._transaction_handle,
            default_search_uris=default_search_uris,
            read_file_contents=self._read_file_contents,
        )
        return tool_ctx

    async def prefetch(self) -> List[Dict]:
        """
        执行 prefetch - 从会话消息中提取相关记忆上下文

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

        rolescope: RoleScope = self._isolation_handler.get_read_scope()
        policy = self._ctx.namespace_policy

        for schema in schemas:
            if not schema.directory:
                continue

            # 根据 operation_mode 决定是否需要 ls 和读取其他文件
            if schema.operation_mode == "add_only":
                continue

            schema_dirs = set()
            for user_id in rolescope.user_ids:
                for agent_id in rolescope.agent_ids:
                    user_space = to_user_space(policy, user_id, agent_id)
                    agent_space = to_agent_space(policy, user_id, agent_id)
                    dir_path = render_template(
                        schema.directory, {"user_space": user_space, "agent_space": agent_space}
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
        read_tool = get_tool("read")
        search_tool = get_tool("search")

        # 首先读取所有 .overview.md 文件（截断以避免窗口过大）
        # 为 overview 读取创建一个基本的 tool_ctx

        # 在每个之前 ls 的目录内执行 search（替换原来的 ls操作）
        files_to_read_from_search = []  # 收集需要读取的文件（eager_prefetch 模式）

        # 批量 search：所有目录一次搜索
        if ls_dirs:
            try:
                # 将所有目录作为 target_uri 传入（支持 List[str]）
                dir_list = list(ls_dirs)
                search_result = await search_tool.execute(
                    viking_fs=self._viking_fs,
                    ctx=self.create_tool_context(dir_list),
                    query="[Keywords]",
                )
                # 处理搜索结果
                if isinstance(search_result, list):
                    result_value = [m.get("uri", "") for m in search_result]
                    if self._eager_prefetch:
                        files_to_read_from_search.extend(result_value)
                elif isinstance(search_result, dict):
                    if "error" in search_result:
                        result_value = f"Error: {search_result.get('error')}"
                else:
                    result_value = []

                add_tool_call_pair_to_messages(
                    messages=pre_fetch_messages,
                    call_id=call_id_seq,
                    tool_name="search",
                    params={"query": "[Keywords]", "search_uri": dir_list},
                    result=result_value,
                )
                call_id_seq += 1
            except Exception as e:
                logger.warning(f"Failed to search in {ls_dirs}: {e}")

        # 读取单文件 schema 的文件（只对非 add_only 模式）
        for file_uri in read_files:
            try:
                result_str = await read_tool.execute(self.create_tool_context(), uri=file_uri)
                add_tool_call_pair_to_messages(
                    messages=pre_fetch_messages,
                    call_id=call_id_seq,
                    tool_name="read",
                    params={"uri": file_uri},
                    result=result_str,
                )
                # read_file_contents
                call_id_seq += 1
            except Exception as e:
                logger.warning(f"Failed to read {file_uri}: {e}")

        # eager_prefetch 模式：读取搜索结果 top-N
        if self._eager_prefetch and read_tool:
            # 只读取 top-N 个文件
            topn_files = files_to_read_from_search[: self._prefetch_search_topn]
            for file_uri in topn_files:
                if not file_uri:
                    continue
                try:
                    result_str = await read_tool.execute(self.create_tool_context(), uri=file_uri)
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
        return self._get_registry().list_all(include_disabled=False)

    def get_schema_directories(self) -> List[str]:
        """返回需要加载的 schema 目录"""
        if self._schema_directories is None:
            memory_templates_dir = str(resolve_memory_templates_dir())
            config = get_openviking_config()
            custom_dir = config.memory.custom_templates_dir
            self._schema_directories = [memory_templates_dir]
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
