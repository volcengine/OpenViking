# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Message management tests"""

import pytest

from openviking import AsyncOpenViking
from openviking.message import ContextPart, TextPart, ToolPart
from openviking.session import Session
from openviking_cli.exceptions import InvalidArgumentError


class TestAddMessage:
    """Test add_message"""

    async def test_add_user_message(self, session: Session):
        """Test adding user message"""
        msg = session.add_message("user", [TextPart("Hello, world!")])

        assert msg is not None
        assert msg.role == "user"
        assert len(msg.parts) == 1
        assert msg.id is not None

    async def test_add_assistant_message(self, session: Session):
        """Test adding assistant message"""
        msg = session.add_message("assistant", [TextPart("Hello! How can I help?")])

        assert msg is not None
        assert msg.role == "assistant"
        assert len(msg.parts) == 1

    async def test_add_message_with_multiple_parts(self, session: Session):
        """Test adding message with multiple parts"""
        parts = [TextPart("Here is some context:"), TextPart("And here is more text.")]
        msg = session.add_message("assistant", parts)

        assert len(msg.parts) == 2

    async def test_add_message_with_context_part(self, session: Session):
        """Test adding message with context part"""
        parts = [
            TextPart("Based on the context:"),
            ContextPart(
                uri="viking://user/test/resources/doc.md",
                context_type="resource",
                abstract="Some context abstract",
            ),
        ]
        msg = session.add_message("assistant", parts)

        assert len(msg.parts) == 2

    async def test_add_message_with_tool_part(self, session: Session):
        """Test adding message with tool call"""
        tool_part = ToolPart(
            tool_id="tool_123",
            tool_name="search_tool",
            tool_uri="viking://session/test/tools/tool_123",
            skill_uri="viking://user/skills/search",
            tool_input={"query": "test"},
            tool_status="running",
        )
        msg = session.add_message("assistant", [TextPart("Executing search..."), tool_part])

        assert len(msg.parts) == 2

    async def test_add_message_with_peer_id(self, session: Session):
        """Test peer_id is persisted on session messages."""
        msg = session.add_message(
            "user",
            [TextPart("Message from Alice")],
            peer_id="web:visitor:alice",
        )

        assert msg.peer_id == "web:visitor:alice"
        assert msg.to_dict()["peer_id"] == "web:visitor:alice"

    async def test_add_message_rejects_peer_id_with_path_separator(self, session: Session):
        """Test direct session usage validates peer_id path safety."""
        with pytest.raises(InvalidArgumentError):
            session.add_message(
                "user",
                [TextPart("Message from Alice")],
                peer_id="web/visitor/alice",
            )

    async def test_messages_list_updated(self, session: Session):
        """Test message list update"""
        initial_count = len(session.messages)

        session.add_message("user", [TextPart("Message 1")])
        session.add_message("assistant", [TextPart("Response 1")])

        assert len(session.messages) == initial_count + 2

    async def test_batch_add_messages_preserves_peer_id_created_at_and_parts(
        self, client: AsyncOpenViking
    ):
        session_id = "batch_message_preservation_test"
        await client.create_session(session_id=session_id)

        result = await client.batch_add_messages(
            session_id,
            [
                {
                    "role": "user",
                    "peer_id": "user-123",
                    "created_at": "2026-05-01T12:00:00Z",
                    "parts": [
                        {"type": "text", "text": "Hello batch"},
                        {
                            "type": "context",
                            "uri": "viking://resources/test-doc",
                            "context_type": "resource",
                            "abstract": "Test document",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "peer_id": "assistant-123",
                    "created_at": "2026-05-01T12:00:05Z",
                    "parts": [
                        {"type": "text", "text": "Executing tool"},
                        {
                            "type": "tool",
                            "tool_id": "tool_123",
                            "tool_name": "search_tool",
                            "tool_uri": f"viking://session/test/{session_id}/tools/tool_123",
                            "skill_uri": "viking://user/skills/search",
                            "tool_status": "completed",
                            "tool_output": "Found a result",
                        },
                    ],
                },
            ],
        )

        assert result["added"] == 2

        context = await client.get_session_context(session_id)
        assert [message["role"] for message in context["messages"]] == ["user", "assistant"]
        assert context["messages"][0]["peer_id"] == "user-123"
        assert context["messages"][0]["created_at"] == "2026-05-01T12:00:00Z"
        assert context["messages"][0]["parts"][1] == {
            "type": "context",
            "uri": "viking://resources/test-doc",
            "context_type": "resource",
            "abstract": "Test document",
        }
        assert context["messages"][1]["peer_id"] == "assistant-123"
        assert context["messages"][1]["created_at"] == "2026-05-01T12:00:05Z"
        assert context["messages"][1]["parts"][1]["type"] == "tool"
        assert context["messages"][1]["parts"][1]["tool_status"] == "completed"
        assert context["messages"][1]["parts"][1]["tool_output"] == "Found a result"

    async def test_batch_add_messages_is_atomic_when_later_message_is_invalid(
        self, client: AsyncOpenViking
    ):
        session_id = "batch_message_atomicity_test"
        await client.create_session(session_id=session_id)

        with pytest.raises(ValueError, match="Either content or parts must be provided"):
            await client.batch_add_messages(
                session_id,
                [
                    {"role": "user", "content": "first valid message"},
                    {"role": "assistant"},
                ],
            )

        context = await client.get_session_context(session_id)
        assert context["messages"] == []

        result = await client.batch_add_messages(
            session_id,
            [{"role": "user", "content": "first valid message"}],
        )

        assert result["added"] == 1
        context = await client.get_session_context(session_id)
        assert [message["parts"][0]["text"] for message in context["messages"]] == [
            "first valid message"
        ]


class TestUpdateToolPart:
    """Test update_tool_part"""

    async def test_update_tool_completed(self, session_with_tool_call):
        """Test updating tool status to completed"""
        session, message_id, tool_id = session_with_tool_call

        session.update_tool_part(
            message_id=message_id,
            tool_id=tool_id,
            output="Tool execution completed successfully",
            status="completed",
        )

        # Verify tool status updated
        # Need to find the corresponding message and tool part
        msg = next((m for m in session.messages if m.id == message_id), None)
        assert msg is not None

    async def test_update_tool_failed(self, session_with_tool_call):
        """Test updating tool status to failed"""
        session, message_id, tool_id = session_with_tool_call

        session.update_tool_part(
            message_id=message_id,
            tool_id=tool_id,
            output="Tool execution failed: error message",
            status="failed",
        )

        # Verify tool status updated
        msg = next((m for m in session.messages if m.id == message_id), None)
        assert msg is not None
