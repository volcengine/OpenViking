# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Session lifecycle tests"""

import re

from openviking.server.identity import RequestContext
from openviking.service.core import OpenVikingService
from openviking.session import Session


class TestSessionCreate:
    """Test Session creation"""

    async def test_create_new_session(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """Test creating new session"""
        session = service.sessions.session(request_context)

        assert session is not None
        assert session.session_id is not None
        assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{16}", session.session_id)

    async def test_create_with_id(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """Test creating session with specified ID"""
        session_id = "custom_session_id_123"
        session = service.sessions.session(request_context, session_id=session_id)

        assert session.session_id == session_id

    async def test_create_multiple_sessions(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """Test creating multiple sessions"""
        session1 = service.sessions.session(request_context, session_id="session_1")
        session2 = service.sessions.session(request_context, session_id="session_2")

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
        self,
        session_with_messages: Session,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """Test loading existing session"""
        session_id = session_with_messages.session_id

        # Create new session instance and load
        new_session = service.sessions.session(request_context, session_id=session_id)
        await new_session.load()

        # Verify messages loaded
        assert len(new_session.messages) > 0

    async def test_load_nonexistent_session(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """Test loading nonexistent session"""
        session = service.sessions.session(
            request_context,
            session_id="nonexistent_session_xyz",
        )
        await session.load()

        # Nonexistent session should be empty after loading
        assert len(session.messages) == 0

    async def test_load_ignores_non_archive_history_entries(self, client: AsyncOpenViking):
        session = client.session(session_id="archive_name_validation_test")
        for name in ("archive_001", "archive_002", "archive_001.bak.20260628"):
            await session._viking_fs.mkdir(
                f"{session.uri}/history/{name}",
                exist_ok=True,
                ctx=session.ctx,
            )

        loaded = client.session(session_id=session.session_id)
        await loaded.load()

        assert loaded.compression.compression_index == 2
        assert loaded.stats.compression_count == 2

    async def test_session_properties(self, session: Session):
        """Test session properties"""
        assert hasattr(session, "uri")
        assert hasattr(session, "messages")
        assert hasattr(session, "session_id")


class TestSessionServiceGet:
    """Test persisted-session lookup semantics."""

    async def test_get_raises_for_nonexistent(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """SessionService.get should reject an unknown session ID."""
        import pytest

        from openviking_cli.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await service.sessions.get("definitely_not_a_real_session", request_context)

    async def test_get_succeeds_after_create(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """SessionService.get should return a persisted session."""
        created = await service.sessions.create(request_context)
        existing_id = created.session_id

        session = await service.sessions.get(existing_id, request_context)
        assert session.session_id == existing_id

    async def test_session_factory_accepts_unpersisted_id(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        session = service.sessions.session(request_context, session_id="fabricated_id_abc")
        await session.load()
        assert session.session_id == "fabricated_id_abc"


class TestSessionExists:
    """Test persisted session existence."""

    async def test_session_exists_true_after_create(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """session_exists() should return True for a created session."""
        session = await service.sessions.create(request_context)

        assert await session.exists() is True

    async def test_session_exists_false_for_unknown(
        self,
        service: OpenVikingService,
        request_context: RequestContext,
    ):
        """session_exists() should return False for an unknown session_id."""
        session = service.sessions.session(
            request_context,
            session_id="definitely_not_a_real_session",
        )
        assert await session.exists() is False

    async def test_session_exists_true_after_add_message(
        self,
        session_with_messages: Session,
    ):
        """session_exists() should return True for a session that has messages."""
        assert await session_with_messages.exists() is True
