# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for Session COW (Copy-on-Write) mode and async commit functionality."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.session import Session
from openviking_cli.session.user_id import UserIdentifier


def _make_user() -> UserIdentifier:
    return UserIdentifier("test_account", "test_user", "test_agent")


def _make_session(viking_fs: MagicMock = None, session_id: str = "test_session_123") -> Session:
    user = _make_user()
    ctx = RequestContext(user=user, role=Role.ROOT)
    fs = viking_fs or MagicMock()
    return Session(
        viking_fs=fs,
        user=user,
        ctx=ctx,
        session_id=session_id,
    )


class TestCreateTempUris:
    """Tests for _create_temp_uris() method."""

    def test_returns_tuple_of_four_uris(self):
        session = _make_session()
        result = session._create_temp_uris()

        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_temp_base_uri_format(self):
        session = _make_session(session_id="sess_abc")
        user = session.user
        temp_base, _, _, _ = session._create_temp_uris()

        assert temp_base.startswith("viking://temp/session/")
        assert f"/{user.user_space_name()}/" in temp_base
        assert "/sess_abc/" in temp_base
        assert "/commit_" in temp_base

    def test_session_temp_uri_structure(self):
        session = _make_session(session_id="sess_abc")
        user = session.user
        temp_base, session_temp, _, _ = session._create_temp_uris()

        assert session_temp.startswith(temp_base)
        assert "/session/" in session_temp
        assert f"/{user.user_space_name()}/sess_abc" in session_temp

    def test_user_temp_uri_structure(self):
        session = _make_session()
        user = session.user
        temp_base, _, user_temp, _ = session._create_temp_uris()

        assert user_temp.startswith(temp_base)
        assert "/user/" in user_temp
        assert user_temp.endswith(f"/user/{user.user_space_name()}")

    def test_agent_temp_uri_structure(self):
        session = _make_session()
        user = session.user
        temp_base, _, _, agent_temp = session._create_temp_uris()

        assert agent_temp.startswith(temp_base)
        assert "/agent/" in agent_temp
        assert agent_temp.endswith(f"/agent/{user.agent_space_name()}")

    def test_sets_internal_state(self):
        session = _make_session()
        temp_base, session_temp, user_temp, agent_temp = session._create_temp_uris()

        assert session._temp_base_uri == temp_base
        assert session._session_temp_uri == session_temp
        assert session._user_temp_uri == user_temp
        assert session._agent_temp_uri == agent_temp
        assert session._temp_created_at is not None

    def test_temp_created_at_is_recent(self):
        session = _make_session()
        before = time.time()
        session._create_temp_uris()
        after = time.time()

        assert before <= session._temp_created_at <= after

    def test_commit_uuid_is_8_chars(self):
        session = _make_session()
        temp_base, _, _, _ = session._create_temp_uris()

        commit_part = temp_base.split("/commit_")[-1]
        assert len(commit_part) == 8
        assert all(c in "0123456789abcdef" for c in commit_part)

    def test_multiple_calls_generate_different_uuids(self):
        session = _make_session()
        temp_base1, _, _, _ = session._create_temp_uris()
        temp_base2, _, _, _ = session._create_temp_uris()

        assert temp_base1 != temp_base2


class TestCleanupTempUris:
    """Tests for _cleanup_temp_uris() method."""

    @pytest.mark.asyncio
    async def test_calls_delete_temp_on_viking_fs(self):
        viking_fs = MagicMock()
        viking_fs.delete_temp = AsyncMock()
        session = _make_session(viking_fs=viking_fs)

        session._create_temp_uris()
        saved_temp_base = session._temp_base_uri
        await session._cleanup_temp_uris()

        viking_fs.delete_temp.assert_called_once()
        call_args = viking_fs.delete_temp.call_args
        assert call_args[0][0] == saved_temp_base

    @pytest.mark.asyncio
    async def test_resets_internal_state(self):
        viking_fs = MagicMock()
        viking_fs.delete_temp = AsyncMock()
        session = _make_session(viking_fs=viking_fs)

        session._create_temp_uris()
        await session._cleanup_temp_uris()

        assert session._temp_base_uri is None
        assert session._session_temp_uri is None
        assert session._user_temp_uri is None
        assert session._agent_temp_uri is None
        assert session._temp_created_at is None

    @pytest.mark.asyncio
    async def test_no_cleanup_when_no_temp_uri(self):
        viking_fs = MagicMock()
        viking_fs.delete_temp = AsyncMock()
        session = _make_session(viking_fs=viking_fs)

        await session._cleanup_temp_uris()

        viking_fs.delete_temp.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_delete_exception(self):
        viking_fs = MagicMock()
        viking_fs.delete_temp = AsyncMock(side_effect=Exception("Delete failed"))
        session = _make_session(viking_fs=viking_fs)

        session._create_temp_uris()
        await session._cleanup_temp_uris()

        assert session._temp_base_uri is None

    @pytest.mark.asyncio
    async def test_passes_ctx_to_delete_temp(self):
        viking_fs = MagicMock()
        viking_fs.delete_temp = AsyncMock()
        session = _make_session(viking_fs=viking_fs)

        session._create_temp_uris()
        await session._cleanup_temp_uris()

        call_kwargs = viking_fs.delete_temp.call_args[1]
        assert "ctx" in call_kwargs
        assert call_kwargs["ctx"] == session.ctx


class TestEnqueueToSemanticQueue:
    """Tests for _enqueue_to_semantic_queue() method."""

    @pytest.mark.asyncio
    async def test_returns_list_of_three_msg_ids(self):
        session = _make_session()

        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()

        mock_queue_manager = MagicMock()
        mock_queue_manager.SEMANTIC = "semantic"
        mock_queue_manager.get_queue = MagicMock(return_value=mock_queue)

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            result = await session._enqueue_to_semantic_queue(
                session_temp_uri="viking://temp/session/test",
                user_temp_uri="viking://temp/user/test",
                agent_temp_uri="viking://temp/agent/test",
            )

        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_session_msg_has_correct_target_uri(self):
        session = _make_session(session_id="sess_xyz")
        user = session.user

        enqueued_msgs = []

        async def capture_enqueue(msg):
            enqueued_msgs.append(msg)

        mock_queue = MagicMock()
        mock_queue.enqueue = capture_enqueue

        mock_queue_manager = MagicMock()
        mock_queue_manager.SEMANTIC = "semantic"
        mock_queue_manager.get_queue = MagicMock(return_value=mock_queue)

        session_temp = f"viking://temp/session/{user.user_space_name()}/sess_xyz/commit_abc123/session/{user.user_space_name()}/sess_xyz"

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            await session._enqueue_to_semantic_queue(
                session_temp_uri=session_temp,
                user_temp_uri="viking://temp/user/test",
                agent_temp_uri="viking://temp/agent/test",
            )

        session_msg = enqueued_msgs[0]
        expected_target = f"viking://session/{user.user_space_name()}/sess_xyz"
        assert session_msg.target_uri == expected_target
        assert session_msg.uri == session_temp

    @pytest.mark.asyncio
    async def test_user_msg_has_correct_target_uri(self):
        session = _make_session()
        user = session.user

        enqueued_msgs = []

        async def capture_enqueue(msg):
            enqueued_msgs.append(msg)

        mock_queue = MagicMock()
        mock_queue.enqueue = capture_enqueue

        mock_queue_manager = MagicMock()
        mock_queue_manager.SEMANTIC = "semantic"
        mock_queue_manager.get_queue = MagicMock(return_value=mock_queue)

        user_temp = f"viking://temp/session/{user.user_space_name()}/sess/commit_abc/user/{user.user_space_name()}"

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            await session._enqueue_to_semantic_queue(
                session_temp_uri="viking://temp/session/test",
                user_temp_uri=user_temp,
                agent_temp_uri="viking://temp/agent/test",
            )

        user_msg = enqueued_msgs[1]
        expected_target = f"viking://user/{user.user_space_name()}"
        assert user_msg.target_uri == expected_target
        assert user_msg.uri == user_temp

    @pytest.mark.asyncio
    async def test_agent_msg_has_correct_target_uri(self):
        session = _make_session()
        user = session.user

        enqueued_msgs = []

        async def capture_enqueue(msg):
            enqueued_msgs.append(msg)

        mock_queue = MagicMock()
        mock_queue.enqueue = capture_enqueue

        mock_queue_manager = MagicMock()
        mock_queue_manager.SEMANTIC = "semantic"
        mock_queue_manager.get_queue = MagicMock(return_value=mock_queue)

        agent_temp = f"viking://temp/session/{user.user_space_name()}/sess/commit_abc/agent/{user.agent_space_name()}"

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            await session._enqueue_to_semantic_queue(
                session_temp_uri="viking://temp/session/test",
                user_temp_uri="viking://temp/user/test",
                agent_temp_uri=agent_temp,
            )

        agent_msg = enqueued_msgs[2]
        expected_target = f"viking://agent/{user.agent_space_name()}"
        assert agent_msg.target_uri == expected_target
        assert agent_msg.uri == agent_temp

    @pytest.mark.asyncio
    async def test_all_msgs_have_context_type_memory(self):
        session = _make_session()

        enqueued_msgs = []

        async def capture_enqueue(msg):
            enqueued_msgs.append(msg)

        mock_queue = MagicMock()
        mock_queue.enqueue = capture_enqueue

        mock_queue_manager = MagicMock()
        mock_queue_manager.SEMANTIC = "semantic"
        mock_queue_manager.get_queue = MagicMock(return_value=mock_queue)

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            await session._enqueue_to_semantic_queue(
                session_temp_uri="viking://temp/session/test",
                user_temp_uri="viking://temp/user/test",
                agent_temp_uri="viking://temp/agent/test",
            )

        for msg in enqueued_msgs:
            assert msg.context_type == "memory"

    @pytest.mark.asyncio
    async def test_all_msgs_have_recursive_true(self):
        session = _make_session()

        enqueued_msgs = []

        async def capture_enqueue(msg):
            enqueued_msgs.append(msg)

        mock_queue = MagicMock()
        mock_queue.enqueue = capture_enqueue

        mock_queue_manager = MagicMock()
        mock_queue_manager.SEMANTIC = "semantic"
        mock_queue_manager.get_queue = MagicMock(return_value=mock_queue)

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            await session._enqueue_to_semantic_queue(
                session_temp_uri="viking://temp/session/test",
                user_temp_uri="viking://temp/user/test",
                agent_temp_uri="viking://temp/agent/test",
            )

        for msg in enqueued_msgs:
            assert msg.recursive is True

    @pytest.mark.asyncio
    async def test_msgs_have_correct_user_context(self):
        user = _make_user()
        session = _make_session()

        enqueued_msgs = []

        async def capture_enqueue(msg):
            enqueued_msgs.append(msg)

        mock_queue = MagicMock()
        mock_queue.enqueue = capture_enqueue

        mock_queue_manager = MagicMock()
        mock_queue_manager.SEMANTIC = "semantic"
        mock_queue_manager.get_queue = MagicMock(return_value=mock_queue)

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            await session._enqueue_to_semantic_queue(
                session_temp_uri="viking://temp/session/test",
                user_temp_uri="viking://temp/user/test",
                agent_temp_uri="viking://temp/agent/test",
            )

        for msg in enqueued_msgs:
            assert msg.account_id == user.account_id
            assert msg.user_id == user.user_id
            assert msg.agent_id == user.agent_id


class TestTempUriStructureMatchesTarget:
    """Tests for temp URI structure matching target URI structure."""

    def test_session_temp_uri_contains_target_path(self):
        session = _make_session(session_id="sess_123")
        user = session.user

        temp_base, session_temp, _, _ = session._create_temp_uris()

        target_path = f"/session/{user.user_space_name()}/sess_123"
        assert session_temp.endswith(target_path)

    def test_user_temp_uri_contains_target_path(self):
        session = _make_session()
        user = session.user

        temp_base, _, user_temp, _ = session._create_temp_uris()

        target_path = f"/user/{user.user_space_name()}"
        assert user_temp.endswith(target_path)

    def test_agent_temp_uri_contains_target_path(self):
        session = _make_session()
        user = session.user

        temp_base, _, _, agent_temp = session._create_temp_uris()

        target_path = f"/agent/{user.agent_space_name()}"
        assert agent_temp.endswith(target_path)

    def test_all_temp_uris_share_same_base(self):
        session = _make_session()

        temp_base, session_temp, user_temp, agent_temp = session._create_temp_uris()

        assert session_temp.startswith(temp_base)
        assert user_temp.startswith(temp_base)
        assert agent_temp.startswith(temp_base)

    def test_temp_uri_structure_allows_semantic_dag_recursive_processing(self):
        session = _make_session(session_id="sess_xyz")
        user = session.user

        temp_base, session_temp, user_temp, agent_temp = session._create_temp_uris()

        assert "/session/" in session_temp
        assert f"/{user.user_space_name()}/sess_xyz" in session_temp

        assert "/user/" in user_temp
        assert f"/{user.user_space_name()}" in user_temp

        assert "/agent/" in agent_temp
        assert f"/{user.agent_space_name()}" in agent_temp


class TestTempUriWithDifferentUsers:
    """Tests for temp URI generation with different user configurations."""

    def test_different_user_space_names(self):
        user1 = UserIdentifier("acc1", "alice", "agent1")
        user2 = UserIdentifier("acc2", "bob", "agent2")

        session1 = Session(
            viking_fs=MagicMock(),
            user=user1,
            ctx=RequestContext(user=user1, role=Role.ROOT),
            session_id="sess1",
        )
        session2 = Session(
            viking_fs=MagicMock(),
            user=user2,
            ctx=RequestContext(user=user2, role=Role.ROOT),
            session_id="sess2",
        )

        _, session_temp1, user_temp1, agent_temp1 = session1._create_temp_uris()
        _, session_temp2, user_temp2, agent_temp2 = session2._create_temp_uris()

        assert "alice" in session_temp1
        assert "bob" in session_temp2
        assert user_temp1 != user_temp2
        assert agent_temp1 != agent_temp2

    def test_agent_space_name_is_hashed(self):
        user = UserIdentifier("acc", "myuser", "myagent")
        session = Session(
            viking_fs=MagicMock(),
            user=user,
            ctx=RequestContext(user=user, role=Role.ROOT),
            session_id="sess",
        )

        _, _, _, agent_temp = session._create_temp_uris()

        assert "myagent" not in agent_temp
        assert user.agent_space_name() in agent_temp
        assert len(user.agent_space_name()) == 12
