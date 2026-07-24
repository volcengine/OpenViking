# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.parsers.media import utils as media_utils
from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_dag import DirNode, SemanticDagExecutor
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.utils import embedding_utils
from openviking_cli.session.user_id import UserIdentifier


class PipelineFS:
    def __init__(self, files, *, tree=None):
        self.files = files
        self.tree = tree or {}
        self.byte_reads = 0

    async def stat(self, path, ctx=None):
        content = self.files.get(path, b"")
        return {"size": len(content)}

    async def read_file_bytes(self, path, ctx=None):
        self.byte_reads += 1
        content = self.files.get(path, b"")
        return content if isinstance(content, bytes) else str(content).encode()

    async def read(self, path, offset=0, size=-1, ctx=None):
        self.byte_reads += 1
        content = self.files.get(path, b"")
        raw = content if isinstance(content, bytes) else str(content).encode()
        return raw[offset:] if size == -1 else raw[offset : offset + size]

    async def read_file(self, path, ctx=None):
        content = self.files.get(path, "")
        return content.decode() if isinstance(content, bytes) else content

    async def ls(self, uri, node_limit=None, ctx=None):
        return self.tree.get(uri, [])


class RecordingMediaClient:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def understand_from_writer(self, *, content_writer, filename, **_kwargs):
        self.calls += 1
        with tempfile.TemporaryDirectory() as temp_dir:
            await content_writer(Path(temp_dir) / filename)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result


class RecordingVLM:
    def __init__(self, result="", *, available=True):
        self.result = result
        self.available = available
        self.calls = 0

    def is_available(self):
        return self.available

    async def get_completion_async(self, _prompt):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class DummyQueue:
    def __init__(self):
        self.items = []

    async def enqueue(self, item):
        self.items.append(item)


class DummyQueueManager:
    EMBEDDING = "embedding"

    def __init__(self, queue):
        self.queue = queue

    def get_queue(self, _name):
        return self.queue


def _config(media_client, vlm, *, configure_audio=True):
    audio = None
    if configure_audio:
        audio = SimpleNamespace(get_client_instance=lambda: media_client)
    return SimpleNamespace(
        media_understanding=SimpleNamespace(audio=audio, video=None),
        vlm=vlm,
        semantic=SimpleNamespace(
            overview_max_chars=4000,
            abstract_max_chars=256,
            max_overview_prompt_chars=60000,
            overview_batch_size=50,
        ),
        embedding=SimpleNamespace(text_source="content_only", max_input_tokens=1000),
        output_language_override="en",
    )


def _patch_dependencies(monkeypatch, config, fs):
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: config)
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(semantic_processor_module, "get_openviking_config", lambda: config)
    monkeypatch.setattr(embedding_utils, "get_openviking_config", lambda: config)
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        semantic_processor_module,
        "render_prompt",
        lambda _name, _values: "directory overview prompt",
    )


def _context():
    return RequestContext(user=UserIdentifier("acc", "user"), role=Role.USER)


def _single_media_node(result, filename):
    uri = "viking://resources/media"
    file_uri = f"{uri}/{filename}"
    return DirNode(
        uri=uri,
        children_dirs=[],
        file_paths=[file_uri],
        file_index={file_uri: 0},
        child_index={},
        file_summaries=[result],
        children_abstracts=[],
        pending=0,
    )


def _executor(processor, fs, **kwargs):
    with patch(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        return_value=fs,
    ):
        return SemanticDagExecutor(processor, "resource", 2, _context(), **kwargs)


async def _run_overview(processor, fs, result, filename):
    executor = _executor(processor, fs)
    node = _single_media_node(result, filename)
    executor._nodes[node.uri] = node
    executor._parent[node.uri] = None
    executor._root_done = asyncio.Event()
    executor._write_directory_semantics = AsyncMock(return_value=True)
    executor._add_vectorize_task = AsyncMock()

    await executor._overview_task(node.uri)

    overview, abstract = executor._write_directory_semantics.await_args.args[1:]
    vector_task = executor._add_vectorize_task.await_args.args[0]
    return overview, abstract, vector_task


async def _vectorize_leaf(monkeypatch, config, fs, filename, result):
    queue = DummyQueue()
    monkeypatch.setattr(
        embedding_utils,
        "get_queue_manager",
        lambda: DummyQueueManager(queue),
    )
    await embedding_utils.vectorize_file(
        file_path=f"viking://resources/media/{filename}",
        summary_dict=result,
        parent_uri="viking://resources/media",
        ctx=_context(),
    )
    assert len(queue.items) == 1
    return queue.items[0]


@pytest.mark.asyncio
async def test_success_flows_through_overview_write_and_leaf_embedding(monkeypatch):
    raw = "# Meeting\n\nQuarterly planning discussion.\n\n### meeting.mp3\n\nRevenue grew."
    media_client = RecordingMediaClient(raw)
    vlm = RecordingVLM("must not be called")
    config = _config(media_client, vlm)
    uri = "viking://resources/media/meeting.mp3"
    fs = PipelineFS({uri: b"audio"})
    _patch_dependencies(monkeypatch, config, fs)

    result = await media_utils.generate_audio_summary(uri, "meeting.mp3")
    processor = SemanticProcessor()
    overview, abstract, vector_task = await _run_overview(
        processor, fs, result, "meeting.mp3"
    )
    leaf_message = await _vectorize_leaf(
        monkeypatch, config, fs, "meeting.mp3", result
    )

    assert set(result) == {"name", "summary"}
    assert media_client.calls == 1
    assert vlm.calls == 0
    assert overview == result["summary"]
    assert abstract == "Quarterly planning discussion."
    assert vector_task.overview == result["summary"]
    assert vector_task.abstract == abstract
    assert leaf_message.message == result["summary"]
    assert leaf_message.context_data["abstract"] == result["summary"]
    assert leaf_message.context_data["content"] == result["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "filename", "configure_audio", "provider_result", "media_calls"),
    [
        ("missing-config", "meeting.mp3", False, "unused", 0),
        ("unsupported-format", "meeting.flac", True, "unused", 0),
        ("provider-failure", "meeting.mp3", True, RuntimeError("provider failed"), 1),
    ],
)
async def test_empty_media_summary_uses_generic_directory_and_filename_leaf(
    monkeypatch,
    scenario,
    filename,
    configure_audio,
    provider_result,
    media_calls,
):
    del scenario
    media_client = RecordingMediaClient(provider_result)
    generic = (
        f"# media\n\nmedia contains {filename}.\n\n"
        f"### {filename}\n\n{filename}"
    )
    vlm = RecordingVLM(generic)
    config = _config(media_client, vlm, configure_audio=configure_audio)
    uri = f"viking://resources/media/{filename}"
    fs = PipelineFS({uri: b"audio"})
    _patch_dependencies(monkeypatch, config, fs)

    result = await media_utils.generate_audio_summary(uri, filename)
    overview, abstract, vector_task = await _run_overview(
        SemanticProcessor(), fs, result, filename
    )
    leaf_message = await _vectorize_leaf(monkeypatch, config, fs, filename, result)

    assert result == {"name": filename, "summary": ""}
    assert media_client.calls == media_calls
    assert vlm.calls == 1
    assert filename in overview
    assert filename in abstract
    assert vector_task.overview == overview
    assert vector_task.abstract == abstract
    assert leaf_message.message == filename
    assert leaf_message.context_data["abstract"] == filename
    assert leaf_message.context_data["content"] == filename


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vlm_result", "available", "vlm_calls", "expected_overview"),
    [
        (
            "unused",
            False,
            0,
            "# media\n\n[Directory overview is not ready]",
        ),
        (
            RuntimeError("VLM failed"),
            True,
            1,
            "# media\n\n[Directory overview is not generated]",
        ),
    ],
    ids=["vlm-unavailable", "vlm-failure"],
)
async def test_empty_media_summary_preserves_legacy_directory_fallback(
    monkeypatch, vlm_result, available, vlm_calls, expected_overview
):
    filename = "meeting.mp3"
    media_client = RecordingMediaClient("unused")
    vlm = RecordingVLM(vlm_result, available=available)
    config = _config(media_client, vlm, configure_audio=False)
    uri = f"viking://resources/media/{filename}"
    fs = PipelineFS({uri: b"audio"})
    _patch_dependencies(monkeypatch, config, fs)
    result = await media_utils.generate_audio_summary(uri, filename)

    overview, abstract, vector_task = await _run_overview(
        SemanticProcessor(), fs, result, filename
    )
    leaf_message = await _vectorize_leaf(monkeypatch, config, fs, filename, result)

    assert vlm.calls == vlm_calls
    assert overview == expected_overview
    assert abstract == expected_overview.split("\n\n", 1)[1]
    assert vector_task.overview == overview
    assert vector_task.abstract == abstract
    assert leaf_message.message == filename
    assert leaf_message.context_data["abstract"] == filename
    assert leaf_message.context_data["content"] == filename


@pytest.mark.asyncio
async def test_incremental_mixed_directory_reuses_singleton_media_summary(monkeypatch):
    root_uri = "viking://resources/media"
    media_uri = f"{root_uri}/meeting.mp3"
    added_uri = f"{root_uri}/notes.txt"
    existing_overview = (
        "# Meeting\n\nQuarterly planning discussion.\n\n"
        "### meeting.mp3\n\nQuarterly planning discussion."
    )
    fs = PipelineFS(
        files={
            media_uri: b"audio",
            added_uri: "New release notes.",
            f"{root_uri}/.overview.md": existing_overview,
        },
        tree={
            root_uri: [
                {"name": "meeting.mp3", "isDir": False},
                {"name": "notes.txt", "isDir": False},
            ]
        },
    )
    media_client = RecordingMediaClient("must not be called")
    vlm = RecordingVLM(
        "# media\n\nmedia contains meeting.mp3 and notes.txt.\n\n"
        "### meeting.mp3\n\nQuarterly planning discussion.\n\n"
        "### notes.txt\n\nNew release notes."
    )
    config = _config(media_client, vlm)
    _patch_dependencies(monkeypatch, config, fs)
    processor = SemanticProcessor()
    processor._generate_text_summary = AsyncMock(
        return_value={"name": "notes.txt", "summary": "New release notes."}
    )
    processor._generate_overview = AsyncMock(wraps=processor._generate_overview)
    executor = _executor(
        processor,
        fs,
        incremental_update=True,
        target_uri=root_uri,
        changes={"added": [added_uri]},
    )
    executor._add_vectorize_task = AsyncMock()
    executor._write_directory_semantics = AsyncMock(return_value=True)

    await executor.run(root_uri)

    assert media_client.calls == 0
    assert vlm.calls == 1
    processor._generate_overview.assert_awaited_once()
    file_summaries = processor._generate_overview.await_args.args[1]
    assert file_summaries == [
        {"name": "meeting.mp3", "summary": "Quarterly planning discussion."},
        {"name": "notes.txt", "summary": "New release notes."},
    ]
    overview, abstract = executor._write_directory_semantics.await_args.args[1:]
    assert "meeting.mp3" in overview
    assert "notes.txt" in overview
    assert "meeting.mp3" in abstract
    assert "notes.txt" in abstract
