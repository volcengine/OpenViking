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

from openviking.server.identity import RequestContext
from openviking.session.memory.memory_utils import (
    collect_allowed_directories,
    detect_language_from_conversation,
    pretty_print_messages,
    validate_operations_uris,
)
from openviking.session.memory.memory_operations import MemoryOperations
from openviking.session.memory.memory_types import MemoryTypeRegistry
from openviking.session.memory.schema_models import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.tools import (
    get_tool,
    get_tool_schemas,
)
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


class ActionType(str, Enum):
    """Action type enumeration."""

    READ = "read"
    FIND = "find"
    LS = "ls"
    TREE = "tree"


class ReadAction(BaseModel):
    """Read action to execute."""

    action_type: ActionType = Field(..., description="Action type: read/find/ls/tree")
    params: Dict[str, Any] = Field(default_factory=dict, description="Call parameters")


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
        llm_provider: Any,
        viking_fs: Optional[VikingFS] = None,
        model: Optional[str] = None,
        max_iterations: int = 5,
        ctx: Optional[RequestContext] = None,
    ):
        """
        Initialize the MemoryReAct.

        Args:
            llm_provider: LLM provider instance (from bot/vikingbot/providers/base.py)
            viking_fs: VikingFS instance for storage operations
            model: Model name to use
            max_iterations: Maximum number of ReAct iterations (default: 5)
            ctx: Request context
        """
        self.llm_provider = llm_provider
        self.viking_fs = viking_fs or get_viking_fs()
        self.model = model or llm_provider.get_default_model()
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
                    self._add_tool_calls_to_messages(
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

                    self._add_tool_calls_to_messages(
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

            # Call LLM with tools - model decides: tool calls OR final operations
            tool_calls, operations = await self._call_llm(messages)

            # If model returned final operations, we're done
            if operations is not None:
                final_operations = operations
                break

            # If no tool calls either, something is wrong
            if not tool_calls:
                logger.warning("LLM returned neither tool calls nor operations")
                final_operations = MemoryOperations()
                break

            # Execute all tool calls in parallel
            async def execute_single_action(idx: int, action: ReadAction):
                """Execute a single read action."""
                result = await self._execute_read_action(action)
                return idx, action, result

            action_tasks = [
                execute_single_action(idx, action)
                for idx, action in enumerate(tool_calls)
            ]
            results = await self._execute_in_parallel(action_tasks)

            # Process results and add to messages
            for _idx, action, result in results:
                tools_used.append({
                    "tool_name": action.action_type.value,
                    "params": action.params,
                    "result": result,
                })
                messages = self._add_tool_result_to_messages(
                    messages,
                    action,
                    result,
                )

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

    def _format_pre_fetched_as_tool_calls(self, pre_fetched_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Format pre-fetched context as previous tool call messages."""
        from typing import Tuple

        messages: List[Dict[str, Any]] = []

        # Collect all tool calls and results
        tool_call_items: List[Tuple[str, str, Dict[str, Any], Any]] = []

        # Add ls calls for directories
        if "directories" in pre_fetched_context:
            for idx, (uri, entries) in enumerate(pre_fetched_context["directories"].items()):
                call_id = f"prefetch_ls_{idx}"
                params = {"uri": uri, "output": "agent"}
                result = {
                    "uri": uri,
                    "files": [e for e in entries if not e.get("isDir", False)],
                    "directories": [e for e in entries if e.get("isDir", False)],
                    "entries": entries,
                    "_note": "This ls result only shows file names. Use read tool to get actual file content before editing any file.",
                }
                tool_call_items.append((call_id, "ls", params, result))

        # Add read calls for summaries
        if "summaries" in pre_fetched_context:
            for idx, (uri, content) in enumerate(pre_fetched_context["summaries"].items()):
                call_id = f"prefetch_read_{idx}"
                params = {"uri": uri}
                result = {
                    "uri": uri,
                    "content": str(content)[:2000],
                }
                tool_call_items.append((call_id, "read", params, result))

        # Add find call for search results
        if "search_results" in pre_fetched_context:
            call_id = "prefetch_find_0"
            params = {"query": "conversation context", "limit": 10}
            search_results = pre_fetched_context["search_results"]
            result = {
                "memories": search_results if isinstance(search_results, list) else [],
                "resources": [],
                "skills": [],
            }
            tool_call_items.append((call_id, "find", params, result))

        if tool_call_items:
            # Use shared method to add tool calls and results
            messages = self._add_tool_calls_to_messages([], tool_call_items)

        return messages

    def _add_tool_calls_to_messages(
        self,
        messages: List[Dict[str, Any]],
        call_id,
        tool_name,
        params,
        result
    ) :
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(params),
                },
            }],
        })

        # Add tool result message immediately after
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(result, ensure_ascii=False),
        })

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
        schema_str = json.dumps(self._json_schema, ensure_ascii=False, indent=2)
        allowed_dirs_list = self._get_allowed_directories_list()

        return f"""You are a memory extraction agent. Your task is to analyze conversations and update memories.

## Workflow
1. Analyze the conversation and pre-fetched context
2. If you need more information, use the available tools (read/find/ls/tree)
3. When you have enough information, output the final memory operations directly

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
When you have enough information and are ready to update memories, respond with a JSON object in this format:

```json
{schema_str}
```

## Important Notes
- Always read a file before editing it - ls and summaries are not enough
- When you have enough information, output ONLY the final operations JSON
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
    ) -> Tuple[Optional[List[ReadAction]], Optional[MemoryOperations]]:
        """
        Call LLM with tools. Returns either tool calls OR final operations.

        Returns:
            Tuple of (tool_calls, operations) - one will be None, the other set
        """
        # Call LLM with tools
        response = await self.llm_provider.chat(
            messages=messages,
            tools=get_tool_schemas(),
            model=self.model,
            temperature=0.0,
        )

        # Case 1: LLM returned tool calls
        if response.has_tool_calls:
            actions = []
            for tool_call in response.tool_calls:
                try:
                    action_type = ActionType(tool_call.name.lower())
                    actions.append(ReadAction(
                        action_type=action_type,
                        params=tool_call.arguments,
                    ))
                except ValueError:
                    logger.warning(f"Unknown tool call: {tool_call.name}")
            return (actions, None)

        # Case 2: Try to parse MemoryOperations from content
        content = response.content or ""
        if content:
            try:
                # Remove markdown fences if present
                if content.startswith("```"):
                    content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                data = json.loads(content)
                operations = MemoryOperations(**data)
                # Validate that all URIs are allowed
                self._validate_operations(operations)
                return (None, operations)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse memory operations: {e}")

        # Case 3: No tool calls and no parsable operations
        return (None, None)

    async def _execute_read_action(
        self,
        action: ReadAction,
    ) -> Any:
        """Execute a single read action (read/find/ls/tree)."""
        if not self.viking_fs:
            return {"error": "VikingFS not available"}

        tool = get_tool(action.action_type.value)
        if not tool:
            return {"error": f"Unknown action type: {action.action_type}"}

        try:
            result_str = await tool.execute(self.viking_fs, self.ctx, **action.params)
            return json.loads(result_str)
        except Exception as e:
            logger.error(f"Failed to execute {action.action_type}: {e}")
            return {"error": str(e)}

    async def _execute_in_parallel(
        self,
        tasks: List[Any],
    ) -> List[Any]:
        """Execute tasks in parallel, similar to AgentLoop."""
        return await asyncio.gather(*tasks)

    def _add_tool_result_to_messages(
        self,
        messages: List[Dict[str, Any]],
        action: ReadAction,
        result: Any,
    ) -> List[Dict[str, Any]]:
        """Add tool result to messages."""
        call_id = f"call_{action.action_type.value}"
        tool_call_items = [(call_id, action.action_type.value, action.params, result)]
        return self._add_tool_calls_to_messages(messages, tool_call_items)
