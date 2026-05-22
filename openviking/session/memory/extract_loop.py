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
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.merge_op import MergeOp
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
    pretty_print_messages,
)
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking.telemetry import bind_telemetry_stage, tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

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
        raw_links = []
        tools_used: List[Dict[str, Any]] = []
        # Reset format retry counter for each run
        self._format_retry_count = 0
        patch_repair_count = 0

        # 从 provider 获取 schemas（内部自动加载 registry）
        schemas = self.context_provider.get_memory_schemas(self.ctx)

        # 初始化 schema 生成器（使用 schemas 而非 registry）
        output_language = self.context_provider.get_output_language()
        self.schema_model_generator = SchemaModelGenerator(
            schemas,
            template_context={"language": output_language},
        )
        self.schema_prompt_generator = SchemaPromptGenerator(
            schemas,
            template_context={"language": output_language},
        )
        self.schema_model_generator.generate_all_models()

        # 预计算工具 schemas
        allowed_tools = self.context_provider.get_tools()
        self._tool_schemas = [
            tool.to_schema()
            for tool in MEMORY_TOOLS_REGISTRY.values()
            if tool.name in allowed_tools
        ]

        # 预计算 expected_fields
        config = get_openviking_config()
        self._link_enabled = config.memory.link_enabled if config.memory else False
        self._expected_fields = ["delete_uris"]
        if self._link_enabled:
            self._expected_fields.append("links")

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
        type_descriptions = self.schema_prompt_generator.generate_type_descriptions()

        messages = []
        page_id_rules = """
## Page ID Rules
- Every memory item you create or edit MUST include "page_id".
- For existing items, use the page_id shown in read/search results.
- For new items, assign a unique page_id >= 100.
- When editing an existing item, reuse its existing page_id.
"""
        link_rules = ""
        if self._link_enabled:
            link_rules = """
## Link Rules
- Link fields `f` and `t` must reference these page_id values.
- Only create links when the relationship is meaningful and clear from the conversation. Do NOT force links between unrelated items.
"""
        messages.append(
            {
                "role": "system",
                "content": f"""
{self.context_provider.instruction()}
{type_descriptions}
{page_id_rules}
{link_rules}
## Read Format Rules
- The read tool accepts `uri`, optional `offset` (0-indexed), and optional `limit`.
- Read content is returned in Claude Code format: each visible line is prefixed with `line_number<TAB>`.
- When you copy text from read results into SEARCH/REPLACE operations, copy the exact text after the line-number prefix. Never include the line-number prefix itself in `search` or `replace`.
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

        for uri in self.context_provider.read_file_contents:
            self._extract_context.page_id_map.get_page_id(uri)

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
                final_operations, raw_links = await self.resolve_operations(operations)
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
                patch_errors = self._validate_patch_operations(final_operations)
                if patch_errors and patch_repair_count == 0:
                    patch_repair_count += 1
                    max_iterations += 1
                    self._disable_tools_for_iteration = True
                    messages.append(
                        {
                            "role": "user",
                            "content": self._build_patch_repair_instruction(patch_errors),
                        }
                    )
                    tracer.info(
                        f"Extended max_iterations to {max_iterations} for retry patch repair",
                        console=True,
                    )
                    continue
                break
            # If no tool calls either, continue to next iteration (don't break!)
            tracer.error(
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

        # Resolve links after the loop completes using the URIs already bound in resolve_operations().
        await self.finalize_operations(final_operations, raw_links)

        return final_operations, tools_used

    async def resolve_operations(self, operations) -> tuple[ResolvedOperations, List]:
        tracer.info(f"operations={JsonUtils.dumps(operations)}")
        upsert_operations: List[ResolvedOperation] = []
        delete_file_contents: List[MemoryFile] = []
        errors: List[str] = []

        role_scope = self._isolation_handler.get_read_scope()
        page_id_map = getattr(self._extract_context, "page_id_map", None)

        for schema in self.context_provider.get_memory_schemas(self.ctx):
            memory_type = schema.memory_type
            value = getattr(operations, memory_type, None)
            if value is None:
                continue

            items = value if isinstance(value, list) else [value]

            for item in items:
                item_dict = dict(item)
                item_dict["memory_type"] = memory_type
                self._isolation_handler.fill_role_ids(item_dict, role_scope=role_scope)

                page_id = item_dict.pop("page_id", None)
                resolved_op = ResolvedOperation(
                    old_memory_file_content=None,
                    memory_fields=item_dict,
                    memory_type=memory_type,
                    uris=[],
                    page_id=page_id,
                )

                if page_id is not None and page_id_map is not None:
                    resolved_uri = page_id_map.resolve(page_id)
                    if resolved_uri:
                        resolved_op.uris = [resolved_uri]
                        old_content = self.context_provider.read_file_contents.get(resolved_uri)
                        if old_content is not None:
                            resolved_op.old_memory_file_content = old_content
                            immutable_fields = {
                                field.name
                                for field in schema.fields
                                if field.merge_op != MergeOp.PATCH
                            }
                            for field_name in immutable_fields:
                                if field_name in old_content.extra_fields:
                                    resolved_op.memory_fields[field_name] = (
                                        old_content.extra_fields[field_name]
                                    )
                    else:
                        resolved_op.uris = self._isolation_handler.calculate_memory_uris(
                            memory_type_schema=schema,
                            operation=resolved_op,
                            extract_context=self._extract_context,
                        )
                else:
                    resolved_op.uris = self._isolation_handler.calculate_memory_uris(
                        memory_type_schema=schema,
                        operation=resolved_op,
                        extract_context=self._extract_context,
                    )

                upsert_operations.append(resolved_op)

        delete_uris_raw = getattr(operations, "delete_uris", []) or []
        for uri_str in delete_uris_raw:
            uri_str = uri_str.strip()
            if not uri_str:
                continue
            old_content = self.context_provider.read_file_contents.get(uri_str)
            if old_content:
                delete_file_contents.append(old_content)

        raw_links = getattr(operations, "links", None) or []
        resolved = ResolvedOperations(
            upsert_operations=upsert_operations,
            delete_file_contents=delete_file_contents,
            errors=errors,
        )

        for op in upsert_operations:
            for uri in op.uris:
                old_content = self.context_provider.read_file_contents.get(uri)
                if old_content and op.old_memory_file_content is None:
                    op.old_memory_file_content = old_content
                    break

        return resolved, raw_links

    async def finalize_operations(self, operations: ResolvedOperations, raw_links: List) -> None:
        """Register new page_ids and resolve links after refetch is complete.

        Must be called after resolve_operations() and any refetch rounds,
        so that existing files discovered by refetch get 1-99 page_ids
        instead of 100+ IDs from register_new_page_id.
        """
        if not self._link_enabled:
            return

        upsert_operations = operations.upsert_operations

        # URIs are already bound in resolve_operations() before any refetch rounds.
        # finalize_operations only consumes them to register new page_ids and resolve links.
        page_id_map = self._extract_context.page_id_map

        # Register new page_ids (100+) after URI resolution, using LLM-declared page_id
        for op in upsert_operations:
            if op.page_id is not None and op.page_id >= 100:
                for uri in op.uris:
                    page_id_map.register_new_page_id(uri, op.page_id)

        # Resolve links from WikiLink (page_ids) to StoredLink (URIs)
        resolved_links = self._resolve_links(raw_links, upsert_operations)

        operations.resolved_links = resolved_links

    def _pair_link_uris(self, from_uris: List[str], to_uris: List[str]) -> List[tuple[str, str]]:
        namespace_pairs = []
        seen_pairs = set()

        for from_uri in from_uris:
            from_namespace = from_uri.split("/memories/", 1)[0]
            for to_uri in to_uris:
                if from_uri == to_uri:
                    continue
                if from_namespace != to_uri.split("/memories/", 1)[0]:
                    continue
                pair = (from_uri, to_uri)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                namespace_pairs.append(pair)

        if namespace_pairs:
            return namespace_pairs

        all_pairs = []
        for from_uri in from_uris:
            for to_uri in to_uris:
                if from_uri == to_uri:
                    continue
                pair = (from_uri, to_uri)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                all_pairs.append(pair)
        return all_pairs

    def _resolve_links(self, raw_links: List, upsert_operations: List = None) -> List[StoredLink]:
        """Resolve WikiLinks with page_ids to StoredLinks with URIs.

        Returns a flat list of StoredLink objects. Each link is stored once.
        Links go into from_uri's "links" field; backlinks go into to_uri's "backlinks" field.
        The routing is handled by memory_updater based on which file each link belongs to.
        """
        from datetime import datetime, timezone

        if not raw_links:
            return []

        op_page_map = {}
        if upsert_operations:
            for op in upsert_operations:
                if op.page_id is None or not op.uris:
                    continue
                op_page_map.setdefault(op.page_id, [])
                for uri in op.uris:
                    if uri not in op_page_map[op.page_id]:
                        op_page_map[op.page_id].append(uri)

        page_id_map = self._extract_context.page_id_map

        if not page_id_map._id_to_uri and not op_page_map:
            return []

        resolved_links = []
        seen_links = set()
        now = datetime.now(timezone.utc).isoformat()

        for link in raw_links:
            if link.f is None or link.t is None:
                tracer.info(f"Skipping link with null page_ids: f={link.f}, t={link.t}")
                continue

            from_uris = []
            to_uris = []

            from_uri = page_id_map.resolve(link.f)
            to_uri = page_id_map.resolve(link.t)
            if from_uri:
                from_uris.append(from_uri)
            if to_uri:
                to_uris.append(to_uri)

            for uri in op_page_map.get(link.f, []):
                if uri not in from_uris:
                    from_uris.append(uri)
            for uri in op_page_map.get(link.t, []):
                if uri not in to_uris:
                    to_uris.append(uri)

            if not from_uris or not to_uris:
                tracer.info(
                    f"Skipping link with unresolved page_ids: f={link.f}, t={link.t}, "
                    f"from_uri={from_uris[0] if from_uris else None}, "
                    f"to_uri={to_uris[0] if to_uris else None}, "
                    f"op_page_map_keys={list(op_page_map.keys())}"
                )
                continue

            for from_uri, to_uri in self._pair_link_uris(from_uris, to_uris):
                link_key = (
                    from_uri,
                    to_uri,
                    link.link_type,
                    link.weight,
                    link.match_text,
                    link.description,
                )
                if link_key in seen_links:
                    continue
                seen_links.add(link_key)

                stored_link = StoredLink(
                    from_uri=from_uri,
                    to_uri=to_uri,
                    link_type=link.link_type,
                    weight=link.weight,
                    match_text=link.match_text,
                    description=link.description,
                    created_at=now,
                )
                resolved_links.append(stored_link)

        return resolved_links

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
                tracer.error(f"Tool call {tool_call.name} has no arguments, skipping")
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
        tracer.info(f"llm_response={response}")
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
                    tracer.error(f"Failed to parse memory operations: {error}")
                    return (None, None)

                return (None, operations)
            except Exception as e:
                logger.exception(f"Error parsing operations: {e}")

        # Case 3: No tool calls and no parsable operations
        tracer.error("No tool calls or operations parsed")
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

    def _validate_patch_operations(self, operations: ResolvedOperations) -> List[Dict[str, Any]]:
        from openviking.session.memory.merge_op.base import SearchReplaceBlock, StrPatch
        from openviking.session.memory.merge_op.patch_handler import apply_str_patch

        errors = []
        read_files = self.context_provider.read_file_contents or {}
        for operation in operations.upsert_operations:
            if operation.old_memory_file_content is None:
                continue
            current_content = operation.old_memory_file_content.content or ""
            target_uri = (
                operation.uris[0] if operation.uris else operation.old_memory_file_content.uri
            )
            for field_name, patch_value in operation.memory_fields.items():
                blocks = []
                if isinstance(patch_value, StrPatch):
                    blocks = patch_value.blocks
                elif isinstance(patch_value, dict) and "blocks" in patch_value:
                    for raw_block in patch_value.get("blocks", []):
                        if isinstance(raw_block, SearchReplaceBlock):
                            blocks.append(raw_block)
                        elif isinstance(raw_block, dict):
                            blocks.append(SearchReplaceBlock(**raw_block))
                if not blocks:
                    continue
                patch = StrPatch(blocks=blocks)
                try:
                    applied_content = apply_str_patch(current_content, patch)
                except Exception:
                    applied_content = current_content
                if applied_content != current_content:
                    continue
                for block in blocks:
                    search = block.search or ""
                    if not search:
                        continue
                    found_in = [
                        uri
                        for uri, memory_file in read_files.items()
                        if uri != target_uri and search in (memory_file.content or "")
                    ]
                    errors.append(
                        {
                            "uri": target_uri,
                            "page_id": operation.page_id,
                            "field": field_name,
                            "search": search,
                            "found_in_other_uris": found_in,
                        }
                    )
                    break
        if errors:
            tracer.info(f"SEARCH/REPLACE patch validation failed before apply: {errors}")
        return errors

    def _build_patch_repair_instruction(self, patch_errors: List[Dict[str, Any]]) -> str:
        details = json.dumps(patch_errors, ensure_ascii=False, indent=2)
        return (
            "SEARCH/REPLACE patch could not be applied to the target memory file. "
            "The SEARCH text must be copied exactly from the read result of the file bound to that operation's page_id. "
            "Do not use SEARCH text from the conversation or from another page. "
            "If you copy from numbered read output, exclude the `line_number<TAB>` prefix from SEARCH and REPLACE text. "
            "If found_in_other_uris is non-empty, diagnose this as a possible page_id mismatch and choose the correct target page_id or rewrite the patch for the current page_id; do not silently move the patch. "
            "Regenerate the complete operations JSON, including previous successful operations and fixed failed operations. "
            "Output ONLY the complete JSON object matching the required schema.\n\n"
            f"Failed patch operations:\n{details}"
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
        # last_msg = messages[-1]
        # last_msg["cache_control"] = {"type": "ephemeral"}

        # 暂时注释掉，不确定对所有模型的影响
        pass
