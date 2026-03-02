"""Tool registry for dynamic tool management."""

import time

from loguru import logger

from typing import Any

from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.config.schema import SessionKey
from vikingbot.hooks import HookContext
from vikingbot.hooks.manager import hook_manager
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.sandbox.manager import SandboxManager


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self.langfuse = LangfuseClient.get_instance()

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        session_key: SessionKey,
        sandbox_manager: SandboxManager | None = None,
        sender_id: str | None = None,
    ) -> str:
        """
        Execute a tool by name with given parameters.

        Args:
            name: Tool name.
            params: Tool parameters.
            session_key: Session key for the current session.
            sandbox_manager: Sandbox manager for file/shell operations.
            sender_id: Sender id for the current session.

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
            sandbox_key=sandbox_manager.to_sandbox_key(session_key),
            sender_id=sender_id,
        )

        # Langfuse tool call tracing - automatic for all tools
        tool_span = None
        start_time = time.time()
        result = None
        try:
            if self.langfuse.enabled:
                tool_ctx = self.langfuse.tool_call(
                    name=name,
                    input=params,
                    session_id=session_key.safe_name(),
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
                        metadata={"duration_ms": duration_ms},
                    )
                    if hasattr(tool_span, "__exit__"):
                        tool_span.__exit__(None, None, None)
                    self.langfuse.flush()
                except Exception:
                    pass

        hook_result = await hook_manager.execute_hooks(
            context=HookContext(
                event_type="tool.post_call",
                session_id=session_key.safe_name(),
                sandbox_key=sandbox_manager.to_sandbox_key(session_key),
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
