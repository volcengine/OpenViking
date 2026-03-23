# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Simplified ReAct orchestrator for memory updates - single LLM call with tool use.

Reference: bot/vikingbot/agent/loop.py AgentLoop structure
"""

import asyncio
import json
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from openviking.models.vlm.base import VLMBase, VLMResponse
from openviking.server.identity import RequestContext
from openviking.session.memory.utils import (
    collect_allowed_directories,
    detect_language_from_conversation,
    extract_json_from_markdown,
    parse_json_with_stability,
    pretty_print_messages,
    validate_operations_uris,
)
from openviking.session.memory.dataclass import MemoryOperations
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.schema_models import (
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
    0. Pre-fetch: System performs ls + read .abstract.md/.overview.md + search
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
    ):
        """
        Initialize the MemoryReAct.

        Args:
            vlm: VLM instance (from openviking.models.vlm.base)
            viking_fs: VikingFS instance for storage operations
            model: Model name to use
            max_iterations: Maximum number of ReAct iterations (default: 5)
            ctx: Request context
        """
        self.vlm = vlm
        self.viking_fs = viking_fs or get_viking_fs()
        self.model = model or self.vlm.model
        self.max_iterations = max_iterations
        self.ctx = ctx

        # Initialize schema registry and generators
        import os
        schemas_dir = os.path.join(os.path.dirname(__file__), "..", "..", "prompts", "templates", "memory")
        self.registry = MemoryTypeRegistry()
        self.registry.load_from_directory(schemas_dir)
        self.schema_model_generator = SchemaModelGenerator(self.registry)
        self.schema_prompt_generator = SchemaPromptGenerator(self.registry)

        # Pre-generate models and JSON schema
        self.schema_model_generator.generate_all_models()
        self._json_schema = self.schema_model_generator.get_llm_json_schema()

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

            # Replace variables in directory path
            dir_path = schema.directory.replace("{user_space}", "default").replace("{agent_space}", "default")

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

                    result_str = await read_tool.execute(self.viking_fs, self.ctx, uri=f'{dir_uri}/.abstract.md')

                    add_tool_call_pair_to_messages(
                        messages=messages,
                        call_id=call_id_seq,
                        tool_name='read',
                        params={
                            "uri": f'{dir_uri}/.abstract.md'
                        },
                        result=result_str
                    )
                    call_id_seq += 1

                except Exception as e:
                    logger.warning(f"Failed to ls {dir_uri}: {e}")

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
        output_language = detect_language_from_conversation(
            conversation, fallback_language=fallback_language
        )
        logger.info(f"Detected output language for memory ReAct: {output_language}")

        # Pre-fetch context internally
        tool_call_messages = await self._pre_fetch_context(conversation)

        messages = self._build_initial_messages(conversation, tool_call_messages, output_language)

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug(f"ReAct iteration {iteration}/{self.max_iterations}")

            # Check if this is the last iteration - force final result
            is_last_iteration = iteration >= self.max_iterations

            # Call LLM with tools - model decides: tool calls OR final operations
            tool_calls, operations = await self._call_llm(messages, force_final=is_last_iteration)

            # If model returned final operations, we're done
            if operations is not None:
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
                add_tool_call_pair_to_messages(
                    messages,
                    call_id=tool_call.id,
                    tool_name=tool_call.name,
                    params=tool_call.arguments,
                    result=result,
                )
            # Print updated messages with tool results
            pretty_print_messages(messages)
        logger.info(f'final_operations={final_operations}')
        if final_operations is None:
            if iteration >= self.max_iterations:
                raise RuntimeError(f"Reached {self.max_iterations} iterations without completion")
            else:
                raise RuntimeError("ReAct loop completed but no operations generated")

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
            },
            {
                "role": "user",
                "content": f"""## Conversation History
{conversation}

First, let's explore the memory directory structure and summaries to understand what's already stored.""",
            },
        ]

        # Add pre-fetched context as tool calls
        messages.extend(tool_call_messages)

        # Print messages in a readable format
        pretty_print_messages(messages)

        return messages


    def _get_allowed_directories_list(self) -> str:
        """Get a formatted list of allowed directories for the system prompt."""
        allowed_dirs = collect_allowed_directories(
            self.registry.list_all(include_disabled=False),
            user_space="default",
            agent_space="default",
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
2. If you need more information, use the available tools (read/search/ls/tree)
3. When you have enough information, output ONLY a JSON object (no extra text before or after)

## Critical: Read Before Edit
IMPORTANT: Before you edit or update ANY existing memory file, you MUST first use the read tool to read its complete content.

- The ls tool only shows you what files exist - it does NOT show you the file content
- The pre-fetched summaries (.abstract.md and .overview.md) are only partial information - they are NOT the complete memory content
- You MUST use the read tool to get the actual content of any file you want to edit
- Without reading the actual file first, your edit operations will fail because the search string won't match

## Target Output Language
All memory content (abstract, overview, content fields) MUST be written in {output_language}.

## URI Handling (Automatic)
IMPORTANT: You do NOT need to construct URIs manually. The system will automatically generate URIs based on:
- For write_uris: Using memory_type and fields
- For edit_uris: Using memory_type and fields to identify the target
- For delete_uris: Using memory_type and fields to identify the target

Just provide the correct memory_type and fields, and the system will handle the rest.

## Allowed Directories
IMPORTANT: All memory operations will be validated to be within these directories:

{allowed_dirs_list}

## Final Output Format
Outputs will be a complete JSON object with the following fields (Don't have '```json' appear and do not use '//' to omit content)

JSON schema:
```json
{schema_str}
```

## Important Notes
- Always read a file before editing it - ls and summaries are not enough
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
                    expected_fields=['reasoning', 'write_uris', 'edit_uris', 'delete_uris'],
                )

                if error is not None:
                    logger.warning(f"Failed to parse memory operations (stable parse): {error}")
                    # Fallback: try with base MemoryOperations
                    content_no_md = extract_json_from_markdown(content)
                    operations, error_fallback = parse_json_with_stability(
                        content=content_no_md,
                        model_class=MemoryOperations,
                        expected_fields=['reasoning', 'write_uris', 'edit_uris', 'delete_uris'],
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
