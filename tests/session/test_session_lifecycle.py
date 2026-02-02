# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Session lifecycle tests"""

from openviking import AsyncOpenViking
from openviking.session import Session


class TestSessionCreate:
    """Test Session creation"""

    async def test_create_new_session(self, client: AsyncOpenViking):
        """Test creating new session"""
        session = client.session()

        assert session is not None
        assert session.session_id is not None
        assert len(session.session_id) > 0

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
        new_session.load()

        # Verify messages loaded
        assert len(new_session.messages) > 0

    async def test_load_nonexistent_session(self, client: AsyncOpenViking):
        """Test loading nonexistent session"""
        session = client.session(session_id="nonexistent_session_xyz")
        session.load()

        # Nonexistent session should be empty after loading
        assert len(session.messages) == 0

    async def test_session_properties(self, session: Session):
        """Test session properties"""
        assert hasattr(session, "uri")
        assert hasattr(session, "messages")
        assert hasattr(session, "session_id")
