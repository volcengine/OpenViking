# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Simplified ReAct orchestrator for memory updates - single LLM call with tool use.

Reference: bot/vikingbot/agent/loop.py AgentLoop structure
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from openviking.models.vlm.base import VLMBase
from openviking.server.identity import RequestContext
from openviking.session.memory.schema_model_generator import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.tools import (
    MEMORY_TOOLS_REGISTRY,
    add_tool_call_pair_to_messages,
    get_tool,
)
from openviking.session.memory.utils import (
    parse_json_with_stability,
    parse_memory_file_with_fields,
    pretty_print_messages,
    validate_operations_uris,
)
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class ExtractLoop:
    """
    Simplified ReAct orchestrator for memory updates.

    Workflow:
    0. Pre-fetch: System performs ls + read .overview.md + search (via strategy)
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
        context_provider: Optional[Any] = None,  # ExtractContextProvider
    ):
        """
        Initialize the ExtractLoop.

        Args:
            vlm: VLM instance (from openviking.models.vlm.base)
            viking_fs: VikingFS instance for storage operations
            model: Model name to use
            max_iterations: Maximum number of ReAct iterations (default: 5)
            ctx: Request context
            context_provider: ExtractContextProvider - 必须提供（由 provider 加载 schema）
        """
        self.vlm = vlm
        self.viking_fs = viking_fs or get_viking_fs()
        self.model = model or self.vlm.model
        self.max_iterations = max_iterations
        self.ctx = ctx
        self.context_provider = context_provider

        # Schema 生成器（在 run() 中初始化）
        self.schema_model_generator = None
        self.schema_prompt_generator = None
        self._json_schema = None

        # 预计算：避免每次迭代重复计算
        self._tool_schemas: Optional[List[Dict[str, Any]]] = None
        self._expected_fields: Optional[List[str]] = None
        self._operations_model: Optional[Any] = None

        # Track files read during ReAct for refetch detection
        self._read_files: Set[str] = set()
        # Transaction handle for file locking
        self._transaction_handle = None

    async def run(self) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
        """
        Run the simplified ReAct loop for memory updates.

        Returns:
            Tuple of (final operations, tools_used list)
        """
        iteration = 0
        max_iterations = self.max_iterations
        final_operations = None
        tools_used: List[Dict[str, Any]] = []

        # 从 provider 获取 schemas（内部自动加载 registry）
        schemas = self.context_provider.get_memory_schemas(self.ctx)

        # 初始化 schema 生成器（使用 schemas 而非 registry）
        self.schema_model_generator = SchemaModelGenerator(schemas)
        self.schema_prompt_generator = SchemaPromptGenerator(schemas)
        self.schema_model_generator.generate_all_models()
        self._json_schema = self.schema_model_generator.get_llm_json_schema()

        # 预计算工具 schemas
        allowed_tools = self.context_provider.get_tools()
        self._tool_schemas = [
            tool.to_schema()
            for tool in MEMORY_TOOLS_REGISTRY.values()
            if tool.name in allowed_tools
        ]

        # 预计算 expected_fields
        # self._expected_fields = ["reasoning", "edit_overview_uris", "delete_uris"]
        self._expected_fields = ["delete_uris"]

        # 获取 ExtractContext（整个流程复用）
        self._extract_context = self.context_provider.get_extract_context()
        if self._extract_context is None:
            raise ValueError("Failed to get ExtractContext from provider")
        for schema in schemas:
            self._expected_fields.append(schema.memory_type)

        # 预计算 operations_model
        self._operations_model = self.schema_model_generator.create_structured_operations_model()

        # Reset read files tracking for this run
        self._read_files.clear()

        # Build initial messages from provider
        import json

        schema_str = json.dumps(self._json_schema, ensure_ascii=False)

        messages = []
        # instruction() 返回字符串，需要包装成 message 格式
        messages.append(
            {
                "role": "system",
                "content": self.context_provider.instruction(),
            }
        )
        messages.append(
            {
                "role": "system",
                "content": f"""
## Output Format
See the complete JSON Schema below:
```json
{schema_str}
```
        """,
            }
        )

        await self._mark_cache_breakpoint(messages)
        # Pre-fetch context via provider
        tool_call_messages = await self.context_provider.prefetch(
            ctx=self.ctx,
            viking_fs=self.viking_fs,
            transaction_handle=self._transaction_handle,
            vlm=self.vlm,
        )
        messages.extend(tool_call_messages)

        while iteration < max_iterations:
            iteration += 1
            tracer.info(f"ReAct iteration {iteration}/{max_iterations}")

            # Check if this is the last iteration - force final result
            is_last_iteration = iteration >= max_iterations

            # If last iteration, add a message telling the model to return result directly
            if is_last_iteration:
                messages.append(
                    {
                        "role": "user",
                        "content": "You have reached the maximum number of tool call iterations. Do not call any more tools - return your final result directly now.",
                    }
                )

            # Call LLM with tools - model decides: tool calls OR final operations
            pretty_print_messages(messages)
            tool_calls, operations = await self._call_llm(messages, force_final=is_last_iteration)

            if tool_calls:
                await self._execute_tool_calls(messages, tool_calls, tools_used)
                # Allow one extra iteration for refetch
                if iteration >= max_iterations:
                    max_iterations += 1
                    tracer.info(f"Extended max_iterations to {max_iterations} for tool call")
                continue

            # If model returned final operations, check if refetch is needed
            if operations is not None:
                # Check if any write_uris target existing files that weren't read
                refetch_uris = await self._check_unread_existing_files(operations)
                if refetch_uris:
                    tracer.info(f"Found unread existing files: {refetch_uris}, refetching...")
                    # Add refetch results to messages and continue loop
                    await self._add_refetch_results_to_messages(messages, refetch_uris)
                    # Allow one extra iteration for refetch
                    if iteration >= max_iterations:
                        max_iterations += 1
                        tracer.info(f"Extended max_iterations to {max_iterations} for refetch")

                    continue

                final_operations = operations
                break
            # If no tool calls either, continue to next iteration (don't break!)
            logger.warning(
                f"LLM returned neither tool calls nor operations (iteration {iteration}/{max_iterations})"
            )
            # If it's the last iteration, use empty operations
            if is_last_iteration:
                final_operations = self._operations_model()
                break
            # Otherwise continue and try again
            continue

        if final_operations is None:
            if iteration >= max_iterations:
                raise RuntimeError(f"Reached {max_iterations} iterations without completion")
            else:
                raise RuntimeError("ReAct loop completed but no operations generated")

        tracer.info(f"final_operations={final_operations.model_dump_json(indent=4)}")

        return final_operations, tools_used

    @tracer("extract_loop.execute_tool_calls")
    async def _execute_tool_calls(self, messages, tool_calls, tools_used):
        # Execute all tool calls in parallel
        async def execute_single_tool_call(idx: int, tool_call):
            """Execute a single tool call."""
            result = await self._execute_tool(tool_call)
            return idx, tool_call, result

        action_tasks = [
            execute_single_tool_call(idx, tool_call) for idx, tool_call in enumerate(tool_calls)
        ]
        results = await self._execute_in_parallel(action_tasks)

        # Process results and add to messages
        for _idx, tool_call, result in results:
            # Skip if arguments is None
            if tool_call.arguments is None:
                logger.warning(f"Tool call {tool_call.name} has no arguments, skipping")
                continue

            tools_used.append(
                {
                    "tool_name": tool_call.name,
                    "params": tool_call.arguments,
                    "result": result,
                }
            )

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

    def _validate_operations(self, operations: Any) -> None:
        """
        Validate that all operations have allowed URIs.

        Args:
            operations: The operations to validate

        Raises:
            ValueError: If any operation has a disallowed URI
        """
        # Get registry from provider (internal method)
        registry = self.context_provider._get_registry()
        schemas = self.context_provider.get_memory_schemas(self.ctx)

        # Use pre-initialized extract_context
        if not hasattr(self, "_extract_context") or self._extract_context is None:
            raise ValueError("ExtractContext not initialized")

        is_valid, errors = validate_operations_uris(
            operations,
            schemas,
            registry,
            user_space=self.ctx.user.user_space_name(),
            agent_space=self.ctx.user.agent_space_name(),
            extract_context=self._extract_context,
        )
        if not is_valid:
            error_msg = "Invalid memory operations:\n" + "\n".join(f"  - {err}" for err in errors)
            logger.error(error_msg)
            raise ValueError(error_msg)

    async def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        force_final: bool = False,
    ) -> Tuple[Optional[List], Optional[Any]]:
        """
        Call LLM with tools. Returns either tool calls OR final operations.

        Args:
            messages: Message list
            force_final: If True, force model to return final result (not tool calls)

        Returns:
            Tuple of (tool_calls, operations) - one will be None, the other set
        """
        # 标记 cache breakpoint
        await self._mark_cache_breakpoint(messages)

        # Call LLM with tools - use tools from strategy
        tool_choice = "none" if force_final else None

        response = await self.vlm.get_completion_async(
            messages=messages,
            tools=self._tool_schemas,
            tool_choice=tool_choice,
        )
        # print(f'response={response}')
        # Log cache hit info
        if hasattr(response, "usage") and response.usage:
            usage = response.usage
            prompt_tokens = usage.get("prompt_tokens", 0)
            cached_tokens = (
                usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                if isinstance(usage.get("prompt_tokens_details"), dict)
                else 0
            )
            if prompt_tokens > 0:
                cache_hit_rate = (cached_tokens / prompt_tokens) * 100
                tracer.info(
                    f"[KVCache] prompt_tokens={prompt_tokens}, cached_tokens={cached_tokens}, cache_hit_rate={cache_hit_rate:.1f}%"
                )
            else:
                tracer.info(
                    f"[KVCache] prompt_tokens={prompt_tokens}, cached_tokens={cached_tokens}"
                )

        # Case 1: LLM returned tool calls
        if response.has_tool_calls:
            # Format tool calls nicely for debug logging
            for tc in response.tool_calls:
                tracer.info(f"[assistant tool_call] (id={tc.id}, name={tc.name})")
                tracer.info(f"  {json.dumps(tc.arguments, indent=2, ensure_ascii=False)}")
            return (response.tool_calls, None)

        # Case 2: Try to parse operations from content with stability
        content = response.content or ""
        if content:
            try:
                # print(f'LLM response content: {content}')
                logger.debug(f"[assistant]\n{content}")

                # Use cached operations_model and expected_fields
                operations, error = parse_json_with_stability(
                    content=content,
                    model_class=self._operations_model,
                    expected_fields=self._expected_fields,
                )

                if error is not None:
                    print(f"content={content}")
                    logger.warning(f"Failed to parse memory operations: {error}")
                    return (None, None)

                # Validate that all URIs are allowed
                self._validate_operations(operations)
                return (None, operations)
            except Exception as e:
                logger.exception(f"Error parsing operations: {e}")

        # Case 3: No tool calls and no parsable operations
        print("No tool calls or operations parsed")
        return (None, None)

    @tracer("extract_loop.execute_tool", ignore_result=False)
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

        tool_ctx = ToolContext(request_ctx=self.ctx, transaction_handle=self._transaction_handle)

        try:
            tracer.info(f"tool_call.arguments={tool_call.arguments}")
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
        operations: Any,
    ) -> List[str]:
        """Check if write operations target existing files that weren't read during ReAct."""
        memory_type_fields = getattr(operations, "_memory_type_fields", None)
        if not memory_type_fields:
            return []

        from openviking.session.memory.utils.uri import resolve_flat_model_uri

        registry = self.context_provider._get_registry()
        refetch_uris = []

        for field_name in memory_type_fields:
            value = getattr(operations, field_name, None)
            if value is None:
                continue
            items = value if isinstance(value, list) else [value]
            for item in items:
                # Convert to dict
                item_dict = dict(item) if hasattr(item, "model_dump") else dict(item)
                try:
                    uri = resolve_flat_model_uri(
                        item_dict,
                        registry,
                        user_space=self.ctx.user.user_space_name(),
                        agent_space=self.ctx.user.agent_space_name(),
                        memory_type=field_name,
                        extract_context=self._extract_context,
                    )
                except Exception as e:
                    logger.warning(f"Failed to resolve URI for {item}: {e}")
                    continue

                if uri in self._read_files:
                    continue
                try:
                    await self.viking_fs.read_file(uri, ctx=self.ctx)
                    refetch_uris.append(uri)
                except Exception:
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
        messages.append(
            {
                "role": "user",
                "content": "Note: The files above were automatically read because they exist and you didn't read them before deciding to write. Please consider the existing content when making write decisions. You can now output updated operations.",
            }
        )

    async def _mark_cache_breakpoint(self, messages):
        # 支持 dict 消息和 object 消息
        last_msg = messages[-1]
        last_msg["cache_control"] = {"type": "ephemeral"}
