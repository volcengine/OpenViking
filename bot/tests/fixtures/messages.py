"""Message fixtures for testing."""

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime


@dataclass
class MessageFixture:
    """Message fixture data class."""

    id: str
    content: str
    user_id: str
    channel: str
    chat_id: Optional[str] = None
    username: Optional[str] = None
    type: str = "text"
    reply_to: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "content": self.content,
            "user_id": self.user_id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "username": self.username,
            "type": self.type,
            "reply_to": self.reply_to,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


# Predefined message fixtures
TEXT_MESSAGE = MessageFixture(
    id="msg-text-001",
    content="Hello, this is a test message",
    user_id="user-123",
    channel="telegram",
    chat_id="chat-456",
    username="testuser",
    type="text",
)

REPLY_MESSAGE = MessageFixture(
    id="msg-reply-001",
    content="This is a reply",
    user_id="user-123",
    channel="telegram",
    chat_id="chat-456",
    username="testuser",
    type="text",
    reply_to="msg-text-001",
)

IMAGE_MESSAGE = MessageFixture(
    id="msg-image-001",
    content="Photo caption",
    user_id="user-123",
    channel="telegram",
    chat_id="chat-456",
    username="testuser",
    type="image",
    metadata={
        "file_id": "photo-file-id",
        "width": 800,
        "height": 600,
    },
)

FEISHU_TEXT_MESSAGE = MessageFixture(
    id="om-feishu-001",
    content='{"text": "Hello from Feishu"}',
    user_id="user-feishu-001",
    channel="feishu",
    chat_id="chat-feishu-001",
    type="text",
    metadata={
        "message_type": "text",
        "chat_type": "p2p",
    },
)

BOT_RESPONSE_MESSAGE = MessageFixture(
    id="msg-bot-001",
    content="This is a bot response",
    user_id="bot",
    channel="telegram",
    chat_id="chat-456",
    type="text",
    reply_to="msg-text-001",
)


def get_all_fixtures() -> list[MessageFixture]:
    """Get all message fixtures."""
    return [
        TEXT_MESSAGE,
        REPLY_MESSAGE,
        IMAGE_MESSAGE,
        FEISHU_TEXT_MESSAGE,
        BOT_RESPONSE_MESSAGE,
    ]


def get_fixture_by_id(fixture_id: str) -> MessageFixture | None:
    """Get a fixture by its ID."""
    for fixture in get_all_fixtures():
        if fixture.id == fixture_id:
            return fixture
    return None
