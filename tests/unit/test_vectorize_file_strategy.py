import builtins
import logging
import types

import pytest

from openviking.core.context import Context
from openviking.storage.vectordb.vectorize.base import VectorizeResult
from openviking.storage.vectordb.vectorize.vectorizer import VectorizerAdapter
from openviking.utils import embedding_input, embedding_utils
from openviking.utils.embedding_input import EMBEDDING_TRUNCATION_SUFFIX


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


class DummyVectorizer:
    config = {"max_input_tokens": 20}

    def __init__(self):
        self.config = type(self).config
        self.data = None

    def get_dense_vector_dim(self, _dense_model, _sparse_model):
        return 1

    def vectorize_document(self, data, _dense_model, _sparse_model):
        self.data = data
        return VectorizeResult(dense_vectors=[[1.0] for _ in data])


class DummyVectorizerWithoutLimit(DummyVectorizer):
    config = {}


class DummyVectorizerDisabledLimit(DummyVectorizer):
    config = {"max_input_tokens": 0}


class DummyVectorizerMalformedConfig(DummyVectorizer):
    config = []


class DummyVectorizerMalformedLimit(DummyVectorizer):
    config = {"max_input_tokens": object()}


class DummyVectorizerStringLimit(DummyVectorizer):
    config = {"max_input_tokens": "20"}


class DummyVectorizerBoolLimit(DummyVectorizer):
    config = {"max_input_tokens": True}


class DummyVectorizerFloatLimit(DummyVectorizer):
    config = {"max_input_tokens": 20.5}


class DummyVectorizerRaisingConfig(DummyVectorizer):
    @property
    def config(self):
        raise RuntimeError("config unavailable")

    def __init__(self):
        self.data = None


def test_vectorizer_adapter_truncates_provider_text_without_mutating_raw_data():
    vectorizer = DummyVectorizer()
    adapter = VectorizerAdapter(
        vectorizer,
        {"Dense": {"TextField": "content", "ModelName": "dummy"}},
    )
    raw_text = "oversized memory content " * 80
    raw_data = [{"content": raw_text, "uri": "viking://user/default/resources/big.md"}]

    dense, sparse = adapter.vectorize_raw_data(raw_data)

    assert dense == [[1.0]]
    assert sparse == []
    provider_text = vectorizer.data[0]["text"]
    assert provider_text.endswith(EMBEDDING_TRUNCATION_SUFFIX)
    assert len(provider_text) < len(raw_text)
    assert raw_data[0]["content"] == raw_text


def test_vectorizer_adapter_accepts_integer_string_limit():
    vectorizer = DummyVectorizerStringLimit()
    adapter = VectorizerAdapter(
        vectorizer,
        {"Dense": {"TextField": "content", "ModelName": "dummy"}},
    )
    raw_text = "oversized memory content " * 80

    adapter.vectorize_raw_data([{"content": raw_text}])

    assert vectorizer.data[0]["text"].endswith(EMBEDDING_TRUNCATION_SUFFIX)


def test_vectorizer_adapter_import_failure_fails_closed(monkeypatch):
    real_import = builtins.__import__

    def fail_embedding_input_import(name, *args, **kwargs):
        if name == "openviking.utils.embedding_input":
            raise ImportError("import failed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_embedding_input_import)
    vectorizer = DummyVectorizer()
    adapter = VectorizerAdapter(
        vectorizer,
        {"Dense": {"TextField": "content", "ModelName": "dummy"}},
    )
    raw_text = "oversized memory content " * 80

    with pytest.raises(RuntimeError, match="truncation is unavailable"):
        adapter.vectorize_raw_data([{"content": raw_text}])


def test_vectorizer_adapter_truncation_failure_fails_closed(monkeypatch):
    def fail_truncation(_text, _max_input_tokens):
        raise RuntimeError("truncate failed")

    monkeypatch.setattr(embedding_input, "truncate_embedding_input", fail_truncation)
    vectorizer = DummyVectorizer()
    adapter = VectorizerAdapter(
        vectorizer,
        {"Dense": {"TextField": "content", "ModelName": "dummy"}},
    )
    raw_text = "oversized memory content " * 80

    with pytest.raises(RuntimeError, match="truncate failed"):
        adapter.vectorize_raw_data([{"content": raw_text}])


def test_vectorizer_adapter_raising_config_fails_closed():
    with pytest.raises(RuntimeError, match="failed to read vectorizer config"):
        VectorizerAdapter(
            DummyVectorizerRaisingConfig(),
            {"Dense": {"TextField": "content", "ModelName": "dummy"}},
        )


def test_vectorizer_adapter_preserves_media_fields_when_truncating_text():
    vectorizer = DummyVectorizer()
    adapter = VectorizerAdapter(
        vectorizer,
        {
            "Dense": {
                "TextField": "content",
                "ImageField": "image",
                "VideoField": "video",
                "ModelName": "dummy",
            }
        },
    )
    raw_text = "oversized memory content " * 80
    image = {"uri": "data:image/png;base64,aaa"}
    video = {"uri": "data:video/mp4;base64,bbb"}

    adapter.vectorize_raw_data([{"content": raw_text, "image": image, "video": video}])

    provider_data = vectorizer.data[0]
    assert provider_data["text"].endswith(EMBEDDING_TRUNCATION_SUFFIX)
    assert provider_data["image"] is image
    assert provider_data["video"] is video


def test_vectorizer_adapter_vectorize_one_truncates_text_and_preserves_media():
    vectorizer = DummyVectorizer()
    adapter = VectorizerAdapter(
        vectorizer,
        {"Dense": {"TextField": "content", "ModelName": "dummy"}},
    )
    raw_text = "oversized memory content " * 80
    image = {"uri": "data:image/png;base64,aaa"}
    video = {"uri": "data:video/mp4;base64,bbb"}

    adapter.vectorize_one(text=raw_text, image=image, video=video)

    provider_data = vectorizer.data[0]
    assert provider_data["text"].endswith(EMBEDDING_TRUNCATION_SUFFIX)
    assert provider_data["image"] is image
    assert provider_data["video"] is video


@pytest.mark.parametrize(
    "vectorizer_cls",
    [
        DummyVectorizerWithoutLimit,
        DummyVectorizerDisabledLimit,
        DummyVectorizerMalformedConfig,
        DummyVectorizerMalformedLimit,
        DummyVectorizerBoolLimit,
        DummyVectorizerFloatLimit,
    ],
)
def test_vectorizer_adapter_leaves_text_unchanged_without_explicit_limit(vectorizer_cls, caplog):
    if vectorizer_cls in {
        DummyVectorizerMalformedLimit,
        DummyVectorizerBoolLimit,
        DummyVectorizerFloatLimit,
    }:
        caplog.set_level(logging.WARNING)
    vectorizer = vectorizer_cls()
    adapter = VectorizerAdapter(
        vectorizer,
        {"Dense": {"TextField": "content", "ModelName": "dummy"}},
    )
    raw_text = "oversized memory content " * 80

    adapter.vectorize_raw_data([{"content": raw_text}])

    assert vectorizer.data[0]["text"] == raw_text
    if vectorizer_cls in {
        DummyVectorizerMalformedLimit,
        DummyVectorizerBoolLimit,
        DummyVectorizerFloatLimit,
    }:
        assert "max_input_tokens disabled" in caplog.text


def test_vectorizer_adapter_vectorize_one_leaves_text_unchanged_without_limit():
    vectorizer = DummyVectorizerWithoutLimit()
    adapter = VectorizerAdapter(
        vectorizer,
        {"Dense": {"TextField": "content", "ModelName": "dummy"}},
    )
    raw_text = "oversized memory content " * 80

    adapter.vectorize_one(text=raw_text)

    assert vectorizer.data[0]["text"] == raw_text


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
async def test_vectorize_unknown_file_reuses_summary_content_without_reread(monkeypatch):
    queue = DummyQueue()
    raw_content = "build:\n\tcargo build --locked\n"
    fs = DummyFS("should not be read")
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
        summary_dict={
            "name": "Makefile",
            "summary": "VLM generated build file summary",
            "content": raw_content,
        },
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "VLM generated build file summary"
    assert msg.context_data["content"] == raw_content
    assert fs.read_file_bytes_calls == 0
    assert fs.read_file_calls == 0


@pytest.mark.asyncio
async def test_vectorize_unknown_binary_file_falls_back_to_summary(monkeypatch):
    queue = DummyQueue()
    summary = "VLM generated binary file summary"
    binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    fs = DummyFS(binary_content)
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
        file_path="viking://user/default/resources/model.weights",
        summary_dict={"name": "model.weights", "summary": summary},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == summary
    assert msg.context_data["content"] == summary
    assert fs.read_file_bytes_calls == 1
    assert fs.read_file_calls == 0


@pytest.mark.asyncio
async def test_vectorize_unknown_unrecognizable_encoding_falls_back_to_summary(monkeypatch):
    queue = DummyQueue()
    summary = "VLM generated unknown file summary"
    fs = DummyFS(b"\xff\xfe\xfd")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils,
        "from_bytes",
        lambda _raw: types.SimpleNamespace(best=lambda: None),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/unknown.data",
        summary_dict={"name": "unknown.data", "summary": summary},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == summary
    assert msg.context_data["content"] == summary
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
async def test_vectorize_text_file_reuses_summary_content_without_reread(monkeypatch):
    queue = DummyQueue()
    raw_content = "# README\nraw text already read during summary\n"
    fs = DummyFS("should not be read")
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
        summary_dict={
            "name": "README.md",
            "summary": "summary for embedding",
            "content": raw_content,
        },
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "summary for embedding"
    assert msg.context_data["content"] == raw_content
    assert fs.read_file_calls == 0
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_text_bytes_sniffs_non_utf8_content(monkeypatch):
    queue = DummyQueue()
    raw_content = (
        "# 说明文档\n"
        "目标: 验证已知 TEXT 文件的 bytes 内容也会进行编码探测。\n"
        "说明: 这是一个中文 README 内容，用于测试 GB18030 编码识别。\n"
        "命令: openviking benchmark run\n"
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
        file_path="viking://user/default/resources/README.md",
        summary_dict={"name": "README.md", "summary": "summary for embedding"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "summary for embedding"
    assert msg.context_data["content"] == raw_content
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
