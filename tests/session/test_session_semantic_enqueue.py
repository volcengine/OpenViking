from unittest.mock import MagicMock, patch

import pytest


class _FakeVikingFS:
    def __init__(self):
        self.writes = {}

    async def write_file(self, uri: str, content: str, ctx=None) -> None:
        self.writes[uri] = content


class _FakeSemanticQueue:
    def __init__(self):
        self.messages = []

    async def enqueue(self, msg):
        self.messages.append(msg)
        return msg.id


class _FakeQueueManager:
    SEMANTIC = "semantic"

    def __init__(self):
        self.queue = _FakeSemanticQueue()
        self.last_allow_create = None

    def get_queue(self, name: str, allow_create: bool = False):
        self.last_allow_create = allow_create
        return self.queue


@pytest.mark.asyncio
async def test_write_to_agfs_async_enqueues_session_semantic():
    from openviking.session.session import Session

    session = Session.__new__(Session)
    session._viking_fs = _FakeVikingFS()
    session._session_uri = "viking://session/test-space/test-session"
    session.ctx = MagicMock()
    session.ctx.account_id = "default"
    session.ctx.user.user_id = "default"
    session.ctx.user.agent_id = "default"
    session.ctx.role.value = "root"
    session._generate_abstract = MagicMock(return_value="ab")
    session._generate_overview = MagicMock(return_value="ov")

    fake_qm = _FakeQueueManager()
    with patch("openviking.storage.queuefs.get_queue_manager", return_value=fake_qm):
        await session._write_to_agfs_async(messages=[])

    assert fake_qm.last_allow_create is True
    assert len(fake_qm.queue.messages) == 1
    msg = fake_qm.queue.messages[0]
    assert msg.uri == session._session_uri
    assert msg.context_type == "session"
    assert msg.recursive is False


@pytest.mark.asyncio
async def test_write_archive_async_enqueues_archive_semantic():
    from openviking.session.session import Session

    session = Session.__new__(Session)
    session._viking_fs = _FakeVikingFS()
    session._session_uri = "viking://session/test-space/test-session"
    session.ctx = MagicMock()
    session.ctx.account_id = "default"
    session.ctx.user.user_id = "default"
    session.ctx.user.agent_id = "default"
    session.ctx.role.value = "root"

    fake_qm = _FakeQueueManager()
    with patch("openviking.storage.queuefs.get_queue_manager", return_value=fake_qm):
        await session._write_archive_async(
            index=1,
            messages=[MagicMock(to_jsonl=MagicMock(return_value="{}"))],
            abstract="ab",
            overview="ov",
        )

    assert fake_qm.last_allow_create is True
    assert len(fake_qm.queue.messages) == 1
    msg = fake_qm.queue.messages[0]
    assert msg.uri == f"{session._session_uri}/history/archive_001"
    assert msg.context_type == "session"
    assert msg.recursive is False
