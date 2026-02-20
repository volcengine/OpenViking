"""Message tool for sending messages to users."""

from typing import Any, Callable, Awaitable

from vikingbot.agent.tools.base import Tool
from vikingbot.bus.events import OutboundMessage


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""
    
    def __init__(
        self, 
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = ""
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
    
    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the current message context."""
        self._default_channel = channel
        self._default_chat_id = chat_id
    
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
                "content": {
                    "type": "string",
                    "description": "The message content to send"
                },
                "channel": {
                    "type": "string",
                    "description": "Optional: target channel (only use if you need to send to a DIFFERENT channel from the current conversation). Format example: feishu:cli_a1b2c3d4e5f"
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional: target chat/user ID (only use if you need to send to a DIFFERENT chat from the current conversation)"
                }
            },
            "required": ["content"]
        }
    
    async def execute(self, **kwargs: Any) -> str:
        from loguru import logger
        
        content = kwargs.get("content")
        channel = kwargs.get("channel")
        chat_id = kwargs.get("chat_id")
        
        target_channel = self._default_channel
        target_chat_id = self._default_chat_id
        
        if channel and channel != target_channel:
            if ":" not in channel and target_channel.startswith(f"{channel}:"):
                logger.debug(f"Keeping default channel {target_channel} instead of shorthand {channel}")
            else:
                target_channel = channel
        
        if chat_id and chat_id != target_chat_id:
            target_chat_id = chat_id
        
        if not target_channel or not target_chat_id:
            return "Error: No target channel/chat specified"
        
        if not self._send_callback:
            return "Error: Message sending not configured"
        
        msg = OutboundMessage(
            channel=target_channel,
            chat_id=target_chat_id,
            content=content
        )
        
        try:
            await self._send_callback(msg)
            return f"Message sent to {target_channel}:{target_chat_id}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
