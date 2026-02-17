"""Base channel interface for chat platforms."""

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from vikingbot.bus.events import InboundMessage, OutboundMessage
from vikingbot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.
    
    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the vikingbot message bus.
    """
    
    name: str = "base"
    
    def __init__(self, config: Any, bus: MessageBus, channel_id: str | None = None):
        """
        Initialize the channel.
        
        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
            channel_id: Unique identifier for this channel (for multi-channel support).
        """
        self.config = config
        self.bus = bus
        self._running = False
        self.channel_id = channel_id or getattr(config, "unique_id", self.name)
        
        # 如果有 channel_id，动态设置 name 为 {type}:{id} 格式
        if self.channel_id and self.channel_id != self.name:
            # 从 config 获取 type，或者从 self.name 获取
            channel_type = getattr(config, "type", self.name)
            # 确保是字符串
            if hasattr(channel_type, "value"):
                channel_type = channel_type.value
            elif not isinstance(channel_type, str):
                channel_type = str(channel_type)
            # 确保不是 "ChannelType.FEISHU" 这种格式
            if "." in channel_type and "ChannelType" in channel_type:
                channel_type = channel_type.split(".")[-1].lower()
            self.name = f"{channel_type}:{self.channel_id}"
    
    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.
        
        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass
    
    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.
        
        Args:
            msg: The message to send.
        """
        pass
    
    def is_allowed(self, sender_id: str) -> bool:
        """
        Check if a sender is allowed to use this bot.
        
        Args:
            sender_id: The sender's identifier.
        
        Returns:
            True if allowed, False otherwise.
        """
        allow_list = getattr(self.config, "allow_from", [])
        
        # If no allow list, allow everyone
        if not allow_list:
            return True
        
        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True
        return False
    
    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """
        Handle an incoming message from the chat platform.
        
        This method checks permissions and forwards to the bus.
        
        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                f"Access denied for sender {sender_id} on channel {self.name}. "
                f"Add them to allowFrom list in config to grant access."
            )
            return
        
        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {}
        )
        
        await self.bus.publish_inbound(msg)
    
    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
