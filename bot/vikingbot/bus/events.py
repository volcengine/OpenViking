"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from vikingbot.config.schema import SessionKey


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    # channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    # chat_id: str  # Chat/channel identifier
    content: str  # Message text
    session_key: SessionKey
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data

    # @property
    # def session_key(self) -> str:
    #     """Unique key for session identification."""
    #     return f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel."""

    session_key: SessionKey
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
