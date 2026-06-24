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
    def __init__(self, content):
        self.content = content

    async def read_file(self, _path, ctx=None):
        return self.content

    async def exists(self, _path, ctx=None):
        return False

    async def ls(self, _uri, ctx=None):
        return []


class DummyUser:
    account_id = "default"
    user_id = "default"

    def user_space_name(self):
        return "default"


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
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
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
async def test_vectorize_file_preserves_content_until_embedder_input_guard(monkeypatch):
    queue = DummyQueue()
    content = " ".join(f"token-{i}" for i in range(200))
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS(content))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=20)
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
    assert text == content


@pytest.mark.asyncio
async def test_index_resource_skips_session_namespace(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils.EmbeddingMsgConverter,
        "from_context",
        lambda context: context,
    )

    await embedding_utils.index_resource(
        uri="viking://session/default/sess_001/history/archive_001",
        ctx=DummyReq(),
    )

    assert queue.items == []


def test_truncate_abstract_bytes_caps_below_byte_limit():
    # small values pass through unchanged
    assert embedding_utils._truncate_abstract_bytes("small") == "small"
    assert embedding_utils._truncate_abstract_bytes("") == ""
    # oversized value is capped AND stays valid UTF-8 (no split multibyte char)
    big = "你" * 30_000  # 90,000 UTF-8 bytes, over the 65535 bytes_row cap
    capped = embedding_utils._truncate_abstract_bytes(big)
    encoded = capped.encode("utf-8")
    assert len(encoded) <= embedding_utils._ABSTRACT_MAX_BYTES
    assert encoded.decode("utf-8") == capped


@pytest.mark.asyncio
async def test_vectorize_file_truncates_oversized_abstract(monkeypatch):
    """An oversized file summary must be capped before it becomes the `abstract`
    scalar, otherwise the vector-store bytes_row write fails (string field >
    65535 bytes) and the resource is silently never vectorized."""
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils.EmbeddingMsgConverter, "from_context", lambda context: context
    )

    oversized = "你" * 30_000  # 90,000 UTF-8 bytes
    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/big.md",
        summary_dict={"name": "big.md", "summary": oversized},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    abstract = queue.items[0].abstract
    assert len(abstract.encode("utf-8")) <= embedding_utils._ABSTRACT_MAX_BYTES
    assert abstract.encode("utf-8").decode("utf-8") == abstract  # valid UTF-8


@pytest.mark.asyncio
async def test_vectorize_directory_meta_truncates_oversized_abstract(monkeypatch):
    """The directory-meta path (fed by index_resource reading .abstract.md) must
    cap the abstract scalar on every enqueued Context (abstract + overview)."""
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))
    monkeypatch.setattr(
        embedding_utils.EmbeddingMsgConverter, "from_context", lambda context: context
    )

    oversized = "你" * 30_000  # 90,000 UTF-8 bytes
    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/dir",
        abstract=oversized,
        overview="overview text",
        ctx=DummyReq(),
    )

    assert queue.items  # at least the abstract-level Context was enqueued
    for item in queue.items:
        assert isinstance(item, Context)
        assert len(item.abstract.encode("utf-8")) <= embedding_utils._ABSTRACT_MAX_BYTES
        assert item.abstract.encode("utf-8").decode("utf-8") == item.abstract
