"""Tool registry for dynamic tool management."""

import time
from typing import Any

from loguru import logger

from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.config.schema import SessionKey
from vikingbot.hooks import HookContext
from vikingbot.hooks.manager import hook_manager
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.utils.tracing import get_current_response_id


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self.langfuse = LangfuseClient.get_instance()

    def register(self, tool: Tool) -> None:
        """
        Register a tool in the registry.

        Adds the tool to the internal registry dictionary, using the tool's name
        as the key. If a tool with the same name already exists, it will be
        silently overwritten.

        Args:
            tool: The Tool instance to register. Must have a unique name property.

        Note:
            Currently, duplicate registration silently overwrites the existing tool.
            Consider checking for duplicates if this behavior is not desired.

        Example:
            >>> registry = ToolRegistry()
            >>> tool = MyTool()
            >>> registry.register(tool)
            >>> assert registry.has(tool.name)
        """
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """
        Unregister a tool by name.

        Removes the tool with the specified name from the registry. If no tool
        with that name exists, this operation is a no-op (no error is raised).

        Args:
            name: The name of the tool to unregister.

        Example:
            >>> registry.register(my_tool)
            >>> registry.unregister(my_tool.name)
            >>> assert not registry.has(my_tool.name)
        """
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """
        Get a tool by name.

        Retrieves the tool with the specified name from the registry.

        Args:
            name: The name of the tool to retrieve.

        Returns:
            The Tool instance if found, or None if no tool with that name exists.

        Example:
            >>> tool = registry.get("read_file")
            >>> if tool:
            ...     print(f"Found tool: {tool.description}")
        """
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """
        Check if a tool is registered.

        Args:
            name: The name of the tool to check.

        Returns:
            True if a tool with the given name is registered, False otherwise.

        Example:
            >>> if registry.has("read_file"):
            ...     print("Read file tool is available")
        """
        return name in self._tools

    def get_definitions(self, ov_tools_enable: bool = True) -> list[dict[str, Any]]:
        """
        Get all tool definitions in OpenAI format.

        Converts all registered tools to the OpenAI function schema format,
        suitable for use with OpenAI's function calling API.

        Args:
            ov_tools_enable: Whether to include OpenViking tools. If False,
                tools with names starting with "openviking_" will be excluded.

        Returns:
            List of tool schemas in OpenAI format, where each schema contains
            the tool's type, name, description, and parameters.

        Example:
            >>> definitions = registry.get_definitions()
            >>> for defn in definitions:
            ...     print(f"Tool: {defn['function']['name']}")
        """
        tools = self._tools.values()
        if not ov_tools_enable:
            tools = [tool for tool in tools if not tool.name.startswith("openviking_")]
        return [tool.to_schema() for tool in tools]

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        session_key: SessionKey,
        sandbox_manager: SandboxManager | None = None,
        sender_id: str | None = None,
        memory_user_ids: list[str] | None = None,
    ) -> str:
        """
        Execute a tool by name with given parameters.

        Args:
            name: Tool name.
            params: Tool parameters.
            session_key: Session key for the current session.
            sandbox_manager: Sandbox manager for file/shell operations.
            sender_id: Sender id for the current session.
            memory_user_ids: List of user IDs for memory retrieval.

        Returns:
            Tool execution result as string.

        Raises:
            KeyError: If tool not found.
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        tool_context = ToolContext(
            session_key=session_key,
            sandbox_manager=sandbox_manager,
            sender_id=sender_id,
            memory_user_ids=memory_user_ids,
        )

        # Langfuse tool call tracing - automatic for all tools
        tool_span = None
        start_time = time.time()
        result = None
        response_id = get_current_response_id()
        try:
            if self.langfuse.enabled:
                tool_ctx = self.langfuse.tool_call(
                    name=name,
                    input=params,
                    session_id=session_key.safe_name(),
                    metadata={"response_id": response_id} if response_id else None,
                )
                tool_span = tool_ctx.__enter__()

            errors = tool.validate_params(params)
            if errors:
                result = f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            else:
                result = await tool.execute(tool_context, **params)
        except Exception as e:
            result = e
            logger.exception("Tool call fail: ", e)
        finally:
            # End Langfuse tool call tracing
            duration_ms = (time.time() - start_time) * 1000
            if tool_span is not None:
                try:
                    execute_success = not isinstance(result, Exception) and not (
                        isinstance(result, str) and result.startswith("Error")
                    )
                    output_str = str(result) if result is not None else None
                    self.langfuse.end_tool_call(
                        span=tool_span,
                        output=output_str,
                        success=execute_success,
                        metadata={
                            "duration_ms": duration_ms,
                            **({"response_id": response_id} if response_id else {}),
                        },
                    )
                    if hasattr(tool_span, "__exit__"):
                        tool_span.__exit__(None, None, None)
                    self.langfuse.flush()
                except Exception:
                    pass

        hook_result = await hook_manager.execute_hooks(
            context=HookContext(
                event_type="tool.post_call",
                session_key=session_key,
                workspace_id=sandbox_manager.to_workspace_id(session_key),
            ),
            tool_name=name,
            params=params,
            result=result,
        )
        result = hook_result.get("result")
        if isinstance(result, Exception):
            return f"Error executing {name}: {str(result)}"
        else:
            return result

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
