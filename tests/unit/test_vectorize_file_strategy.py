import base64
import io
import types

import pytest
from PIL import Image

from openviking.core.context import ResourceContentType
from openviking.parse.parsers.media.utils import (
    MPEG_TS_PACKET_SIZE,
    MPEG_TS_PROBE_BYTES,
)
from openviking.utils import embedding_utils
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.utils.config.parser_config import ImageConfig


class DummyQueue:
    def __init__(self):
        self.items = []

    async def enqueue(self, msg):
        self.items.append(msg)
        return "queue-message-id"


class DummyQueueWithId(DummyQueue):
    async def enqueue(self, msg):
        self.items.append(msg)
        return "queue-message-id"


class FailingQueue(DummyQueue):
    async def enqueue(self, msg):
        self.items.append(msg)
        raise RuntimeError("queue unavailable")


class DummyQueueManager:
    EMBEDDING = "embedding"

    def __init__(self, queue):
        self._queue = queue

    def get_queue(self, _name):
        return self._queue


class DummyFS:
    def __init__(self, content):
        self.content = content
        self.read_calls = []
        self.read_file_calls = 0
        self.read_file_bytes_calls = 0

    async def read(self, _path, offset=0, size=-1, ctx=None):
        self.read_calls.append((offset, size))
        raw = self.content if isinstance(self.content, bytes) else str(self.content).encode("utf-8")
        return raw[offset : offset + size]

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


def _jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _data_uri_image_size(data_uri: str) -> tuple[int, int]:
    _, encoded = data_uri.split(";base64,", 1)
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as img:
        return img.size


def test_get_resource_content_type_recognizes_media_extensions():
    expected = {
        "recording.ogg": ResourceContentType.AUDIO,
        "RECORDING.OPUS": ResourceContentType.AUDIO,
        "recording.mkv": ResourceContentType.VIDEO,
        "RECORDING.WEBM": ResourceContentType.VIDEO,
        "source.ts": ResourceContentType.TEXT,
    }

    for filename, content_type in expected.items():
        assert embedding_utils.get_resource_content_type(filename) == content_type


def _mpeg_ts_bytes() -> bytes:
    content = bytearray(MPEG_TS_PROBE_BYTES)
    for offset in range(0, MPEG_TS_PROBE_BYTES, MPEG_TS_PACKET_SIZE):
        content[offset] = 0x47
    return bytes(content)


@pytest.mark.asyncio
async def test_vectorize_disambiguates_typescript_and_mpeg_ts(monkeypatch):
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    async def vectorize(filename, content, summary):
        queue = DummyQueue()
        fs = DummyFS(content)
        monkeypatch.setattr(
            embedding_utils,
            "get_queue_manager",
            lambda: DummyQueueManager(queue),
        )
        monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
        await embedding_utils.vectorize_file(
            file_path=f"viking://user/default/resources/{filename}",
            summary_dict={"name": filename, "summary": summary},
            parent_uri="viking://user/default/resources",
            ctx=DummyReq(),
        )
        return queue, fs

    text_queue, text_fs = await vectorize(
        "source.ts", "export const answer: number = 42;", "TypeScript source"
    )
    video_queue, video_fs = await vectorize("broadcast.ts", _mpeg_ts_bytes(), "")

    assert text_fs.read_file_calls == 1
    assert text_queue.items[0].message == "export const answer: number = 42;"
    assert "content" not in text_queue.items[0].context_data
    assert video_fs.read_file_calls == 0
    assert video_queue.items[0].message == "broadcast.ts"
    assert "content" not in video_queue.items[0].context_data


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
    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/test.md",
        summary_dict={"name": "test.md", "summary": "short summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    assert queue.items[0].message == "short summary"
    assert "content" not in queue.items[0].context_data


@pytest.mark.asyncio
async def test_vectorize_image_downsamples_large_embedding_input(monkeypatch):
    queue = DummyQueue()
    original = _jpeg_bytes(80, 220)
    fs = DummyFS(original)
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000),
            image=ImageConfig(
                preview_max_dimension=64,
                max_file_size_mb=100.0,
                large_image_threshold_dimension=100,
            ),
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://resources/docs/large.jpg",
        summary_dict={"name": "large.jpg", "summary": "large image"},
        parent_uri="viking://resources/docs",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    message = queue.items[0].message
    image_part = next(part for part in message if part["type"] == "image_url")
    width, height = _data_uri_image_size(image_part["image_url"]["url"])
    assert max(width, height) <= 64
    assert fs.content == original
    assert fs.read_file_bytes_calls == 1


@pytest.mark.asyncio
async def test_vectorize_file_registers_request_wait_with_embedding_msg_id(monkeypatch):
    queue = DummyQueueWithId()
    registered = []
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("hello"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils,
        "get_request_wait_tracker",
        lambda: types.SimpleNamespace(
            register_embedding_root=lambda telemetry_id, root_id: registered.append(
                (telemetry_id, root_id)
            )
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/test.md",
        summary_dict={"name": "test.md", "summary": ""},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    assert registered == [(queue.items[0].telemetry_id, queue.items[0].id)]
    assert registered[0][1] != "queue-message-id"


@pytest.mark.asyncio
async def test_vectorize_file_marks_registered_wait_root_failed_when_enqueue_raises(monkeypatch):
    queue = FailingQueue()
    registered = []
    failed = []
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("hello"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )
    monkeypatch.setattr(
        embedding_utils,
        "get_request_wait_tracker",
        lambda: types.SimpleNamespace(
            register_embedding_root=lambda telemetry_id, root_id: registered.append(
                (telemetry_id, root_id)
            ),
            mark_embedding_failed=lambda telemetry_id, root_id, message: failed.append(
                (telemetry_id, root_id, message)
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await embedding_utils.vectorize_file(
            file_path="viking://user/default/resources/test.md",
            summary_dict={"name": "test.md", "summary": ""},
            parent_uri="viking://user/default/resources",
            ctx=DummyReq(),
        )

    assert len(queue.items) == 1
    assert registered == [(queue.items[0].telemetry_id, queue.items[0].id)]
    assert failed
    assert failed[0][0:2] == registered[0]


@pytest.mark.asyncio
async def test_vectorize_file_propagates_enqueue_failure(monkeypatch):
    monkeypatch.setattr(
        embedding_utils, "get_queue_manager", lambda: DummyQueueManager(FailingQueue())
    )
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("content"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="summary_first", max_input_tokens=1000)
        ),
    )
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await embedding_utils.vectorize_file(
            "viking://user/default/resources/test.md",
            {"name": "test.md", "summary": "summary"},
            "viking://user/default/resources",
            ctx=DummyReq(),
        )


@pytest.mark.asyncio
async def test_vectorize_directory_propagates_enqueue_failures(monkeypatch):
    monkeypatch.setattr(
        embedding_utils, "get_queue_manager", lambda: DummyQueueManager(FailingQueue())
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await embedding_utils.vectorize_directory_meta(
            "viking://user/default/resources",
            abstract="abstract",
            overview="overview",
            ctx=DummyReq(),
        )


@pytest.mark.asyncio
async def test_vectorize_unknown_text_file_embeds_summary_without_reading_content(monkeypatch):
    queue = DummyQueue()
    raw_makefile = "build:\n\tcargo build --locked\n"
    fs = DummyFS(raw_makefile)
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
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_unknown_text_file_without_summary_uses_bounded_content(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("build:\n\tcargo build --release\n")
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    enqueued = await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/Makefile",
        summary_dict={"name": "Makefile", "summary": ""},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert enqueued is True
    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "build:\n\tcargo build --release\n"
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 1


@pytest.mark.asyncio
async def test_vectorize_empty_content_skips_enqueue(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS(""))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    enqueued = await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/obsolete.md",
        summary_dict={"name": "obsolete.md", "summary": ""},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert enqueued is False
    assert queue.items == []


@pytest.mark.asyncio
async def test_vectorize_file_writes_search_tags_into_embedding_context(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("deployment guide"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/demo.md",
        summary_dict={"name": "demo.md", "summary": "deployment summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(["team=search", "env=test"]),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.context_data["search_tags"] == ["team=search", "env=test"]


@pytest.mark.asyncio
async def test_vectorize_file_appends_search_tags_to_existing_record_tags(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("deployment guide"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/demo.md",
        summary_dict={"name": "demo.md", "summary": "deployment summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(
            ["env=prod", "team=search"],
            mode="append",
        ),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.context_data["search_tags"] == ["env=prod", "team=search"]
    assert msg.context_data["_upsert_options"] == {"search_tag_mode": "append"}


@pytest.mark.asyncio
async def test_vectorize_file_append_does_not_read_existing_tags(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("deployment guide"))
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    class DummyVikingDB:
        async def filter(self, **_kwargs):
            raise AssertionError("vectorize must not read existing tags")

    monkeypatch.setattr(
        "openviking.server.dependencies.get_service",
        lambda: types.SimpleNamespace(vikingdb_manager=DummyVikingDB()),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/demo.md",
        summary_dict={"name": "demo.md", "summary": "deployment summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(
            ["env=prod", "team=search"],
            mode="append",
        ),
    )

    assert queue.items[0].context_data["search_tags"] == ["env=prod", "team=search"]
    assert queue.items[0].context_data["_upsert_options"] == {"search_tag_mode": "append"}


@pytest.mark.asyncio
async def test_vectorize_directory_meta_writes_search_tags_into_embedding_context(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview="demo overview",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(["team=search", "env=test"]),
    )

    assert len(queue.items) == 2
    for msg in queue.items:
        assert msg.context_data["search_tags"] == ["team=search", "env=test"]


@pytest.mark.asyncio
async def test_vectorize_directory_meta_appends_search_tags_by_level(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview="demo overview",
        ctx=DummyReq(),
        ingest_options=IngestOptions.from_search_tags(
            ["env=prod", "team=search"],
            mode="append",
        ),
    )

    assert len(queue.items) == 2
    assert queue.items[0].context_data["level"] == 0
    assert queue.items[0].context_data["search_tags"] == ["env=prod", "team=search"]
    assert queue.items[0].context_data["_upsert_options"] == {"search_tag_mode": "append"}
    assert queue.items[1].context_data["level"] == 1
    assert queue.items[1].context_data["search_tags"] == ["env=prod", "team=search"]
    assert queue.items[1].context_data["_upsert_options"] == {"search_tag_mode": "append"}


@pytest.mark.asyncio
async def test_vectorize_directory_meta_l1_abstract_is_overview(monkeypatch):
    """L1 records must carry the overview in the abstract scalar so Rerank
    sees L1 text instead of the L0 abstract."""
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    overview = "# Overview\n\n" + ("section detail. " * 50)
    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview=overview,
        ctx=DummyReq(),
    )

    assert len(queue.items) == 2
    l0, l1 = queue.items
    assert l0.context_data["level"] == 0
    assert l0.context_data["abstract"] == "demo abstract"
    assert l1.context_data["level"] == 1
    assert l1.context_data["abstract"] == overview
    assert l1.message == overview


@pytest.mark.asyncio
async def test_vectorize_directory_meta_l1_abstract_truncated(monkeypatch):
    """Oversized overviews are capped below the scalar byte limit."""
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))

    oversized = "长" * 80_000
    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/demo",
        abstract="demo abstract",
        overview=oversized,
        ctx=DummyReq(),
    )

    l1 = queue.items[1]
    assert l1.context_data["level"] == 1
    assert len(l1.context_data["abstract"].encode("utf-8")) <= 50_000


@pytest.mark.asyncio
async def test_vectorize_unknown_text_content_only_reads_embedding_input(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("build:\n\tcargo build --locked\n".encode("gb18030"))
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
        ),
    )

    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/Makefile",
        summary_dict={"name": "Makefile", "summary": ""},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "build:\n\tcargo build --locked\n"
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 0
    assert fs.read_file_calls == 1


@pytest.mark.asyncio
async def test_vectorize_unknown_file_ignores_summary_content_without_reread(monkeypatch):
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
    assert "content" not in msg.context_data
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
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 0
    assert fs.read_file_calls == 0


@pytest.mark.asyncio
async def test_vectorize_text_summary_first_defers_full_content_read(monkeypatch):
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
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 0
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_image_file_enqueues_summary_and_image(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS(b"\x89PNG\r\n\x1a\nimage")
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
        file_path="viking://user/default/resources/photo.png",
        summary_dict={"name": "photo.png", "summary": "a cat on a sofa"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message[0] == {"type": "text", "text": "a cat on a sofa"}
    assert msg.message[1]["type"] == "image_url"
    assert msg.message[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "content" not in msg.context_data
    assert fs.read_file_bytes_calls == 1


@pytest.mark.asyncio
async def test_vectorize_svg_file_uses_summary_and_indexes_markup(monkeypatch):
    queue = DummyQueue()
    svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><text>queue flow</text></svg>'
    fs = DummyFS(svg_content)
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
        file_path="viking://user/default/resources/diagram.svg",
        summary_dict={"name": "diagram.svg", "summary": "queue processing diagram"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    msg = queue.items[0]
    assert msg.message == "queue processing diagram"
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 0
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_image_file_falls_back_to_summary_when_image_unreadable(monkeypatch):
    class UnreadableImageFS(DummyFS):
        async def read_file_bytes(self, _path, ctx=None):
            self.read_file_bytes_calls += 1
            raise OSError("cannot read")

    queue = DummyQueue()
    fs = UnreadableImageFS("")
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
        file_path="viking://user/default/resources/photo.png",
        summary_dict={"name": "photo.png", "summary": "fallback summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    assert queue.items[0].message == "fallback summary"
    assert fs.read_file_bytes_calls == 1


@pytest.mark.asyncio
async def test_vectorize_text_file_ignores_summary_content_without_reread(monkeypatch):
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
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 0
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_content_only_reads_embedding_input(monkeypatch):
    queue = DummyQueue()
    fs = DummyFS("README content".encode("gb18030"))
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=1000)
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
    assert msg.message == "README content"
    assert "content" not in msg.context_data
    assert fs.read_file_calls == 1
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_vectorize_file_bounds_content_before_enqueue(monkeypatch):
    queue = DummyQueue()
    content = " ".join(f"token-{i}" for i in range(200))
    fs = DummyFS(content)
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(text_source="content_only", max_input_tokens=20)
        ),
    )
    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/test.md",
        summary_dict={"name": "test.md", "summary": "short summary"},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    assert queue.items[0].message != content
    assert queue.items[0].message.endswith("...(truncated for embedding)")
    assert fs.read_file_calls == 1


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
    oversized = "你" * 30_000  # 90,000 UTF-8 bytes
    await embedding_utils.vectorize_file(
        file_path="viking://user/default/resources/big.md",
        summary_dict={"name": "big.md", "summary": oversized},
        parent_uri="viking://user/default/resources",
        ctx=DummyReq(),
    )

    assert len(queue.items) == 1
    abstract = queue.items[0].context_data["abstract"]
    assert len(abstract.encode("utf-8")) <= embedding_utils._ABSTRACT_MAX_BYTES
    assert abstract.encode("utf-8").decode("utf-8") == abstract  # valid UTF-8


@pytest.mark.asyncio
async def test_empty_media_uses_filename_but_unknown_binary_skips(monkeypatch):
    monkeypatch.setattr(
        embedding_utils,
        "get_openviking_config",
        lambda: types.SimpleNamespace(
            embedding=types.SimpleNamespace(
                text_source="content_only",
                max_input_tokens=1000,
            )
        ),
    )

    queue = DummyQueue()
    monkeypatch.setattr(
        embedding_utils,
        "get_queue_manager",
        lambda: DummyQueueManager(queue),
    )
    monkeypatch.setattr(
        embedding_utils,
        "get_viking_fs",
        lambda: DummyFS(b"media"),
    )
    await embedding_utils.vectorize_file(
        file_path="viking://resources/media/meeting.mp3",
        summary_dict={"name": "meeting.mp3", "summary": ""},
        parent_uri="viking://resources/media",
        ctx=DummyReq(),
    )

    assert queue.items[0].message == "meeting.mp3"
    assert "content" not in queue.items[0].context_data

    queue = DummyQueue()
    monkeypatch.setattr(
        embedding_utils,
        "get_queue_manager",
        lambda: DummyQueueManager(queue),
    )
    monkeypatch.setattr(
        embedding_utils,
        "get_viking_fs",
        lambda: DummyFS(b"binary"),
    )
    await embedding_utils.vectorize_file(
        file_path="viking://resources/media/archive.bin",
        summary_dict={"name": "archive.bin", "summary": ""},
        parent_uri="viking://resources/media",
        ctx=DummyReq(),
    )

    assert queue.items == []


@pytest.mark.asyncio
async def test_vectorize_directory_meta_truncates_oversized_abstract(monkeypatch):
    """The directory-meta path (fed by index_resource reading .abstract.md) must
    cap the abstract scalar on every enqueued Context (abstract + overview)."""
    queue = DummyQueue()
    monkeypatch.setattr(embedding_utils, "get_queue_manager", lambda: DummyQueueManager(queue))
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS("ignored"))
    oversized = "你" * 30_000  # 90,000 UTF-8 bytes
    await embedding_utils.vectorize_directory_meta(
        uri="viking://user/default/resources/dir",
        abstract=oversized,
        overview="overview text",
        ctx=DummyReq(),
    )

    assert queue.items  # at least the abstract-level Context was enqueued
    for item in queue.items:
        abstract = item.context_data["abstract"]
        assert len(abstract.encode("utf-8")) <= embedding_utils._ABSTRACT_MAX_BYTES
        assert abstract.encode("utf-8").decode("utf-8") == abstract
