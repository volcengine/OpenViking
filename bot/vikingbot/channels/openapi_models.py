"""Pydantic models for OpenAPI channel."""

import base64
import binascii
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

MAX_CHAT_IMAGES = 4
MAX_CHAT_INLINE_IMAGE_BYTES = 10 * 1024 * 1024
SUPPORTED_CHAT_INLINE_IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def _detect_chat_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_chat_image_url(value: str) -> str:
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("image URL must be an absolute HTTPS URL without credentials")
        # Match multimodal model APIs: pass remote URLs through without fetching
        # them. Content-type and format support are therefore provider-specific.
        return value

    if not value.startswith("data:image/"):
        raise ValueError("image URL must use HTTPS or an image Base64 data URL")

    try:
        header, encoded = value.split(",", 1)
        media_type, encoding = header[5:].split(";", 1)
    except ValueError as exc:
        raise ValueError("invalid image data URL") from exc

    media_type = media_type.lower()
    if media_type not in SUPPORTED_CHAT_INLINE_IMAGE_MIME_TYPES:
        raise ValueError("unsupported image MIME type; expected JPEG, PNG, GIF, or WebP")
    if encoding.lower() != "base64" or not encoded:
        raise ValueError("image data URL must contain Base64-encoded data")

    # Reject oversized payloads before decoding to avoid a large temporary allocation.
    if len(encoded) > ((MAX_CHAT_INLINE_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError(f"decoded image exceeds {MAX_CHAT_INLINE_IMAGE_BYTES} bytes")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image data URL contains invalid Base64") from exc
    if len(decoded) > MAX_CHAT_INLINE_IMAGE_BYTES:
        raise ValueError(f"decoded image exceeds {MAX_CHAT_INLINE_IMAGE_BYTES} bytes")

    detected_type = _detect_chat_image_mime(decoded)
    if detected_type is None:
        raise ValueError("image data does not have a supported image signature")
    if detected_type != media_type:
        raise ValueError(
            f"image MIME type {media_type} does not match detected type {detected_type}"
        )
    return value


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
    CONTENT_DELTA = "content_delta"
    REASONING_DELTA = "reasoning_delta"
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


class ChatImageURL(BaseModel):
    """OpenAI-compatible image URL descriptor."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        ...,
        description="HTTPS passthrough URL or validated Base64 image data URL",
    )
    detail: Optional[Literal["auto", "low", "high"]] = Field(
        default=None,
        description="Optional image detail level; omitted for maximum provider compatibility",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_chat_image_url(value)


class ChatImage(BaseModel):
    """OpenAI-compatible image input part."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"] = "image_url"
    image_url: ChatImageURL


class OpenVikingConnection(BaseModel):
    """OpenViking identity forwarded by the Studio proxy."""

    api_key: Optional[str] = Field(default=None, description="API key from the active client")
    account_id: Optional[str] = Field(default=None, description="Effective account ID")
    user_id: Optional[str] = Field(default=None, description="Effective user ID")
    agent_id: Optional[str] = Field(default=None, description="Effective agent ID")
    actor_peer_id: Optional[str] = Field(default=None, description="Effective actor peer ID")
    role: Optional[str] = Field(default=None, description="Effective OpenViking role")
    api_key_type: Optional[str] = Field(default=None, description="OpenViking API key type")
    namespace_policy: Optional[Dict[str, bool]] = Field(
        default=None,
        description="Effective account namespace policy",
    )
    server_url: Optional[str] = Field(default=None, description="OpenViking server URL")


class ChatRequest(BaseModel):
    """Request body for chat endpoint."""

    message: str = Field(default="", description="User message to send")
    images: List[ChatImage] = Field(
        default_factory=list,
        max_length=MAX_CHAT_IMAGES,
        description="HTTPS passthrough URLs or validated Base64 image inputs",
    )
    session_id: Optional[str] = Field(
        default=None, description="Session ID (optional, will create new if not provided)"
    )
    _principal_scope: str = PrivateAttr(default="local")
    user_id: Optional[str] = Field(default=None, description="User identifier (optional)")
    stream: bool = Field(default=False, description="Whether to stream the response")
    context: Optional[List[ChatMessage]] = Field(
        default=None,
        description="Non-empty context messages are not supported and are rejected",
    )
    need_reply: bool = True
    channel_id: Optional[str] = Field(
        default=None, description="Channel ID for multi-channel routing (optional)"
    )
    disabled_tools: List[str] = Field(
        default_factory=list,
        description="Tool names to hide for this request",
    )
    openviking_connection: Optional[OpenVikingConnection] = Field(
        default=None,
        description="Authenticated OpenViking connection forwarded by the server proxy",
    )

    @model_validator(mode="after")
    def validate_request(self) -> "ChatRequest":
        if not self.message.strip() and not self.images:
            raise ValueError("message or at least one image is required")
        if self.context:
            raise ValueError("context is not supported")
        return self


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
    openviking_connection: Optional[OpenVikingConnection] = Field(
        default=None,
        description="Authenticated OpenViking connection forwarded by the server proxy",
    )
    _principal_scope: str = PrivateAttr(default="local")

    @model_validator(mode="after")
    def validate_rating_feedback(self) -> "FeedbackRequest":
        """Require a numeric score when the client submits rating feedback."""
        if self.feedback_type == FeedbackType.RATING and self.feedback_score is None:
            raise ValueError("feedback_score is required when feedback_type is rating")
        return self


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
