"""Message tool for sending messages to users."""

from typing import Any, Callable, Awaitable

from vikingbot.agent.tools.base import Tool
from vikingbot.bus.events import OutboundMessage
from vikingbot.config.schema import SessionKey


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ):
        self._send_callback = send_callback

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The message content to send"}
            },
            "required": ["content"],
        }

    async def execute(self, tool_context: "ToolContext", **kwargs: Any) -> str:
        from loguru import logger

        content = kwargs.get("content")

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = OutboundMessage(session_key=tool_context.session_key, content=content)

        try:
            await self._send_callback(msg)
            return f"Message sent to {tool_context.session_key} "
        except Exception as e:
            return f"Error sending message: {str(e)}"
