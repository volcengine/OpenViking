"""Pydantic models for OpenAPI channel."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """Message role enumeration."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class EventType(str, Enum):
    """Event type enumeration."""

    RESPONSE = "response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    REASONING = "reasoning"
    ITERATION = "iteration"


class FeedbackType(str, Enum):
    """Supported explicit feedback types."""

    THUMB_UP = "thumb_up"
    THUMB_DOWN = "thumb_down"
    RATING = "rating"


class ChatMessage(BaseModel):
    """A single chat message."""

    role: MessageRole = Field(..., description="Role of the message sender")
    content: str = Field(..., description="Message content")
    timestamp: Optional[datetime] = Field(
        default_factory=datetime.now, description="Message timestamp"
    )


class ChatRequest(BaseModel):
    """Request body for chat endpoint."""

    message: str = Field(..., description="User message to send", min_length=1)
    session_id: Optional[str] = Field(
        default="default", description="Session ID (optional, will create new if not provided)"
    )
    user_id: Optional[str] = Field(default=None, description="User identifier (optional)")
    stream: bool = Field(default=False, description="Whether to stream the response")
    context: Optional[List[ChatMessage]] = Field(
        default=None, description="Additional context messages"
    )
    need_reply: bool = True
    channel_id: Optional[str] = Field(
        default=None, description="Channel ID for multi-channel routing (optional)"
    )
    disabled_tools: List[str] = Field(
        default_factory=list,
        description="Tool names to hide for this request",
    )


class ChatResponse(BaseModel):
    """Response from chat endpoint (non-streaming)."""

    session_id: str = Field(..., description="Session ID")
    response_id: Optional[str] = Field(default=None, description="Assistant response ID")
    message: str = Field(..., description="Assistant's response message")
    events: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Intermediate events (thinking, tool calls)"
    )
    relevant_memories: Optional[str] = Field(
        default=None,
        description="OpenViking memories assembled during _process_message",
    )
    token_usage: Dict[str, int] = Field(
        default_factory=dict,
        description="Token usage statistics (prompt_tokens, completion_tokens, total_tokens)",
    )
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")


class FeedbackRequest(BaseModel):
    """Request body for explicit feedback submission."""

    response_id: str = Field(..., description="Assistant response ID", min_length=1)
    session_id: str = Field(..., description="Session ID", min_length=1)
    user_id: Optional[str] = Field(default=None, description="User identifier (optional)")
    feedback_type: FeedbackType = Field(..., description="Feedback type")
    feedback_score: Optional[float] = Field(default=None, description="Numeric feedback score")
    feedback_reason: Optional[str] = Field(default=None, description="Feedback reason label")
    feedback_text: Optional[str] = Field(default=None, description="Free-form feedback text")
    channel_id: Optional[str] = Field(
        default=None,
        description="Bot channel ID for multi-channel routing (optional)",
    )


class FeedbackResponse(BaseModel):
    """Response from feedback endpoint."""

    accepted: bool = Field(default=True, description="Whether feedback was accepted")
    response_id: str = Field(..., description="Assistant response ID")
    session_id: str = Field(..., description="Session ID")
    feedback_type: FeedbackType = Field(..., description="Feedback type")
    feedback_delay_sec: Optional[float] = Field(
        default=None,
        description="Delay between response creation and feedback submission",
    )
    timestamp: datetime = Field(default_factory=datetime.now, description="Feedback timestamp")


class ChatStreamEvent(BaseModel):
    """A single event in the chat stream (SSE)."""

    event: EventType = Field(..., description="Event type")
    data: Any = Field(..., description="Event data")
    timestamp: datetime = Field(default_factory=datetime.now, description="Event timestamp")


class SessionInfo(BaseModel):
    """Session information."""

    id: str = Field(..., description="Session ID")
    created_at: datetime = Field(..., description="Session creation time")
    last_active: datetime = Field(..., description="Last activity time")
    message_count: int = Field(default=0, description="Number of messages in session")


class SessionCreateRequest(BaseModel):
    """Request to create a new session."""

    user_id: Optional[str] = Field(default=None, description="User identifier")
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional session metadata"
    )


class SessionCreateResponse(BaseModel):
    """Response from session creation."""

    session_id: str = Field(..., description="Created session ID")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")


class SessionListResponse(BaseModel):
    """Response listing all sessions."""

    sessions: List[SessionInfo] = Field(default_factory=list, description="List of sessions")
    total: int = Field(..., description="Total number of sessions")


class SessionDetailResponse(BaseModel):
    """Detailed session information including messages."""

    session: SessionInfo = Field(..., description="Session information")
    messages: List[ChatMessage] = Field(default_factory=list, description="Session messages")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(default="healthy", description="Service status")
    version: Optional[str] = Field(default=None, description="API version")
    timestamp: datetime = Field(default_factory=datetime.now, description="Check timestamp")


class ErrorResponse(BaseModel):
    """Error response."""

    error: str = Field(..., description="Error message")
    code: Optional[str] = Field(default=None, description="Error code")
    detail: Optional[str] = Field(default=None, description="Detailed error information")