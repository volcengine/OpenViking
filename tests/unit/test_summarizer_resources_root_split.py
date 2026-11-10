# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.summarizer import FileRefreshRequest, Summarizer
from openviking_cli.session.user_id import UserIdentifier


class _DummyQueue:
    def __init__(self):
        self.msgs = []

    async def enqueue(self, msg):
        self.msgs.append(msg)


class _FailingQueue:
    async def enqueue(self, msg):
        raise RuntimeError("queue unavailable")


class _DummyQueueManager:
    SEMANTIC = "semantic"

    def __init__(self, queue):
        self._queue = queue

    def get_queue(self, _name, allow_create=False):
        return self._queue


class _DummyVikingFS:
    def __init__(self, entries_by_uri):
        self.entries_by_uri = entries_by_uri

    async def ls(self, uri, show_all_hidden=False, ctx=None, **kwargs):
        return self.entries_by_uri.get(uri, [])


@pytest.mark.asyncio
async def test_resources_root_is_split_into_children():
    queue = _DummyQueue()
    qm = _DummyQueueManager(queue)
    vfs = _DummyVikingFS(
        {
            "viking://temp/import_root": [
                {"name": "existing_a", "isDir": True},
                {"name": "file.txt", "isDir": False},
            ]
        }
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with (
        patch("openviking.utils.summarizer.get_queue_manager", return_value=qm),
        patch(
            "openviking.utils.summarizer.get_current_telemetry",
            return_value=SimpleNamespace(telemetry_id="tid"),
        ),
        patch("openviking.utils.summarizer.get_viking_fs", return_value=vfs),
    ):
        summarizer = Summarizer(vlm_processor=None)
        res = await summarizer.summarize(
            resource_uris=["viking://resources/"],
            ctx=ctx,
            temp_uris=["viking://temp/import_root"],
        )

    assert res["status"] == "success"
    assert res["enqueued_count"] == 2
    assert [m.target_uri for m in queue.msgs] == [
        "viking://resources/existing_a",
        "viking://resources/file.txt",
    ]
    assert [m.uri for m in queue.msgs] == [
        "viking://temp/import_root/existing_a",
        "viking://temp/import_root/file.txt",
    ]


@pytest.mark.asyncio
async def test_explicit_subpath_not_split():
    queue = _DummyQueue()
    qm = _DummyQueueManager(queue)
    vfs = _DummyVikingFS({})
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with (
        patch("openviking.utils.summarizer.get_queue_manager", return_value=qm),
        patch(
            "openviking.utils.summarizer.get_current_telemetry",
            return_value=SimpleNamespace(telemetry_id="tid"),
        ),
        patch("openviking.utils.summarizer.get_viking_fs", return_value=vfs),
    ):
        summarizer = Summarizer(vlm_processor=None)
        res = await summarizer.summarize(
            resource_uris=["viking://resources/foo"],
            ctx=ctx,
            temp_uris=["viking://temp/import_root"],
            semantic_source={"kind": "http", "uri": "https://example.com/demo.pdf"},
            generation_trigger="resource_ingest",
        )

    assert res["status"] == "success"
    assert res["enqueued_count"] == 1
    assert queue.msgs[0].target_uri == "viking://resources/foo"
    assert queue.msgs[0].uri == "viking://temp/import_root"
    assert queue.msgs[0].source == {
        "kind": "http",
        "uri": "https://example.com/demo.pdf",
    }
    assert queue.msgs[0].generation_trigger == "resource_ingest"


@pytest.mark.asyncio
async def test_resources_root_empty_import_is_error():
    queue = _DummyQueue()
    qm = _DummyQueueManager(queue)
    vfs = _DummyVikingFS({"viking://temp/import_root": []})
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with (
        patch("openviking.utils.summarizer.get_queue_manager", return_value=qm),
        patch(
            "openviking.utils.summarizer.get_current_telemetry",
            return_value=SimpleNamespace(telemetry_id="tid"),
        ),
        patch("openviking.utils.summarizer.get_viking_fs", return_value=vfs),
    ):
        summarizer = Summarizer(vlm_processor=None)
        res = await summarizer.summarize(
            resource_uris=["viking://resources"],
            ctx=ctx,
            temp_uris=["viking://temp/import_root"],
        )

    assert res["status"] == "error"
    assert queue.msgs == []


@pytest.mark.asyncio
async def test_flat_file_refresh_enqueues_incremental_parent_summary():
    queue = _DummyQueue()
    qm = _DummyQueueManager(queue)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with (
        patch("openviking.utils.summarizer.get_queue_manager", return_value=qm),
        patch(
            "openviking.utils.summarizer.get_current_telemetry",
            return_value=SimpleNamespace(telemetry_id="tid"),
        ),
    ):
        result = await Summarizer(vlm_processor=None).refresh_file_parent(
            file_uri="viking://resources/神雕.md",
            ctx=ctx,
            skip_vectorization=False,
        )

    assert result == {
        "status": "success",
        "enqueued_count": 1,
        "semantic_action": "refresh_now",
    }
    assert len(queue.msgs) == 1
    msg = queue.msgs[0]
    assert msg.uri == "viking://resources"
    assert msg.recursive is False
    assert msg.changes == {"modified": ["viking://resources/神雕.md"]}
    assert msg.skip_vectorization is False
    assert msg.role == Role.ROOT
    assert msg.telemetry_id == "tid"


@pytest.mark.asyncio
async def test_grouped_file_refresh_coalesces_only_direct_parents():
    queue = _DummyQueue()
    qm = _DummyQueueManager(queue)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with (
        patch("openviking.utils.summarizer.get_queue_manager", return_value=qm),
        patch(
            "openviking.utils.summarizer.get_current_telemetry",
            return_value=SimpleNamespace(telemetry_id="tid"),
        ),
    ):
        result = await Summarizer(vlm_processor=None).refresh_file_parents(
            changes=[
                FileRefreshRequest(
                    "viking://resources/docs/a.md",
                    created=True,
                    md5="a",
                    vector_action="upsert",
                    ingest_options=IngestOptions(search_tags=["team=search"]),
                ),
                FileRefreshRequest("viking://resources/docs/b.md", md5="b"),
                FileRefreshRequest("viking://resources/src/main.py", md5="c"),
            ],
            ctx=ctx,
        )

    assert result["enqueued_count"] == 2
    assert result["parent_actions"] == {
        "viking://resources/docs": "refresh_now",
        "viking://resources/src": "refresh_now",
    }
    assert len(queue.msgs) == 2
    messages = {msg.uri: msg for msg in queue.msgs}
    assert messages["viking://resources/docs"].changes == {
        "added": ["viking://resources/docs/a.md"],
        "modified": ["viking://resources/docs/b.md"],
    }
    assert messages["viking://resources/docs"].file_md5s == {
        "viking://resources/docs/a.md": "a",
        "viking://resources/docs/b.md": "b",
    }
    assert messages["viking://resources/docs"].file_vector_actions == {
        "viking://resources/docs/a.md": "upsert"
    }
    assert messages["viking://resources/docs"].file_ingest_options == {
        "viking://resources/docs/a.md": IngestOptions(search_tags=["team=search"])
    }
    assert messages["viking://resources/src"].changes == {
        "modified": ["viking://resources/src/main.py"]
    }
    assert messages["viking://resources/src"].file_vector_actions == {}


@pytest.mark.asyncio
async def test_flat_file_refresh_propagates_enqueue_failure():
    qm = _DummyQueueManager(_FailingQueue())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with (
        patch("openviking.utils.summarizer.get_queue_manager", return_value=qm),
        patch(
            "openviking.utils.summarizer.get_current_telemetry",
            return_value=SimpleNamespace(telemetry_id="tid"),
        ),
    ):
        with pytest.raises(RuntimeError, match="queue unavailable"):
            await Summarizer(vlm_processor=None).refresh_file_parent(
                file_uri="viking://resources/神雕.md",
                ctx=ctx,
            )
