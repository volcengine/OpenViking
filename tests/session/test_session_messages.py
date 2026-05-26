# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Message management tests"""

import pytest

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
