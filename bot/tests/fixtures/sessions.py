"""Session fixtures for testing."""

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime


@dataclass
class SessionFixture:
    """Session fixture data class."""

    session_id: str
    user_id: str
    channel: str
    chat_id: str
    messages: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "messages": self.messages,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def add_message(self, role: str, content: str, **kwargs) -> dict:
        """Add a message to the session."""
        message = {
            "id": f"msg-{len(self.messages)}",
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        }
        self.messages.append(message)
        self.updated_at = datetime.now().isoformat()
        return message


# Predefined session fixtures
EMPTY_SESSION = SessionFixture(
    session_id="sess-empty-001",
    user_id="user-001",
    channel="telegram",
    chat_id="chat-001",
    messages=[],
)

SINGLE_MESSAGE_SESSION = SessionFixture(
    session_id="sess-single-001",
    user_id="user-002",
    channel="telegram",
    chat_id="chat-002",
    messages=[
        {
            "id": "msg-0",
            "role": "user",
            "content": "Hello bot",
            "timestamp": "2024-01-01T10:00:00",
        },
        {
            "id": "msg-1",
            "role": "assistant",
            "content": "Hello! How can I help you today?",
            "timestamp": "2024-01-01T10:00:01",
        },
    ],
)

MULTI_TURN_SESSION = SessionFixture(
    session_id="sess-multi-001",
    user_id="user-003",
    channel="feishu",
    chat_id="chat-003",
    messages=[
        {
            "id": "msg-0",
            "role": "user",
            "content": "What's the weather like?",
            "timestamp": "2024-01-01T10:00:00",
        },
        {
            "id": "msg-1",
            "role": "assistant",
            "content": "I don't have access to real-time weather data.",
            "timestamp": "2024-01-01T10:00:01",
        },
        {
            "id": "msg-2",
            "role": "user",
            "content": "Can you help me write Python code?",
            "timestamp": "2024-01-01T10:00:30",
        },
        {
            "id": "msg-3",
            "role": "assistant",
            "content": "Yes, I can help you with Python! What would you like to build?",
            "timestamp": "2024-01-01T10:00:31",
        },
    ],
    metadata={
        "user_preferences": {"language": "python", "skill_level": "intermediate"},
    },
)

SESSION_WITH_TOOL_CALLS = SessionFixture(
    session_id="sess-tools-001",
    user_id="user-004",
    channel="telegram",
    chat_id="chat-004",
    messages=[
        {
            "id": "msg-0",
            "role": "user",
            "content": "Search for Python tutorials",
            "timestamp": "2024-01-01T10:00:00",
        },
        {
            "id": "msg-1",
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-001",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "Python tutorials"}',
                    },
                }
            ],
            "timestamp": "2024-01-01T10:00:01",
        },
        {
            "id": "msg-2",
            "role": "tool",
            "tool_call_id": "call-001",
            "content": "[Search Results for Python tutorials...]",
            "timestamp": "2024-01-01T10:00:02",
        },
        {
            "id": "msg-3",
            "role": "assistant",
            "content": "I found several Python tutorials for you...",
            "timestamp": "2024-01-01T10:00:03",
        },
    ],
)


def get_all_fixtures() -> list[SessionFixture]:
    """Get all session fixtures."""
    return [
        EMPTY_SESSION,
        SINGLE_MESSAGE_SESSION,
        MULTI_TURN_SESSION,
        SESSION_WITH_TOOL_CALLS,
    ]


def get_fixture_by_id(session_id: str) -> SessionFixture | None:
    """Get a fixture by its session ID."""
    for fixture in get_all_fixtures():
        if fixture.session_id == session_id:
            return fixture
    return None


def create_custom_session(
    session_id: str,
    user_id: str,
    num_messages: int = 0,
) -> SessionFixture:
    """Create a custom session fixture with the specified number of messages."""
    session = SessionFixture(
        session_id=session_id,
        user_id=user_id,
        channel="telegram",
        chat_id=f"chat-{user_id}",
    )

    for i in range(num_messages):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"Message {i} from {role}"
        session.add_message(role=role, content=content)

    return session
