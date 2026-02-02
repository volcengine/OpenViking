# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Commit tests"""

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.session import Session


class TestCommit:
    """Test commit"""

    async def test_commit_success(self, session_with_messages: Session):
        """Test successful commit"""
        result = session_with_messages.commit()

        assert isinstance(result, dict)
        assert result.get("status") == "committed"
        assert "session_id" in result

    async def test_commit_extracts_memories(
        self, session_with_messages: Session, client: AsyncOpenViking
    ):
        """Test commit extracts memories"""
        result = session_with_messages.commit()

        assert "memories_extracted" in result
        # Wait for memory extraction to complete
        await client.wait_processed(timeout=60.0)

    async def test_commit_archives_messages(self, session_with_messages: Session):
        """Test commit archives messages"""
        initial_message_count = len(session_with_messages.messages)
        assert initial_message_count > 0

        result = session_with_messages.commit()

        assert result.get("archived") is True
        # Current message list should be cleared after commit
        assert len(session_with_messages.messages) == 0

    async def test_commit_empty_session(self, session: Session):
        """Test committing empty session"""
        # Empty session commit should not raise error
        result = session.commit()

        assert isinstance(result, dict)

    async def test_commit_multiple_times(self, client: AsyncOpenViking):
        """Test multiple commits"""
        session = client.session(session_id="multi_commit_test")

        # First round of conversation
        session.add_message("user", [TextPart("First round message")])
        session.add_message("assistant", [TextPart("First round response")])
        result1 = session.commit()
        assert result1.get("status") == "committed"

        # Second round of conversation
        session.add_message("user", [TextPart("Second round message")])
        session.add_message("assistant", [TextPart("Second round response")])
        result2 = session.commit()
        assert result2.get("status") == "committed"

    async def test_commit_with_usage_records(self, client: AsyncOpenViking):
        """Test commit with usage records"""
        session = client.session(session_id="usage_commit_test")

        session.add_message("user", [TextPart("Test message")])
        session.used(contexts=["viking://user/test/resources/doc.md"])
        session.add_message("assistant", [TextPart("Response")])

        result = session.commit()

        assert result.get("status") == "committed"
        assert "active_count_updated" in result
