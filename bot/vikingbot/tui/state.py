"""TUI state management module."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Callable, Any

from vikingbot.config.schema import SessionKey


class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ThinkingStepType(Enum):
    """思考步骤类型"""

    REASONING = "reasoning"  # 推理内容
    TOOL_CALL = "tool_call"  # 工具调用
    TOOL_RESULT = "tool_result"  # 工具结果
    ITERATION = "iteration"  # 迭代开始


@dataclass
class ThinkingStep:
    """单个思考步骤"""

    step_type: ThinkingStepType
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


@dataclass
class Message:
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tokens_used: Optional[int] = None
    thinking_steps: List[ThinkingStep] = field(default_factory=list)


@dataclass
class TUIState:
    messages: List[Message] = field(default_factory=list)
    session_key: SessionKey = SessionKey(type="tui", channel_id="default", chat_id="default")
    is_thinking: bool = False
    thinking_message: str = "vikingbot is thinking..."
    input_text: str = ""
    input_history: List[str] = field(default_factory=list)
    history_index: int = -1
    last_error: Optional[str] = None
    total_tokens: int = 0
    message_count: int = 0

    # 思考过程相关
    current_thinking_steps: List[ThinkingStep] = field(default_factory=list)
    show_thinking_panel: bool = True  # 是否显示思考面板
    thinking_callback: Optional[Callable[[ThinkingStep], None]] = None
