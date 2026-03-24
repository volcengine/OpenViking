"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from vikingbot.config.schema import SessionKey


class OutboundEventType(str, Enum):
    """Type of outbound message/event."""

    RESPONSE = "response"  # Normal response message
    TOOL_CALL = "tool_call"  # Tool being called
    TOOL_RESULT = "tool_result"  # Result from tool execution
    REASONING = "reasoning"  # Reasoning content
    ITERATION = "iteration"  # Iteration marker


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    sender_id: str  # User identifier
    content: str  # Message text
    session_key: SessionKey
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data


@dataclass
class OutboundMessage:
    """Message to send to a chat channel."""

    session_key: SessionKey
    content: str
    event_type: OutboundEventType = OutboundEventType.RESPONSE
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    token_usage: dict[str, int] = field(default_factory=dict)
    time_cost: float = field(default_factory=float)
    iteration: int = field(default_factory=int)
    tools_used_names: list[str] = field(default_factory=list)

    @property
    def channel(self) -> str:
        """Get channel key from session key."""
        return self.session_key.channel_key()

    @property
    def is_normal_message(self) -> bool:
        """Check if this is a normal response message."""
        return self.event_type == OutboundEventType.RESPONSE
