"""Tool registry for dynamic tool management."""

from loguru import logger

from typing import Any, TYPE_CHECKING

from vikingbot.agent.tools.base import Tool, ToolContext
from vikingbot.config import loader
from vikingbot.config.schema import SessionKey
from vikingbot.hooks import HookContext
from vikingbot.hooks.manager import hook_manager
from vikingbot.sandbox.manager import SandboxManager

"""Tool registry for dynamic tool management."""
from loguru import logger

from typing import Any

from vikingbot.agent.tools.base import Tool
from vikingbot.config.schema import SessionKey
from vikingbot.hooks import HookContext
from vikingbot.hooks.manager import hook_manager


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

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
    ) -> str:
        """
        Execute a tool by name with given parameters.

        Args:
            name: Tool name.
            params: Tool parameters.
            session_key: Session key for the current session.
            sandbox_manager: Sandbox manager for file/shell operations.

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
        )

        result = None
        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            result = await tool.execute(tool_context, **params)
        except Exception as e:
            result = e
            logger.exception("Tool call fail: ", e)

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
