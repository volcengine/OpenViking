# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Usage record tests"""

from openviking.message import TextPart
from openviking.session import Session


class TestUsed:
    """Test usage recording"""

    async def test_used_contexts(self, session: Session):
        """Test recording used contexts"""
        # Add some messages first
        session.add_message("user", [TextPart("Test message")])

        # Record used contexts
        session.used(
            contexts=[
                "viking://user/test/resources/doc1.md",
                "viking://user/test/resources/doc2.md",
            ]
        )

        # Verify usage records
        assert len(session.usage_records) > 0

    async def test_used_skill(self, session: Session):
        """Test recording used skill"""
        session.add_message("user", [TextPart("Test message")])

        session.used(skill={"uri": "viking://agent/skills/search", "name": "search_skill"})

        assert len(session.usage_records) > 0

    async def test_used_both(self, session: Session):
        """Test recording both context and skill"""
        session.add_message("user", [TextPart("Test message")])

        session.used(
            contexts=["viking://user/test/resources/doc.md"],
            skill={"uri": "viking://agent/skills/analyze", "name": "analyze_skill"},
        )

        assert len(session.usage_records) > 0

    async def test_used_multiple_times(self, session: Session):
        """Test recording usage multiple times"""
        session.add_message("user", [TextPart("Message 1")])
        session.used(contexts=["viking://user/test/resources/doc1.md"])

        session.add_message("user", [TextPart("Message 2")])
        session.used(contexts=["viking://user/test/resources/doc2.md"])

        # Should have multiple usage records
        assert len(session.usage_records) >= 2

    async def test_used_empty(self, session: Session):
        """Test empty usage record"""
        session.add_message("user", [TextPart("Test message")])

        # No parameters passed
        session.used()

        # Should not raise error
