# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Context retrieval tests"""

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.session import Session


class TestGetContextForSearch:
    """Test get_context_for_search"""

    async def test_get_context_basic(self, session_with_messages: Session):
        """Test basic context retrieval"""
        context = session_with_messages.get_context_for_search(query="testing help")

        assert isinstance(context, dict)
        assert "summaries" in context or "recent_messages" in context

    async def test_get_context_with_max_messages(self, session_with_messages: Session):
        """Test limiting max messages"""
        context = session_with_messages.get_context_for_search(query="test", max_messages=2)

        assert isinstance(context, dict)
        if "recent_messages" in context:
            assert len(context["recent_messages"]) <= 2

    async def test_get_context_with_max_archives(self, client: AsyncOpenViking):
        """Test limiting max archives"""
        session = client.session(session_id="archive_context_test")

        # Add messages and commit (create archive)
        session.add_message("user", [TextPart("First message")])
        session.add_message("assistant", [TextPart("First response")])
        session.commit()

        # Add more messages
        session.add_message("user", [TextPart("Second message")])

        context = session.get_context_for_search(query="test", max_archives=1)

        assert isinstance(context, dict)

    async def test_get_context_empty_session(self, session: Session):
        """Test getting context from empty session"""
        context = session.get_context_for_search(query="test")

        assert isinstance(context, dict)

    async def test_get_context_after_commit(self, client: AsyncOpenViking):
        """Test getting context after commit"""
        session = client.session(session_id="post_commit_context_test")

        # Add messages
        session.add_message("user", [TextPart("Test message before commit")])
        session.add_message("assistant", [TextPart("Response before commit")])

        # Commit
        session.commit()

        # Add new messages
        session.add_message("user", [TextPart("New message after commit")])

        # Getting context should include archive summary
        context = session.get_context_for_search(query="test")

        assert isinstance(context, dict)
