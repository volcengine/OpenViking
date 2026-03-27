# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Simplified ReAct orchestrator for memory updates - single LLM call with tool use.

Reference: bot/vikingbot/agent/loop.py AgentLoop structure
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from openviking.message import Message
from openviking.models.vlm.base import VLMBase
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryOperations
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.schema_model_generator import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.tools import (
    add_tool_call_pair_to_messages,
    get_tool,
    get_tool_schemas,
)
from openviking.session.memory.utils import (
    collect_allowed_directories,
    detect_language_from_conversation,
    extract_json_from_markdown,
    parse_json_with_stability,
    parse_memory_file_with_fields,
    pretty_print_messages,
    truncate_content,
    validate_operations_uris,
)
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)



class MemoryReAct:
    """
    Simplified ReAct orchestrator for memory updates.

    Workflow:
    0. Pre-fetch: System performs ls + read .overview.md + search
    1. LLM call with tools: Model decides to either use tools OR output final operations
    2. If tools used: Execute and continue loop
    3. If operations output: Return and finish
    """

    def __init__(
        self,
        vlm: VLMBase,
        viking_fs: Optional[VikingFS] = None,
        model: Optional[str] = None,
        max_iterations: int = 3,
        ctx: Optional[RequestContext] = None,
        registry: Optional[MemoryTypeRegistry] = None,
    ):
        """
        Initialize the MemoryReAct.

        Args:
            vlm: VLM instance (from openviking.models.vlm.base)
            viking_fs: VikingFS instance for storage operations
            model: Model name to use
            max_iterations: Maximum number of ReAct iterations (default: 5)
            ctx: Request context
            registry: Optional MemoryTypeRegistry - if not provided, will be created
        """
        self.vlm = vlm
        self.viking_fs = viking_fs or get_viking_fs()
        self.model = model or self.vlm.model
        self.max_iterations = max_iterations
        self.ctx = ctx

        # Initialize schema registry and generators
        if registry is not None:
            self.registry = registry
        else:
            import os
            schemas_dir = os.path.join(os.path.dirname(__file__), "..", "..", "prompts", "templates", "memory")
            self.registry = MemoryTypeRegistry()
            self.registry.load_from_directory(schemas_dir)
        self.schema_model_generator = SchemaModelGenerator(self.registry)
        self.schema_prompt_generator = SchemaPromptGenerator(self.registry)

        # Pre-generate models and JSON schema
        self.schema_model_generator.generate_all_models()
        self._json_schema = self.schema_model_generator.get_llm_json_schema()

        # Track files read during ReAct for refetch detection
        self._read_files: Set[str] = set()
        self._output_language: str = "en"
        # Transaction handle for file locking
        self._transaction_handle = None

    def _get_all_memory_schema_dirs(self) -> List[str]:
        """
        Get all memory schema directories

        Returns:
            List of all memory schema directories
        """
        dirs = []

        for schema in self.registry.list_all(include_disabled=False):
            if not schema.directory:
                continue

            # Replace variables in directory path with actual user/agent space
            user_space = self.ctx.user.user_space_name() if self.ctx and self.ctx.user else "default"
            agent_space = self.ctx.user.agent_space_name() if self.ctx and self.ctx.user else "default"
            dir_path = schema.directory.replace("{user_space}", user_space).replace("{agent_space}", agent_space)

            # Convert Viking URI to AGFS path using VikingFS's internal path conversion
            # This is necessary because LockManager/PathLock work directly with AGFSClient
            # which expects /local/{account_id}/ format paths
            dir_path = self.viking_fs._uri_to_path(dir_path, self.ctx)

            if dir_path not in dirs:
                dirs.append(dir_path)

        return dirs

    def _assemble_conversation(self, messages: List[Message], latest_archive_overview: str = "") -> str:
        """Assemble conversation string from messages.

        This method converts a list of Message objects into a formatted string
        that can be used by the ReAct loop.

        Args:
            messages: List of Message objects
            latest_archive_overview: Optional overview from previous archive for context

        Returns:
            Formatted conversation string
        """
        import json
        from openviking.message.part import ToolPart

        conversation_sections: List[str] = []

        # Add previous archive overview if provided
        # if latest_archive_overview:
        #     conversation_sections.append(f"## Previous Archive Overview\n{latest_archive_overview}")

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
            "\n".join([f"[{idx}][{msg.role}]: {format_message_with_parts(msg)}" for idx, msg in enumerate(messages)])
        )

        return "\n\n".join(section for section in conversation_sections if section)

    async def _pre_fetch_context(self, messages: List[Message]) -> Dict[str, Any]:
        """
        Pre-fetch context based on activated schemas.

        Optimized logic:
        - For multi-file schemas (filename_template has variables): ls the directory
        - For single-file schemas (filename_template no variables): directly read the file
        - No longer ls the root memories directory
        - For operation_mode = "add_only": skip ls and search, only read .overview.md

        Args:
            messages: List of Message objects for extracting user query

        Returns:
            Pre-fetched context with directories, summaries, and search_results
        """
        from openviking.session.memory.tools import get_tool
        pre_fetch_messages = []

        # Step 1: Separate schemas into multi-file (ls) and single-file (direct read)
        ls_dirs = set()  # directories to ls (for multi-file schemas)
        read_files = set()  # files to read directly (for single-file schemas)
        overview_files = set()  # .overview.md files to read

        for schema in self.registry.list_all(include_disabled=False):
            if not schema.directory:
                continue

            # Replace variables in directory path with actual user/agent space
            user_space = self.ctx.user.user_space_name() if self.ctx and self.ctx.user else "default"
            agent_space = self.ctx.user.agent_space_name() if self.ctx and self.ctx.user else "default"
            dir_path = schema.directory.replace("{user_space}", user_space).replace("{agent_space}", agent_space)

            # Always add .overview.md to read list
            overview_files.add(f"{dir_path}/.overview.md")

            # 根据 operation_mode 决定是否需要 ls 和读取其他文件
            if schema.operation_mode == "add_only":
                # 只新增，不需要查看之前的记忆列表，只需要读取 .overview.md
                continue

            # Check if filename_template has variables (contains {xxx})
            has_variables = False
            if schema.filename_template:
                has_variables = "{" in schema.filename_template and "}" in schema.filename_template

            if has_variables or not schema.filename_template:
                # Multi-file schema or no filename template: ls the directory
                ls_dirs.add(dir_path)
            else:
                # Single-file schema: directly read the specific file
                file_uri = f"{dir_path}/{schema.filename_template}"
                read_files.add(file_uri)

        call_id_seq = 0
        # Step 2: Execute ls for multi-file schema directories in parallel
        ls_tool = get_tool("ls")
        read_tool = get_tool("read")
        from openviking.server.identity import ToolContext

        # 获取 search URIs
        user_space = self.ctx.user.user_space_name() if self.ctx and self.ctx.user else "default"
        agent_space = self.ctx.user.agent_space_name() if self.ctx and self.ctx.user else "default"
        search_uris = self.registry.list_search_uris(user_space, agent_space)

        tool_ctx = ToolContext(
            request_ctx=self.ctx,
            transaction_handle=self._transaction_handle,
            default_search_uris=search_uris
        )

        # 首先读取所有 .overview.md 文件（截断以避免窗口过大）
        for overview_uri in overview_files:
            try:
                result_str = await read_tool.execute(self.viking_fs, tool_ctx, uri=overview_uri)
                add_tool_call_pair_to_messages(
                    messages=pre_fetch_messages,
                    call_id=call_id_seq,
                    tool_name='read',
                    params={
                        "uri": overview_uri
                    },
                    result=result_str
                )
                call_id_seq += 1
            except Exception as e:
                logger.warning(f"Failed to read .overview.md: {e}")

        # 然后执行 ls 操作（只对非 add_only 模式）
        if ls_tool and self.viking_fs and ls_dirs:
            for dir_uri in ls_dirs:
                try:
                    result_str = await ls_tool.execute(self.viking_fs, tool_ctx, uri=dir_uri)
                    add_tool_call_pair_to_messages(
                        messages=pre_fetch_messages,
                        call_id=call_id_seq,
                        tool_name='ls',
                        params={
                            "uri": dir_uri
                        },
                        result=result_str
                    )
                    call_id_seq += 1
                except Exception as e:
                    logger.warning(f"Failed to ls {dir_uri}: {e}")

        # 读取单文件 schema 的文件（只对非 add_only 模式）
        for file_uri in read_files:
            try:
                result_str = await read_tool.execute(self.viking_fs, tool_ctx, uri=file_uri)
                add_tool_call_pair_to_messages(
                    messages=pre_fetch_messages,
                    call_id=call_id_seq,
                    tool_name='read',
                    params={
                        "uri": file_uri
                    },
                    result=result_str
                )
                call_id_seq += 1
            except Exception as e:
                logger.warning(f"Failed to read {file_uri}: {e}")

        # Step 3: Search for relevant memories based on user messages in conversation
        # 只对非 add_only 模式执行搜索
        search_tool = get_tool("search")
        logger.debug(f"Search tool available: {search_tool is not None}")
        logger.debug(f"VikingFS available: {self.viking_fs is not None}")
        logger.debug(f"Context available: {self.ctx is not None}")

        if search_tool and self.viking_fs and self.ctx:
            # 检查是否有非 add_only 模式的 schema 需要搜索
            has_non_add_only_schemas = any(
                schema.operation_mode != "add_only"
                for schema in self.registry.list_all(include_disabled=False)
            )
            logger.info(f"  Has non add-only schemas: {has_non_add_only_schemas}")

            # 打印所有启用的记忆类型及其 operation_mode
            enabled_schemas = self.registry.list_all(include_disabled=False)
            logger.info(f"  Enabled schemas ({len(enabled_schemas)}):")
            for schema in enabled_schemas:
                logger.info(f"    - {schema.memory_type}: operation_mode={schema.operation_mode}, enabled={schema.enabled}")

            if has_non_add_only_schemas:
                try:
                    # Extract only user messages from messages (List[Dict])
                    user_messages = []
                    for msg in messages:
                        if msg.role == "user":
                            user_messages.append(msg.content)
                    user_query = " ".join(user_messages)

                    # 执行搜索
                    search_result = None
                    search_error = None
                    try:
                        search_result = await search_tool.execute(
                            viking_fs=self.viking_fs,
                            ctx=tool_ctx,
                            query=user_query or "",
                        )
                    except Exception as e:
                        search_error = str(e)
                        logger.warning(f"Search execution failed: {e}")

                    # 根据搜索结果确定记录内容
                    if search_error:
                        result_value = f"Error: {search_error}"
                    elif isinstance(search_result, list):
                        result_value = [m.get("uri", "") for m in search_result]
                    elif isinstance(search_result, dict):
                        if "error" in search_result:
                            result_value = f"Error: {search_result.get('error')}"
                        else:
                            result_value = [m.get("uri", "") for m in search_result.get("memories", [])]
                    else:
                        result_value = []

                    add_tool_call_pair_to_messages(
                        messages=pre_fetch_messages,
                        call_id=call_id_seq,
                        tool_name='search',
                        params={"query": "[Keywords from Conversation]"},
                        result=result_value
                    )
                    call_id_seq += 1
                except Exception as e:
                    logger.warning(f"Pre-fetch search failed: {e}")

        return pre_fetch_messages


    async def run(
        self,
        messages: List[Message],
        latest_archive_overview: str = "",
    ) -> Tuple[Optional[MemoryOperations], List[Dict[str, Any]]]:
        """
        Run the simplified ReAct loop for memory updates.

        Args:
            messages: List of Message objects from the conversation
            latest_archive_overview: Optional overview from previous archive for context

        Returns:
            Tuple of (final MemoryOperations, tools_used list)
        """
        # Assemble conversation from messages
        conversation = self._assemble_conversation(messages, latest_archive_overview)

        iteration = 0
        max_iterations = self.max_iterations
        final_operations = None
        tools_used: List[Dict[str, Any]] = []

        # Detect output language from conversation
        config = get_openviking_config()
        fallback_language = (config.language_fallback or "en").strip() or "en"
        self._output_language = detect_language_from_conversation(
            conversation, fallback_language=fallback_language
        )
        logger.info(f"Detected output language for memory ReAct: {self._output_language}")

        # Pre-fetch context internally
        tool_call_messages = await self._pre_fetch_context(messages)

        # Reset read files tracking for this run
        self._read_files.clear()

        messages = self._build_initial_messages(conversation, tool_call_messages, self._output_language)

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"ReAct iteration {iteration}/{max_iterations}")

            # Check if this is the last iteration - force final result
            is_last_iteration = iteration >= max_iterations

            # If last iteration, add a message telling the model to return result directly
            if is_last_iteration:
                messages.append({
                    "role": "user",
                    "content": "You have reached the maximum number of tool call iterations. Do not call any more tools - return your final result directly now."
                })

            # Call LLM with tools - model decides: tool calls OR final operations
            tool_calls, operations = await self._call_llm(messages, force_final=is_last_iteration)
            # If model returned final operations, check if refetch is needed
            if operations is not None:
                # Check if any write_uris target existing files that weren't read
                refetch_uris = await self._check_unread_existing_files(operations)
                if refetch_uris:
                    logger.info(f"Found unread existing files: {refetch_uris}, refetching...")
                    # Add refetch results to messages and continue loop
                    await self._add_refetch_results_to_messages(messages, refetch_uris)
                    # Allow one extra iteration for refetch
                    if iteration >= max_iterations:
                        max_iterations += 1
                        logger.info(f"Extended max_iterations to {max_iterations} for refetch")
                    # Clear operations to force another iteration
                    operations = None
                    # Continue to next iteration
                    continue

                final_operations = operations
                break

            # If no tool calls either, continue to next iteration (don't break!)
            if not tool_calls:
                logger.warning(f"LLM returned neither tool calls nor operations (iteration {iteration}/{max_iterations})")
                # If it's the last iteration, use empty operations
                if is_last_iteration:
                    final_operations = MemoryOperations()
                    break
                # Otherwise continue and try again
                continue

            # Execute all tool calls in parallel
            async def execute_single_tool_call(idx: int, tool_call):
                """Execute a single tool call."""
                result = await self._execute_tool(tool_call)
                return idx, tool_call, result

            action_tasks = [
                execute_single_tool_call(idx, tool_call)
                for idx, tool_call in enumerate(tool_calls)
            ]
            results = await self._execute_in_parallel(action_tasks)

            # Process results and add to messages
            for _idx, tool_call, result in results:
                tools_used.append({
                    "tool_name": tool_call.name,
                    "params": tool_call.arguments,
                    "result": result,
                })

                # Track read tool calls for refetch detection
                if tool_call.name == "read" and tool_call.arguments.get("uri"):
                    self._read_files.add(tool_call.arguments["uri"])

                add_tool_call_pair_to_messages(
                    messages,
                    call_id=tool_call.id,
                    tool_name=tool_call.name,
                    params=tool_call.arguments,
                    result=result,
                )
            # Print updated messages with tool results
            pretty_print_messages(messages)
        if final_operations is None:
            if iteration >= max_iterations:
                raise RuntimeError(f"Reached {max_iterations} iterations without completion")
            else:
                raise RuntimeError("ReAct loop completed but no operations generated")

        logger.info(f'final_operations={final_operations.model_dump_json(indent=4)}')

        return final_operations, tools_used

    def _build_initial_messages(
        self,
        conversation: str,
        tool_call_messages: List,
        output_language: str,
    ) -> List[Dict[str, Any]]:
        """Build initial messages from conversation and pre-fetched context.

        Prompt caching strategy:
        - The system prompt is cached (static across all calls)
        - Each ReAct iteration continues from the previous cached state
        """
        system_prompt = self._get_system_prompt(output_language)
        messages = [
            {
                "role": "system",
                "content": system_prompt,
                # Cache the system prompt - it's constant and will be reused
                "cache_control": {"type": "ephemeral"},
            }
        ]

        # Add pre-fetched context as tool calls
        messages.extend(tool_call_messages)

        # Get current date and day of week
        from datetime import datetime
        now = datetime.now()
        current_time = now.strftime("%Y-%m-%d %H:%M:%S")
        day_of_week = now.strftime("%A")

        messages.append({
                "role": "user",
                "content": f"""## Conversation History
**Current Time:** {current_time} ({day_of_week})

{conversation}

After exploring, analyze the conversation and output ALL memory write/edit/delete operations in a single response. Do not output operations one at a time - gather all changes first, then return them together.""",
        })
        # Print messages in a readable format
        pretty_print_messages(messages)

        return messages


    def _get_allowed_directories_list(self) -> str:
        """Get a formatted list of allowed directories for the system prompt."""
        user_space = self.ctx.user.user_space_name() if self.ctx and self.ctx.user else "default"
        agent_space = self.ctx.user.agent_space_name() if self.ctx and self.ctx.user else "default"
        allowed_dirs = collect_allowed_directories(
            self.registry.list_all(include_disabled=False),
            user_space=user_space,
            agent_space=agent_space,
        )
        if not allowed_dirs:
            return "No directories configured (this is an error)."
        return "\n".join(f"- {dir_path}" for dir_path in sorted(allowed_dirs))

    def _get_system_prompt(self, output_language: str) -> str:
        """Get the simplified system prompt."""
        import json
        schema_str = json.dumps(self._json_schema, ensure_ascii=False)

        return f"""You are a memory extraction agent. Your task is to analyze conversations and update memories.

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

## Edit Overview Files
After writing new memories, you MUST also update the corresponding .overview.md file.
- Provide memory_type to identify which directory's overview to update
- Example: {{"memory_type": "profile", "overview": "User profile overview..."}}

## Overview Format
See GenericOverviewEdit in the JSON Schema below.

## Output Format
See the complete JSON Schema below:
```json
{schema_str}
```
"""

    def _validate_operations(self, operations: MemoryOperations) -> None:
        """
        Validate that all operations have allowed URIs.

        Args:
            operations: The MemoryOperations to validate

        Raises:
            ValueError: If any operation has a disallowed URI
        """
        is_valid, errors = validate_operations_uris(
            operations,
            self.registry.list_all(include_disabled=False),
            self.registry,
            user_space="default",
            agent_space="default",
        )
        if not is_valid:
            error_msg = "Invalid memory operations:\n" + "\n".join(f"  - {err}" for err in errors)
            logger.error(error_msg)
            raise ValueError(error_msg)

    async def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        force_final: bool = False,
    ) -> Tuple[Optional[List], Optional[MemoryOperations]]:
        """
        Call LLM with tools. Returns either tool calls OR final operations.

        Args:
            messages: Message list
            force_final: If True, force model to return final result (not tool calls)

        Returns:
            Tuple of (tool_calls, operations) - one will be None, the other set
        """
        # Call LLM with tools
        tool_choice = "none" if force_final else None
        response = await self.vlm.get_completion_async(
            messages=messages,
            tools=get_tool_schemas(),
            tool_choice=tool_choice,
            max_retries=self.vlm.max_retries,
        )
        # print(f'response={response}')
        # Log cache hit info
        if hasattr(response, 'usage') and response.usage:
            usage = response.usage
            prompt_tokens = usage.get('prompt_tokens', 0)
            cached_tokens = usage.get('prompt_tokens_details', {}).get('cached_tokens', 0) if isinstance(usage.get('prompt_tokens_details'), dict) else 0
            if prompt_tokens > 0:
                cache_hit_rate = (cached_tokens / prompt_tokens) * 100
                logger.info(f"[KVCache] prompt_tokens={prompt_tokens}, cached_tokens={cached_tokens}, cache_hit_rate={cache_hit_rate:.1f}%")
            else:
                logger.info(f"[KVCache] prompt_tokens={prompt_tokens}, cached_tokens={cached_tokens}")

        # Case 1: LLM returned tool calls
        if response.has_tool_calls:
            # Format tool calls nicely for debug logging
            for tc in response.tool_calls:
                logger.info(f"[assistant tool_call] (id={tc.id}, name={tc.name})")
                logger.info(f"  {json.dumps(tc.arguments, indent=2, ensure_ascii=False)}")
            return (response.tool_calls, None)

        # Case 2: Try to parse MemoryOperations from content with stability
        content = response.content or ""
        if content:
            try:
                # print(f'LLM response content: {content}')
                logger.debug(f"[assistant]\n{content}")
                # Get the dynamically generated operations model for better type safety
                operations_model = self.schema_model_generator.create_structured_operations_model()

                # Use five-layer stable JSON parsing
                operations, error = parse_json_with_stability(
                    content=content,
                    model_class=operations_model,
                    expected_fields=['reasoning', 'write_uris', 'edit_uris', 'edit_overview_uris', 'delete_uris'],
                )

                if error is not None:
                    print(f'content={content}')
                    logger.warning(f"Failed to parse memory operations (stable parse): {error}")
                    # Fallback: try with base MemoryOperations
                    content_no_md = extract_json_from_markdown(content)
                    operations, error_fallback = parse_json_with_stability(
                        content=content_no_md,
                        model_class=MemoryOperations,
                        expected_fields=['reasoning', 'write_uris', 'edit_uris', 'edit_overview_uris', 'delete_uris'],
                    )
                    if error_fallback is not None:
                        logger.warning(f"Fallback parse also failed: {error_fallback}")
                        return (None, None)

                # Validate that all URIs are allowed
                self._validate_operations(operations)
                # print(f'Parsed operations: {operations}')
                return (None, operations)
            except Exception as e:
                print(f'Error parsing operations: {e}')
                logger.warning(f"Unexpected error parsing memory operations: {e}")

        # Case 3: No tool calls and no parsable operations
        print('No tool calls or operations parsed')
        return (None, None)

    async def _execute_tool(
        self,
        tool_call,
    ) -> Any:
        """Execute a single read action (read/search/ls/tree)."""
        if not self.viking_fs:
            return {"error": "VikingFS not available"}

        tool = get_tool(tool_call.name)
        if not tool:
            return {"error": f"Unknown tool: {tool_call.name}"}

        # 创建 ToolContext
        from openviking.server.identity import ToolContext
        tool_ctx = ToolContext(
            request_ctx=self.ctx,
            transaction_handle=self._transaction_handle
        )

        try:
            result = await tool.execute(self.viking_fs, tool_ctx, **tool_call.arguments)
            return result
        except Exception as e:
            logger.error(f"Failed to execute {tool_call.name}: {e}")
            return {"error": str(e)}

    async def _execute_in_parallel(
        self,
        tasks: List[Any],
    ) -> List[Any]:
        """Execute tasks in parallel, similar to AgentLoop."""
        return await asyncio.gather(*tasks)

    async def _check_unread_existing_files(
        self,
        operations: MemoryOperations,
    ) -> List[str]:
        """Check if write_uris target existing files that weren't read during ReAct."""
        if not operations.write_uris:
            return []

        from openviking.session.memory.utils.uri import resolve_flat_model_uri

        refetch_uris = []
        for op in operations.write_uris:
            # Resolve the flat model to URI
            try:
                uri = resolve_flat_model_uri(op, self.registry, "default", "default")
            except Exception as e:
                logger.warning(f"Failed to resolve URI for {op}: {e}")
                continue

            # Skip if already read
            if uri in self._read_files:
                continue
            # Check if file exists
            try:
                await self.viking_fs.read_file(uri, ctx=self.ctx)
                # File exists and wasn't read - need refetch
                refetch_uris.append(uri)
            except Exception:
                # File doesn't exist, no need to refetch
                pass
        return refetch_uris

    async def _add_refetch_results_to_messages(
        self,
        messages: List[Dict[str, Any]],
        refetch_uris: List[str],
    ) -> None:
        """Add existing file content as read tool results to messages."""
        # Calculate call_id based on existing tool messages
        call_id_seq = len([m for m in messages if m.get("role") == "tool"]) + 1000

        for uri in refetch_uris:
            try:
                content = await self.viking_fs.read_file(uri, ctx=self.ctx)
                parsed = parse_memory_file_with_fields(content)

                # Add as read tool call + result
                add_tool_call_pair_to_messages(
                    messages=messages,
                    call_id=call_id_seq,
                    tool_name="read",
                    params={"uri": uri},
                    result=parsed,
                )
                call_id_seq += 1

                # Mark as read
                self._read_files.add(uri)
            except Exception as e:
                logger.warning(f"Failed to refetch {uri}: {e}")

        # Add reminder message for the model
        messages.append({
            "role": "user",
            "content": "Note: The files above were automatically read because they exist and you didn't read them before deciding to write. Please consider the existing content when making write decisions. You can now output updated operations."
        })
