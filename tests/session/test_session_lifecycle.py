# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Session lifecycle tests"""

import re

from openviking import AsyncOpenViking
from openviking.session import Session


class TestSessionCreate:
    """Test Session creation"""

    async def test_create_new_session(self, client: AsyncOpenViking):
        """Test creating new session"""
        session = client.session()

        assert session is not None
        assert session.session_id is not None
        assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{16}", session.session_id)

    async def test_create_with_id(self, client: AsyncOpenViking):
        """Test creating session with specified ID"""
        session_id = "custom_session_id_123"
        session = client.session(session_id=session_id)

        assert session.session_id == session_id

    async def test_create_multiple_sessions(self, client: AsyncOpenViking):
        """Test creating multiple sessions"""
        session1 = client.session(session_id="session_1")
        session2 = client.session(session_id="session_2")

        assert session1.session_id != session2.session_id

    async def test_session_uri(self, session: Session):
        """Test session URI"""
        uri = session.uri

        assert uri.startswith("viking://")
        assert "session" in uri
        assert session.session_id in uri


class TestSessionLoad:
    """Test Session loading"""

    async def test_load_existing_session(
        self, session_with_messages: Session, client: AsyncOpenViking
    ):
        """Test loading existing session"""
        session_id = session_with_messages.session_id

        # Create new session instance and load
        new_session = client.session(session_id=session_id)
        await new_session.load()

        # Verify messages loaded
        assert len(new_session.messages) > 0

    async def test_load_nonexistent_session(self, client: AsyncOpenViking):
        """Test loading nonexistent session"""
        session = client.session(session_id="nonexistent_session_xyz")
        await session.load()

        # Nonexistent session should be empty after loading
        assert len(session.messages) == 0

    async def test_session_properties(self, session: Session):
        """Test session properties"""
        assert hasattr(session, "uri")
        assert hasattr(session, "messages")
        assert hasattr(session, "session_id")


class TestSessionMustExist:
    """Test session(must_exist=True) raises when session does not exist."""

    async def test_must_exist_raises_for_nonexistent(self, client: AsyncOpenViking):
        """must_exist=True should raise NotFoundError for an unknown session_id."""
        import pytest

        from openviking_cli.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            client.session(session_id="definitely_not_a_real_session", must_exist=True)

    async def test_must_exist_succeeds_after_create(self, client: AsyncOpenViking):
        """must_exist=True should succeed for a session created via create_session()."""
        result = await client.create_session()
        existing_id = result["session_id"]

        session = client.session(session_id=existing_id, must_exist=True)
        assert session.session_id == existing_id

    async def test_must_exist_false_default_accepts_unknown_id(self, client: AsyncOpenViking):
        """Default must_exist=False should silently accept any session_id (backward compat)."""
        session = client.session(session_id="fabricated_id_abc")
        await session.load()
        assert session.session_id == "fabricated_id_abc"


class TestSessionExists:
    """Test session_exists() convenience method."""

    async def test_session_exists_true_after_create(self, client: AsyncOpenViking):
        """session_exists() should return True for a created session."""
        result = await client.create_session()
        session_id = result["session_id"]

        assert await client.session_exists(session_id) is True

    async def test_session_exists_false_for_unknown(self, client: AsyncOpenViking):
        """session_exists() should return False for an unknown session_id."""
        assert await client.session_exists("definitely_not_a_real_session") is False

    async def test_session_exists_true_after_add_message(
        self, session_with_messages: Session, client: AsyncOpenViking
    ):
        """session_exists() should return True for a session that has messages."""
        assert await client.session_exists(session_with_messages.session_id) is True


class TestExtractAbstractFromSummary:
    """Regression coverage for Session._extract_abstract_from_summary (issue #3136)."""

    @staticmethod
    def _session() -> "Session":
        """Return a bare Session shell sufficient for calling the pure helper."""
        from openviking.session.session import Session as _S

        return _S.__new__(_S)

    def test_skips_markdown_heading_only_summary(self):
        """Must not write '# Working Memory' as the archive abstract (issue #3136)."""
        s = self._session()
        summary = "# Working Memory\n\n- User likes reading sci-fi books"
        abstract = s._extract_abstract_from_summary(summary)
        assert "# Working Memory" not in abstract
        assert "sci-fi" in abstract

    def test_skips_deep_headings_and_separators(self):
        """## Heading, ### subheading, --- separators should all be skipped."""
        s = self._session()
        summary = (
            "## Session Title\n"
            "---\n"
            "### Subsection\n"
            "***\n"
            "- First bullet about project planning\n"
            "- Second bullet\n"
        )
        abstract = s._extract_abstract_from_summary(summary)
        assert "Session Title" not in abstract
        assert "Subsection" not in abstract
        assert "project planning" in abstract

    def test_preserves_legacy_bold_keyed_label(self):
        """A `**Label**:` line should still win when present (backwards compat)."""
        s = self._session()
        summary = (
            "# Should be skipped\n"
            "**Executive Summary**: User researches memory systems for LLM agents\n"
        )
        abstract = s._extract_abstract_from_summary(summary)
        assert "Executive Summary" not in abstract
        assert "memory systems" in abstract
        assert "User researches memory systems for LLM agents" == abstract

    def test_returns_empty_when_only_headings_and_whitespace(self):
        """Degrade to empty string rather than leaking a title heading."""
        s = self._session()
        summary = "# Heading\n## Subheading\n\n  \n---\n\n"
        assert s._extract_abstract_from_summary(summary) == ""
        assert s._extract_abstract_from_summary("") == ""
        assert s._extract_abstract_from_summary("     \n\n") == ""

    def test_short_plain_body_kept_unchanged(self):
        """A valid short paragraph (no heading/bullet) should be preserved."""
        s = self._session()
        summary = "The user investigated 3 retrieval strategies and prefers hybrid search."
        assert s._extract_abstract_from_summary(summary) == summary

    def test_bullet_without_heading_returns_first_item(self):
        """Standalone numbered / dashed lists should drop the bullet prefix."""
        s = self._session()
        dashed = "- Drafted proposal for memory deduplication\n- Second item"
        numbered = "1. Drafted proposal for memory deduplication\n2. Second"
        for src in (dashed, numbered):
            abs_val = s._extract_abstract_from_summary(src)
            assert abs_val.startswith("Drafted proposal for memory deduplication"), abs_val
            assert not abs_val.startswith("- ") and not abs_val.startswith("1. ")

    def test_long_line_truncated_with_ellipsis(self):
        """Lines longer than 200 chars should be truncated with ellipsis."""
        s = self._session()
        long_word = "abcdefghij" * 25  # 250 chars
        result = s._extract_abstract_from_summary(long_word)
        assert len(result) == 201  # 200 chars + ellipsis
        assert result.endswith("…")
        assert not result.startswith("#")

    def test_atx_headings_with_leading_whitespace_still_skipped(self):
        """Up to 3 leading spaces before '#' still count as ATX headings per CommonMark."""
        s = self._session()
        summary = "   #  Padded heading\n\nReal content after padding"
        abstract = s._extract_abstract_from_summary(summary)
        assert "Padded heading" not in abstract
        assert "Real content after padding" == abstract

