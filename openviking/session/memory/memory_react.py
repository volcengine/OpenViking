# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Simplified ReAct orchestrator for memory updates - single LLM call with tool use.

Reference: bot/vikingbot/agent/loop.py AgentLoop structure
"""

import asyncio
import json
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from openviking.models.vlm.base import VLMBase, VLMResponse
from openviking.server.identity import RequestContext
from openviking.session.memory.utils import (
    collect_allowed_directories,
    detect_language_from_conversation,
    extract_json_from_markdown,
    parse_json_with_stability,
    parse_memory_file_with_fields,
    pretty_print_messages,
    validate_operations_uris,
)
from openviking.session.memory.dataclass import MemoryOperations
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.schema_model_generator import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.tools import (
    get_tool,
    get_tool_schemas,
    add_tool_call_pair_to_messages,
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
        max_iterations: int = 5,
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

    async def _pre_fetch_context(self, conversation: str) -> Dict[str, Any]:
        """
        Pre-fetch context based on activated schemas.

        Optimized logic:
        - For multi-file schemas (filename_template has variables): ls the directory
        - For single-file schemas (filename_template no variables): directly read the file
        - No longer ls the root memories directory

        Args:
            conversation: Conversation history for search query

        Returns:
            Pre-fetched context with directories, summaries, and search_results
        """
        from openviking.session.memory.tools import get_tool
        messages = []

        # Step 1: Separate schemas into multi-file (ls) and single-file (direct read)
        ls_dirs = set()  # directories to ls (for multi-file schemas)
        read_files = set()  # files to read directly (for single-file schemas)

        for schema in self.registry.list_all(include_disabled=False):
            if not schema.directory:
                continue

            # Replace variables in directory path with actual user/agent space
            user_space = self.ctx.user.user_space_name() if self.ctx and self.ctx.user else "default"
            agent_space = self.ctx.user.agent_space_name() if self.ctx and self.ctx.user else "default"
            dir_path = schema.directory.replace("{user_space}", user_space).replace("{agent_space}", agent_space)

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
        if ls_tool and self.viking_fs and ls_dirs:
            for dir_uri in ls_dirs:
                try:
                    result_str = await ls_tool.execute(self.viking_fs, self.ctx, uri=dir_uri)
                    add_tool_call_pair_to_messages(
                        messages=messages,
                        call_id=call_id_seq,
                        tool_name='ls',
                        params={
                            "uri": dir_uri
                        },
                        result=result_str
                    )
                    call_id_seq += 1

                    result_str = await read_tool.execute(self.viking_fs, self.ctx, uri=f'{dir_uri}/.overview.md')

                    add_tool_call_pair_to_messages(
                        messages=messages,
                        call_id=call_id_seq,
                        tool_name='read',
                        params={
                            "uri": f'{dir_uri}/.overview.md'
                        },
                        result=result_str
                    )
                    call_id_seq += 1

                except Exception as e:
                    logger.warning(f"Failed to ls {dir_uri}: {e}")

        # Step 3: Search for relevant memories based on user messages in conversation
        search_tool = get_tool("search")
        if search_tool and self.viking_fs and self.ctx:
            try:
                # Extract only user messages from conversation
                user_messages = []
                for line in conversation.split("\n"):
                    if line.startswith("[user]:"):
                        user_messages.append(line[len("[user]:"):].strip())
                user_query = " ".join(user_messages)

                if user_query:
                    search_result = await search_tool.execute(
                        viking_fs=self.viking_fs,
                        ctx=self.ctx,
                        query=user_query,
                    )
                    if search_result and not search_result.get("error"):
                        add_tool_call_pair_to_messages(
                            messages=messages,
                            call_id=call_id_seq,
                            tool_name='search',
                            params={"query": user_query},
                            result=str(search_result)
                        )
                        call_id_seq += 1
            except Exception as e:
                logger.warning(f"Pre-fetch search failed: {e}")

        return messages


    async def run(
        self,
        conversation: str,
    ) -> Tuple[Optional[MemoryOperations], List[Dict[str, Any]]]:
        """
        Run the simplified ReAct loop for memory updates.

        Args:
            conversation: Conversation history

        Returns:
            Tuple of (final MemoryOperations, tools_used list)
        """
        iteration = 0
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
        tool_call_messages = await self._pre_fetch_context(conversation)

        # Reset read files tracking for this run
        self._read_files.clear()

        messages = self._build_initial_messages(conversation, tool_call_messages, self._output_language)

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug(f"ReAct iteration {iteration}/{self.max_iterations}")

            # Check if this is the last iteration - force final result
            is_last_iteration = iteration >= self.max_iterations

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
                    # Clear operations to force another iteration
                    operations = None
                    # Continue to next iteration
                    continue

                final_operations = operations
                break

            # If no tool calls either, continue to next iteration (don't break!)
            if not tool_calls:
                logger.warning(f"LLM returned neither tool calls nor operations (iteration {iteration}/{self.max_iterations})")
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
            if iteration >= self.max_iterations:
                raise RuntimeError(f"Reached {self.max_iterations} iterations without completion")
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
        """Build initial messages from conversation and pre-fetched context."""
        system_prompt = self._get_system_prompt(output_language)
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        # Add pre-fetched context as tool calls
        messages.extend(tool_call_messages)
        messages.append({
                "role": "user",
                "content": f"""## Conversation History
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
        allowed_dirs_list = self._get_allowed_directories_list()

        return f"""You are a memory extraction agent. Your task is to analyze conversations and update memories.

## Workflow
1. Analyze the conversation and pre-fetched context
2. If you need more information, use the available tools (read/search)
3. When you have enough information, output ONLY a JSON object (no extra text before or after)

## CRITICAL: Available Tools
- ONLY read and search tools are available
- DO NOT use write tool - just output the JSON result, the system will handle writing
- ls tool is NOT available

## Critical: Read Before Edit
IMPORTANT: Before you edit or update ANY existing memory file, you MUST first use the read tool to read its complete content.

- The pre-fetched .overview.md files are only partial information - they are NOT the complete memory content
- You MUST use the read tool to get the actual content of any file you want to edit
- Without reading the actual file first, your edit operations will fail because the search string won't match

## Target Output Language
All memory content (abstract, overview, content fields) MUST be written in {output_language}.

## URI Handling (Automatic)
IMPORTANT: You do NOT need to construct URIs manually. The system will automatically generate URIs based on:
- For write_uris: Using memory_type and fields
- For edit_uris: Using memory_type and fields to identify the target
- For edit_overview_uris: Using memory_type to identify the directory, then updates the .overview.md file in that directory
- For delete_uris: Using memory_type and fields to identify the target

Just provide the correct memory_type and fields, and the system will handle the rest.

## Edit Overview Files (IMPORTANT - Don't Forget!)
You MUST use edit_overview_uris to update the .overview.md file whenever you write new memories.

This is a REQUIRED step after writing memories:
1. After adding new entries via write_uris, ALWAYS also update the corresponding .overview.md
2. The .overview.md provides a high-level summary for that memory type directory
3. Without updating overview, new memories won't be visible in high-level summaries

Example workflow:
- write_uris: Add new skill "Python async programming" → writes to skills/python_async.md
- edit_overview_uris: {{"memory_type": "skills", "overview": "Python async programming, Go concurrency, System design..."}}

How to use edit_overview_uris:
- Provide memory_type to identify which directory's overview to update
- Provide overview field with the new content (string or patch format)
- Example: {{"memory_type": "profile", "overview": "User profile overview..."}}

## Overview Format Requirements (IMPORTANT)
When generating overview content for edit_overview_uris, you MUST follow this structure:

1. **Title (H1)**: Directory name (e.g., "# skills")
2. **Brief Description (plain text paragraph, 50-150 words)**:
   - Immediately following the title, without any H2 heading
   - Explain what this directory is about
   - Include core keywords for easy searching
3. **Quick Navigation (H2)**: Decision Tree style
   - Use "What do you want to learn?" or "What do you want to do?"
   - Use markdown links with relative paths: [description](./filename.md)
4. **Detailed Description (H2)**: One H3 subsection for each file

Example:
# skills

Python async programming, Go concurrency, and System design skills for backend developers.

## Quick Navigation
- Want to learn async programming → [Python Async](./python_async.md)
- Want to learn concurrency → [Go Concurrency](./go_concurrency.md)

## Detailed Description
### Python Async
...

Total length: 400-800 words

## Final Output Format
Outputs will be a complete JSON object with the following fields (Don't have '```json' appear and do not use '//' to omit content)

JSON schema:
```json
{schema_str}
```

## Important Notes
- DO NOT use write tool - the system will write memories based on your JSON output
- Only read and search tools are available for you to use
- Output ONLY the JSON object - no extra text before or after
- Put your thinking and reasoning in the `reasonning` field of the JSON
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
                return (None, operations)
            except Exception as e:
                logger.warning(f"Unexpected error parsing memory operations: {e}")

        # Case 3: No tool calls and no parsable operations
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

        try:
            result = await tool.execute(self.viking_fs, self.ctx, **tool_call.arguments)
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
