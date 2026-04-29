# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Security regression tests for ovpack import target-policy enforcement."""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openviking.resource.watch_storage import WATCH_TASK_STORAGE_URI
from openviking.server.identity import RequestContext, Role
from openviking.storage.local_fs import import_ovpack
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    def __init__(self) -> None:
        self.written_files: list[str] = []
        self.written_payloads: dict[str, bytes] = {}
        self.created_dirs: list[str] = []

    async def stat(self, uri: str, ctx=None):
        return {"uri": uri, "isDir": True}

    async def mkdir(self, uri: str, exist_ok: bool = False, ctx=None):
        self.created_dirs.append(uri)

    async def ls(self, uri: str, ctx=None):
        raise NotFoundError(uri, "file")

    async def write_file_bytes(self, uri: str, data: bytes, ctx=None):
        self.written_files.append(uri)
        self.written_payloads[uri] = data


class FakeSemanticQueue:
    def __init__(self) -> None:
        self.msgs = []

    async def enqueue(self, msg):
        self.msgs.append(msg)
        return getattr(msg, "id", "queued")


class FakeQueueManager:
    SEMANTIC = "semantic"

    def __init__(self) -> None:
        self.queue = FakeSemanticQueue()

    def get_queue(self, name: str, allow_create: bool = False):
        assert name == self.SEMANTIC
        return self.queue


@pytest.fixture
def request_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice", "agent1"), role=Role.USER)


@pytest.fixture
def temp_ovpack_path() -> Path:
    fd, path = tempfile.mkstemp(suffix=".ovpack")
    os.close(fd)
    ovpack_path = Path(path)
    try:
        yield ovpack_path
    finally:
        ovpack_path.unlink(missing_ok=True)


def _write_ovpack(path: Path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


@pytest.mark.asyncio
async def test_import_ovpack_skips_derived_semantic_files_and_enqueues_refresh(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            "demo/_._meta.json": json.dumps({"uri": "viking://resources/demo"}),
            "demo/_._abstract.md": "ATTACKER_ABSTRACT",
            "demo/_._overview.md": "ATTACKER_OVERVIEW",
            "demo/_._relations.json": '{"links":["bad"]}',
            "demo/notes.txt": "hello",
        },
    )
    fake_fs = FakeVikingFS()
    queue_manager = FakeQueueManager()

    with (
        patch("openviking.storage.local_fs.get_queue_manager", return_value=queue_manager),
        patch("openviking.storage.local_fs.logger.warning") as mock_warning,
    ):
        root_uri = await import_ovpack(
            fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx, vectorize=False
        )

    assert root_uri == "viking://resources/demo"
    assert fake_fs.written_files == [
        "viking://resources/demo/.meta.json",
        "viking://resources/demo/notes.txt",
    ]
    assert "viking://resources/demo/.abstract.md" not in fake_fs.written_payloads
    assert "viking://resources/demo/.overview.md" not in fake_fs.written_payloads
    assert "viking://resources/demo/.relations.json" not in fake_fs.written_payloads
    assert len(queue_manager.queue.msgs) == 1
    assert queue_manager.queue.msgs[0].uri == root_uri
    assert queue_manager.queue.msgs[0].context_type == "resource"
    mock_warning.assert_called_once()
    warning_args = mock_warning.call_args[0]
    assert "Skipped %d derived semantic files during ovpack import to %s" in warning_args[0]
    assert warning_args[1] == 3
    assert warning_args[2] == root_uri
    assert "wait_processed()" in warning_args[0]


@pytest.mark.asyncio
async def test_import_ovpack_enqueues_semantic_refresh_for_memory_targets(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            "demo/_._meta.json": json.dumps({"uri": "viking://user/default/memories/demo"}),
            "demo/memory.md": "remember this fact",
        },
    )
    fake_fs = FakeVikingFS()
    queue_manager = FakeQueueManager()

    with patch("openviking.storage.local_fs.get_queue_manager", return_value=queue_manager):
        root_uri = await import_ovpack(
            fake_fs,
            str(temp_ovpack_path),
            "viking://user/default/memories",
            request_ctx,
            vectorize=False,
        )

    assert root_uri == "viking://user/default/memories/demo"
    assert len(queue_manager.queue.msgs) == 1
    assert queue_manager.queue.msgs[0].uri == root_uri
    assert queue_manager.queue.msgs[0].context_type == "memory"


@pytest.mark.asyncio
async def test_import_ovpack_does_not_enqueue_when_only_metadata_and_skipped_files_exist(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            "demo/_._meta.json": json.dumps({"uri": "viking://resources/demo"}),
            "demo/_._abstract.md": "ATTACKER_ABSTRACT",
            "demo/_._overview.md": "ATTACKER_OVERVIEW",
        },
    )
    fake_fs = FakeVikingFS()
    queue_manager = FakeQueueManager()

    with patch("openviking.storage.local_fs.get_queue_manager", return_value=queue_manager):
        root_uri = await import_ovpack(
            fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx, vectorize=False
        )

    assert root_uri == "viking://resources/demo"
    assert fake_fs.written_files == ["viking://resources/demo/.meta.json"]
    assert queue_manager.queue.msgs == []


@pytest.mark.asyncio
async def test_import_ovpack_rejects_watch_task_control_files(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            ".watch_tasks.json/_._meta.json": json.dumps({"uri": WATCH_TASK_STORAGE_URI}),
            ".watch_tasks.json/state.json": '{"task":"forged"}',
        },
    )
    fake_fs = FakeVikingFS()

    with pytest.raises(
        InvalidArgumentError,
        match=r"cannot import watch task control file: viking://resources/\.watch_tasks\.json",
    ):
        await import_ovpack(
            fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx, vectorize=False
        )


@pytest.mark.asyncio
async def test_import_ovpack_rejects_session_scope_targets(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            "victim/_._meta.json": json.dumps({"session_id": "victim"}),
            "victim/messages.jsonl": '{"id":"msg_attacker","role":"user","parts":[{"type":"text","text":"forged"}],"created_at":"2026-01-01T00:00:00Z"}\n',
        },
    )
    fake_fs = FakeVikingFS()

    with pytest.raises(
        InvalidArgumentError,
        match=r"ovpack import is not supported for scope: session",
    ):
        await import_ovpack(
            fake_fs, str(temp_ovpack_path), "viking://session/default", request_ctx, vectorize=False
        )

    assert fake_fs.written_files == []
