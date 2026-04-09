# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.utils.summarizer import Summarizer
from openviking_cli.session.user_id import UserIdentifier


class _DummyQueue:
    def __init__(self):
        self.msgs = []

    async def enqueue(self, msg):
        self.msgs.append(msg)


class _DummyQueueManager:
    SEMANTIC = "semantic"

    def __init__(self, queue):
        self._queue = queue

    def get_queue(self, _name, allow_create=False):
        return self._queue


class _DummyWaitTracker:
    def __init__(self):
        self.registered = []

    def register_semantic_root(self, telemetry_id, msg_id):
        self.registered.append((telemetry_id, msg_id))


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
                {"name": "new_c", "isDir": True},
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
        patch(
            "openviking.utils.summarizer.get_request_wait_tracker", return_value=_DummyWaitTracker()
        ),
        patch("openviking.utils.summarizer.get_viking_fs", return_value=vfs),
    ):
        summarizer = Summarizer(vlm_processor=None)
        res = await summarizer.summarize(
            resource_uris=["viking://resources"],
            ctx=ctx,
            temp_uris=["viking://temp/import_root"],
        )

    assert res["status"] == "success"
    assert res["enqueued_count"] == 2
    assert [m.target_uri for m in queue.msgs] == [
        "viking://resources/existing_a",
        "viking://resources/new_c",
    ]
    assert [m.uri for m in queue.msgs] == [
        "viking://temp/import_root/existing_a",
        "viking://temp/import_root/new_c",
    ]


@pytest.mark.asyncio
async def test_resources_root_single_file_child():
    queue = _DummyQueue()
    qm = _DummyQueueManager(queue)
    vfs = _DummyVikingFS({"viking://temp/import_root": [{"name": "file.txt", "isDir": False}]})
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with (
        patch("openviking.utils.summarizer.get_queue_manager", return_value=qm),
        patch(
            "openviking.utils.summarizer.get_current_telemetry",
            return_value=SimpleNamespace(telemetry_id="tid"),
        ),
        patch(
            "openviking.utils.summarizer.get_request_wait_tracker", return_value=_DummyWaitTracker()
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
    assert res["enqueued_count"] == 1
    assert queue.msgs[0].target_uri == "viking://resources/file.txt"
    assert queue.msgs[0].uri == "viking://temp/import_root/file.txt"


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
        patch(
            "openviking.utils.summarizer.get_request_wait_tracker", return_value=_DummyWaitTracker()
        ),
        patch("openviking.utils.summarizer.get_viking_fs", return_value=vfs),
    ):
        summarizer = Summarizer(vlm_processor=None)
        res = await summarizer.summarize(
            resource_uris=["viking://resources/foo"],
            ctx=ctx,
            temp_uris=["viking://temp/import_root"],
        )

    assert res["status"] == "success"
    assert res["enqueued_count"] == 1
    assert queue.msgs[0].target_uri == "viking://resources/foo"
    assert queue.msgs[0].uri == "viking://temp/import_root"


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
        patch(
            "openviking.utils.summarizer.get_request_wait_tracker", return_value=_DummyWaitTracker()
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
