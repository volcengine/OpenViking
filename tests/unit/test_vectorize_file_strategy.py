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
        self.read_file_calls = 0
        self.read_file_bytes_calls = 0

    async def read_file(self, _path, ctx=None):
        self.read_file_calls += 1
        return self.content

    async def read_file_bytes(self, _path, ctx=None):
        self.read_file_bytes_calls += 1
        if isinstance(self.content, bytes):
            return self.content
        return str(self.content).encode("utf-8")

    async def exists(self, _path, ctx=None):
        return False

    async def ls(self, _uri, ctx=None):
        return []


class DummyUser:
    account_id = "default"
    user_id = "default"

    def user_space_name(self):
        return "default"

    def to_dict(self):
        return {"account_id": self.account_id, "user_id": self.user_id}


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
async def test_vectorize_unknown_text_file_embeds_summary_but_indexes_raw_content(monkeypatch):
    queue = DummyQueue()
    raw_makefile = "build:\n\tcargo build --locked\n"
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS(raw_makefile))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/Makefile",
        summary_dict={"name": "Makefile", "summary": "VLM generated build file summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "VLM generated build file summary"
    assert msg.context_data["content"] == raw_makefile


@pytest.mark.asyncio
async def test_vectorize_unknown_text_file_sniffs_non_utf8_raw_content(monkeypatch):
    queue = DummyQueue()
    raw_content = (
        "# 构建脚本\n"
        "目标: 编译项目\n"
        "说明: 这是一个中文 Makefile 内容，用于测试编码探测。\n"
        "命令: cargo build --locked\n"
    )
    fs = DummyFS(raw_content.encode("gb18030"))
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/Makefile",
        summary_dict={"name": "Makefile", "summary": "VLM generated build file summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "VLM generated build file summary"
    assert msg.context_data["content"] == raw_content
    assert fs.read_file_bytes_calls == 1
    assert fs.read_file_calls == 0


@pytest.mark.asyncio
async def test_vectorize_text_summary_first_reuses_single_file_read(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("# README\nraw text for bm25\n")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/README.md",
        summary_dict={"name": "README.md", "summary": "summary for embedding"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "summary for embedding"
    assert msg.context_data["content"] == "# README\nraw text for bm25\n"
    assert fs.read_file_calls == 1
    assert fs.read_file_bytes_calls == 0


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
