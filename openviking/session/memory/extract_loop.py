# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Simplified ReAct orchestrator for memory updates - single LLM call with tool use.

Reference: bot/vikingbot/agent/loop.py AgentLoop structure
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from openviking.models.vlm.base import ToolCall, VLMBase
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import (
    MemoryFileContent,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.schema_model_generator import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.tools import (
    MEMORY_TOOLS_REGISTRY,
    add_tool_call_pair_to_messages,
)
from openviking.session.memory.utils import (
    parse_json_with_stability,
    parse_memory_file_with_fields,
    pretty_print_messages,
)
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.session.memory.utils.uri import supplement_operation_uris
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking.telemetry import bind_telemetry_stage, tracer
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
        isolation_handler: MemoryIsolationHandler = None,
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
        # Use provided isolation_handler or create one in run()
        self._isolation_handler = isolation_handler
        # Track format error retry (max 1 retry)
        self._format_retry_count = 0

        # Schema 生成器（在 run() 中初始化）
        self.schema_model_generator = None
        self.schema_prompt_generator = None

        # 预计算：避免每次迭代重复计算
        self._tool_schemas: Optional[List[Dict[str, Any]]] = None
        self._expected_fields: Optional[List[str]] = None
        self._operations_model: Optional[Any] = None

        # Transaction handle for file locking
        self._transaction_handle = None
        # Flag to disable tools in next iteration after unknown tool error
        self._disable_tools_for_iteration = False

        self._tool_ctx = None

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
        # Reset format retry counter for each run
        self._format_retry_count = 0

        # 从 provider 获取 schemas（内部自动加载 registry）
        schemas = self.context_provider.get_memory_schemas(self.ctx)

        # 初始化 schema 生成器（使用 schemas 而非 registry）
        self.schema_model_generator = SchemaModelGenerator(schemas)
        self.schema_prompt_generator = SchemaPromptGenerator(schemas)
        self.schema_model_generator.generate_all_models()

        # 预计算工具 schemas
        allowed_tools = self.context_provider.get_tools()
        self._tool_schemas = [
            tool.to_schema()
            for tool in MEMORY_TOOLS_REGISTRY.values()
            if tool.name in allowed_tools
        ]

        # 预计算 expected_fields
        self._expected_fields = ["delete_uris"]

        # 获取 ExtractContext（整个流程复用）
        self._extract_context = self.context_provider.get_extract_context()
        if self._extract_context is None:
            raise ValueError("Failed to get ExtractContext from provider")
        for schema in schemas:
            self._expected_fields.append(f"{schema.memory_type}")

        # 预计算 operations_model
        role_scope = self._isolation_handler.get_read_scope() if self._isolation_handler else None

        self._operations_model = self.schema_model_generator.create_structured_operations_model(
            role_scope
        )

        json_schema = self._operations_model.model_json_schema()

        # Build initial messages from provider
        import json

        schema_str = json.dumps(json_schema, ensure_ascii=False)

        messages = []
        messages.append(
            {
                "role": "system",
                "content": f"""
{self.context_provider.instruction()}

## Output Format
The final output of the model must strictly follow the JSON Schema format shown below:
```json
{schema_str}
```
        """,
            }
        )

        await self._mark_cache_breakpoint(messages)
        # Pre-fetch context via provider
        tool_call_messages = await self.context_provider.prefetch()
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
                        "content": self._build_final_operations_instruction(),
                    }
                )

            # Call LLM with tools - model decides: tool calls OR final operations
            pretty_print_messages(messages)

            tool_calls, operations = await self._call_llm(messages)

            if tool_calls:
                has_unknown_tool = await self._execute_tool_calls(messages, tool_calls, tools_used)
                # If model called an unknown tool, disable tools in next iteration
                if has_unknown_tool:
                    self._disable_tools_for_iteration = True
                    tracer.info("Unknown tool called, will disable tools in next iteration")
                # Allow one extra iteration for refetch
                if iteration >= max_iterations:
                    max_iterations += 1
                    self._disable_tools_for_iteration = True
                    tracer.info(f"Extended max_iterations to {max_iterations} for tool call")
                continue

            # If model returned final operations, check if refetch is needed
            if operations is not None:
                final_operations = await self.resolve_operations(operations)
                # Check if any write_uris target existing files that weren't read
                refetch_uris = await self._check_unread_existing_files(final_operations)
                if refetch_uris:
                    tracer.info(f"Found unread existing files: {refetch_uris}, refetching...")
                    # Add refetch results to messages and continue loop
                    await self._add_refetch_results_to_messages(messages, refetch_uris)
                    # Allow one extra iteration for refetch
                    if iteration >= max_iterations:
                        max_iterations += 1
                        tracer.info(f"Extended max_iterations to {max_iterations} for refetch")

                    continue
                break
            # If no tool calls either, continue to next iteration (don't break!)
            logger.warning(
                f"LLM returned neither tool calls nor operations (iteration {iteration}/{max_iterations})"
            )
            # Add format error message if parse failed (max 1 retry)
            if self._format_retry_count == 0:
                self._format_retry_count += 1
                max_iterations += 1
                tracer.info(f"Extended max_iterations to {max_iterations} for format retry")
                self._add_format_error_message(messages)

            # If it's the last iteration, fail instead of silently treating an
            # unparseable final response as "no memory operations".
            if iteration >= max_iterations:
                raise RuntimeError(
                    "Memory extraction final response could not be parsed as JSON operations"
                )

            self._disable_tools_for_iteration = True
            continue

        if final_operations is None:
            if iteration >= max_iterations:
                raise RuntimeError(f"Reached {max_iterations} iterations without completion")
            else:
                raise RuntimeError("ReAct loop completed but no operations generated")

        tracer.info(f"final_operations={final_operations.model_dump_json(indent=4)}")

        return final_operations, tools_used

    async def resolve_operations(self, operations) -> ResolvedOperations:
        tracer.info(f"operations={JsonUtils.dumps(operations)}")
        upsert_operations: List[ResolvedOperation] = []
        delete_file_contents: List[MemoryFileContent] = []
        errors: List[str] = []

        # 获取 registry
        registry = self.context_provider._get_registry()
        role_scope = self._isolation_handler.get_read_scope()

        # 遍历每个 memory_type 字段
        for schema in self.context_provider.get_memory_schemas(self.ctx):
            memory_type = schema.memory_type
            value = getattr(operations, memory_type, None)
            if value is None:
                continue

            # 统一转为列表
            items = value if isinstance(value, list) else [value]

            for item in items:
                # 转换为 dict
                item_dict = dict(item)
                item_dict["memory_type"] = memory_type
                # 填充 user_id 和 agent_id
                self._isolation_handler.fill_role_ids(item_dict, role_scope=role_scope)

                # 构建 ResolvedOperation
                # 注意：此时 uris 为空，稍后由 supplement_operation_uris 填充

                resolved_op = ResolvedOperation(
                    old_memory_file_content=None,
                    memory_fields=item_dict,
                    memory_type=memory_type,
                    uris=[],
                )
                upsert_operations.append(resolved_op)

        # 处理 delete_uris - 转换为 delete_file_contents
        delete_uris_raw = getattr(operations, "delete_uris", []) or []
        for uri_str in delete_uris_raw:
            uri_str = uri_str.strip()
            if not uri_str:
                continue
            # 尝试从已读取的文件内容中获取
            old_content = self.context_provider.read_file_contents.get(uri_str)
            if old_content:
                delete_file_contents.append(old_content)

        # 构建 ResolvedOperations
        resolved = ResolvedOperations(
            upsert_operations=upsert_operations,
            delete_file_contents=delete_file_contents,
            errors=errors,
        )
        # 调用 supplement_operation_uris 填充 uris
        if self._isolation_handler:
            supplement_operation_uris(
                operations=resolved,
                registry=registry,
                extract_context=self._extract_context,
                isolation_handler=self._isolation_handler,
            )

        # 填充 old_memory_file_content：从 _read_file_contents 获取已读取的文件内容
        for op in upsert_operations:
            for uri in op.uris:
                old_content = self.context_provider.read_file_contents.get(uri)
                if old_content and op.old_memory_file_content is None:
                    op.old_memory_file_content = old_content
                    break

        return resolved

    @tracer("extract_loop.execute_tool_calls")
    async def _execute_tool_calls(self, messages, tool_calls, tools_used) -> bool:
        """
        Execute tool calls in parallel.

        Returns:
            True if any tool call returned "Unknown tool" error, indicating
            the model should not receive tools in the next iteration.
        """

        # Execute all tool calls in parallel
        async def execute_single_tool_call(idx: int, tool_call):
            """Execute a single tool call."""
            result = await self.context_provider.execute_tool(tool_call)
            return idx, tool_call, result

        action_tasks = [
            execute_single_tool_call(idx, tool_call) for idx, tool_call in enumerate(tool_calls)
        ]
        results = await self._execute_in_parallel(action_tasks)

        has_unknown_tool = False

        # Process results and add to messages
        for _idx, tool_call, result in results:
            # Check for unknown tool error
            if isinstance(result, dict) and result.get("error", "").startswith("Unknown tool:"):
                has_unknown_tool = True
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

            add_tool_call_pair_to_messages(
                messages,
                call_id=tool_call.id,
                tool_name=tool_call.name,
                params=tool_call.arguments,
                result=result,
            )

        return has_unknown_tool

    async def _call_llm(
        self, messages: List[Dict[str, Any]]
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
        tools = None
        tool_choice = None
        if not self._disable_tools_for_iteration and self._tool_schemas:
            tools = self._tool_schemas
            tool_choice = "auto"
        with bind_telemetry_stage("memory_extract"):
            response = await self.vlm.get_completion_async(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        tracer.info(f"response={response}")
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
            try:
                from openviking.metrics.datasources.cache import CacheEventDataSource

                if int(cached_tokens or 0) > 0:
                    CacheEventDataSource.record_hit("L2")
                else:
                    CacheEventDataSource.record_miss("L2")
            except Exception:
                pass
            if prompt_tokens > 0:
                cache_hit_rate = (cached_tokens / prompt_tokens) * 100
                tracer.info(
                    f"[KVCache] prompt_tokens={prompt_tokens}, cached_tokens={cached_tokens}, cache_hit_rate={cache_hit_rate:.1f}%"
                )
            else:
                tracer.info(
                    f"[KVCache] prompt_tokens={prompt_tokens}, cached_tokens={cached_tokens}"
                )

        # Case 0: Handle string response (when tools are not provided) or None
        if response is None:
            content = ""
        elif isinstance(response, str):
            # When tools=None, VLM returns string instead of VLMResponse
            content = response
        # Case 1: LLM returned tool calls
        elif response.has_tool_calls:
            # Format tool calls nicely for debug logging
            for tc in response.tool_calls:
                tracer.info(f"[assistant tool_call] (id={tc.id}, name={tc.name})")
                tracer.info(f"  {json.dumps(tc.arguments, indent=2, ensure_ascii=False)}")
            return (response.tool_calls, None)
        else:
            # Case 2: VLMResponse without tool calls - get content from response
            content = response.content or ""

        # Parse operations from content
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

                return (None, operations)
            except Exception as e:
                logger.exception(f"Error parsing operations: {e}")

        # Case 3: No tool calls and no parsable operations
        print("No tool calls or operations parsed")
        return (None, None)

    async def _execute_in_parallel(
        self,
        tasks: List[Any],
    ) -> List[Any]:
        """Execute tasks in parallel, similar to AgentLoop."""
        return await asyncio.gather(*tasks)

    async def _check_unread_existing_files(self, operations: ResolvedOperations) -> Dict:

        refetch_uris = {}
        for operation in operations.upsert_operations:
            for uri in operation.uris:
                if uri in self.context_provider.read_file_contents:
                    continue
                try:
                    content = await self.context_provider.execute_tool(
                        ToolCall(id="", name="read", arguments={"uri": uri})
                    )
                    # 读取出错表示文件不存在（error dict 含 "error" key）
                    if isinstance(content, Dict) and "error" in content:
                        continue

                    # execute_tool(MemoryReadTool) 已经返回 parsed dict，直接使用
                    refetch_uris[uri] = content
                except Exception as e:
                    tracer.error("read tool execute fail", e)
        return refetch_uris

    def _add_format_error_message(self, messages: List[Dict[str, Any]]) -> None:
        """Add format error guidance message to prompt."""
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous output could not be parsed as valid JSON. "
                    "Please output ONLY a valid JSON object matching the required schema. "
                    "Do not include any explanation, markdown formatting, or text outside the JSON."
                ),
            }
        )

    def _build_final_operations_skeleton(self) -> Dict[str, List[Any]]:
        """Build an empty operations object matching the expected flat schema fields."""
        fields = ["delete_uris", *(self._expected_fields or [])]
        return {field: [] for field in dict.fromkeys(fields)}

    def _build_final_operations_instruction(self) -> str:
        """Build schema-aware final-iteration instructions for the LLM."""
        skeleton = json.dumps(
            self._build_final_operations_skeleton(),
            ensure_ascii=False,
            indent=2,
        )
        return (
            "You have reached the maximum number of tool call iterations. "
            "Do not call any more tools. Return your final result now as ONLY a valid JSON object "
            "matching the required schema. Do not include explanations or markdown. "
            "If there are no memory changes, return this exact empty-shape JSON with all fields present:\n"
            f"{skeleton}"
        )

    async def _add_refetch_results_to_messages(
        self,
        messages: List[Dict[str, Any]],
        refetch_uris: Dict[str, Any],
    ) -> None:
        """Add existing file content as read tool results to messages."""
        # Calculate call_id based on existing tool messages
        call_id_seq = len([m for m in messages if m.get("role") == "tool"]) + 1000
        for uri, parsed in refetch_uris.items():
            # Add as read tool call + result
            add_tool_call_pair_to_messages(
                messages=messages,
                call_id=call_id_seq,
                tool_name="read",
                params={"uri": uri},
                result=parsed,
            )
            call_id_seq += 1

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
