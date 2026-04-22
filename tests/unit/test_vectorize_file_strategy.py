import types

import pytest

from openviking.core.context import Context
from openviking.utils import embedding_utils


class DummyQueue:
    def __init__(self):
        self.items = []

    async def enqueue(self, msg):
        self.items.append(msg)


class DummyQueueManager:
    EMBEDDING = "embedding"

    def __init__(self, queue):
        self._queue = queue

    def get_queue(self, _name):
        return self._queue


class DummyFS:
    def __init__(self, content, mod_time='2026-04-14T01:32:29Z'):
        self.content = content
        self.mod_time = mod_time

    async def read_file(self, _path, ctx=None):
        return self.content

    async def stat(self, _path, ctx=None):
        return {'modTime': self.mod_time}


class DummyUser:
    account_id = "default"

    def user_space_name(self):
        return "user/default"

    def agent_space_name(self):
        return "agent/default"


class DummyReq:
    def __init__(self):
        self.user = DummyUser()
        self.account_id = "default"


@pytest.mark.asyncio
async def test_vectorize_file_uses_summary_first(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("X" * 5000))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_chars=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils.EmbeddingMsgConverter,
        "from_context",
        lambda context: context,
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/test.md",
        summary_dict={"name": "test.md", "summary": "short summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    assert isinstance(queue.items[0], Context)
    assert queue.items[0].get_vectorization_text() == "short summary"


@pytest.mark.asyncio
async def test_vectorize_file_truncates_content_when_content_only(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("A" * 1500))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_chars=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils.EmbeddingMsgConverter,
        "from_context",
        lambda context: context,
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/test.md",
        summary_dict={"name": "test.md", "summary": "short summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    text = queue.items[0].get_vectorization_text()
    assert text.startswith("A" * 1000)
    assert text.endswith("...(truncated for embedding)")


@pytest.mark.asyncio
async def test_vectorize_file_preserves_created_at_and_uses_fs_mod_time(monkeypatch):
    queue = DummyQueue()
    mod_time = '2026-04-14T01:33:26Z'
    created_at = '2026-04-14T01:32:29Z'

    async def fake_get_existing_created_at(*_args, **_kwargs):
        return embedding_utils._coerce_datetime(created_at)

    monkeypatch.setattr(embedding_utils, 'get_queue_manager', lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, 'get_viking_fs', lambda: DummyFS('content', mod_time=mod_time))
    monkeypatch.setattr(
        embedding_utils,
        'get_openviking_config',
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source='summary_first', max_input_chars=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils,
        '_get_existing_created_at',
        fake_get_existing_created_at,
    )
    monkeypatch.setattr(
        embedding_utils.EmbeddingMsgConverter,
        'from_context',
        lambda context: context,
    )

    await embedding_utils.vectorize_file(
        file_path='viking://user/default/resources/test.md',
        summary_dict={'name': 'test.md', 'summary': 'short summary'},
        parent_uri='viking://user/default/resources',
        ctx=DummyReq(),
        preserve_existing_created_at=True,
    )

    assert len(queue.items) == 1
    context = queue.items[0]
    assert context.created_at == embedding_utils._coerce_datetime(created_at)
    assert context.updated_at == embedding_utils._coerce_datetime(mod_time)


@pytest.mark.asyncio
async def test_vectorize_file_uses_fs_mod_time_for_created_at_by_default(monkeypatch):
    queue = DummyQueue()
    mod_time = '2026-04-14T01:33:26Z'
    created_at = '2026-04-14T01:32:29Z'

    async def fake_get_existing_created_at(*_args, **_kwargs):
        return embedding_utils._coerce_datetime(created_at)

    monkeypatch.setattr(embedding_utils, 'get_queue_manager', lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, 'get_viking_fs', lambda: DummyFS('content', mod_time=mod_time))
    monkeypatch.setattr(
        embedding_utils,
        'get_openviking_config',
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source='summary_first', max_input_chars=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils,
        '_get_existing_created_at',
        fake_get_existing_created_at,
    )
    monkeypatch.setattr(
        embedding_utils.EmbeddingMsgConverter,
        'from_context',
        lambda context: context,
    )

    await embedding_utils.vectorize_file(
        file_path='viking://user/default/resources/test.md',
        summary_dict={'name': 'test.md', 'summary': 'short summary'},
        parent_uri='viking://user/default/resources',
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    context = queue.items[0]
    assert context.created_at == embedding_utils._coerce_datetime(mod_time)
    assert context.updated_at == embedding_utils._coerce_datetime(mod_time)
