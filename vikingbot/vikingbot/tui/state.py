"""TUI state management module."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tokens_used: Optional[int] = None


@dataclass
class TUIState:
    messages: List[Message] = field(default_factory=list)
    session_id: str = "tui:default"
    is_thinking: bool = False
    thinking_message: str = "vikingbot is thinking..."
    input_text: str = ""
    input_history: List[str] = field(default_factory=list)
    history_index: int = -1
    last_error: Optional[str] = None
    total_tokens: int = 0
    message_count: int = 0
