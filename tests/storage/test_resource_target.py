# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the AGFS resource target used by content tree actions."""

from unittest.mock import AsyncMock

import pytest

from openviking.observability.events import get_event_bus
from openviking.observability.reporting import runtime
from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import bind_task_context
from openviking.storage.resource_target import AgfsResourceTarget
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


class _Ctx:
    account_id = "acct"


class _FakeVikingFS:
    def __init__(self, existing=None):
        self.files = dict(existing or {})
        self.written = []
        self.removed = []

    async def write_file_bytes(self, uri, content, *, ctx=None, lease_ref=None):
        self.files[uri] = content
        self.written.append(uri)

    async def read_file_bytes(self, uri, *, ctx=None):
        return self.files[uri]

    async def rm(self, uri, *, recursive=False, ctx=None, lease_ref=None):
        self.removed.append(uri)
        self.files.pop(uri, None)

    async def remove_files(self, uri, *, recursive=False, ctx=None, lease_ref=None):
        self.removed.append((uri, recursive, lease_ref))
        self.files.pop(uri, None)


_ROOT = "viking://resources/proj"


def _target(vfs):
    return AgfsResourceTarget(viking_fs=vfs, root_uri=_ROOT, ctx=_Ctx())


@pytest.mark.asyncio
class TestAgfsResourceTarget:
    async def test_write_joins_rel_path_under_root(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs)

        await target.write_file("sub/a.py", b"print(1)")

        assert vfs.files[f"{_ROOT}/sub/a.py"] == b"print(1)"

    async def test_empty_rel_path_targets_root_file(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs)

        await target.write_file("", b"body")

        assert vfs.files[_ROOT] == b"body"

    async def test_write_preserves_artifact_bytes(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs)

        artifact_bytes = "你好".encode("gbk")
        await target.write_file("a.py", artifact_bytes)

        assert vfs.files[f"{_ROOT}/a.py"] == artifact_bytes

    async def test_write_rejects_path_escape(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs)

        with pytest.raises(ValueError):
            await target.write_file("../evil.py", b"x")

    async def test_delete_reports_only_successful_storage_changes(self, tmp_path, monkeypatch):
        backend = MockLocalAGFS(root_path=tmp_path)
        vfs = VikingFS(agfs=backend)
        ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
        target = AgfsResourceTarget(viking_fs=vfs, root_uri=_ROOT, ctx=ctx)
        await target.write_file("gone.py", b"removed")
        await target.write_file("keep.py", b"retained")
        monkeypatch.setattr(runtime, "_reporter", object())
        events = []
        bus = get_event_bus()
        bus.register("resource-delete-reporting", events.append)
        try:
            with bind_task_context("reimport", "acct", "alice", task_type="add_resource"):
                await target.delete_path("gone.py", is_dir=False)
            assert not backend.exists(vfs._uri_to_path(f"{_ROOT}/gone.py", ctx=ctx))
            assert await target.read_file("keep.py") == b"retained"
            assert len(events) == 1
            assert events[0].payload["uri"] == f"{_ROOT}/gone.py"
            assert events[0].payload["change_type"] == "deleted"
            assert events[0].payload["task_id"] == "reimport"
            monkeypatch.setattr(vfs, "remove_files", AsyncMock(side_effect=OSError("unavailable")))
            with pytest.raises(OSError):
                await target.delete_path("keep.py", is_dir=False)
            assert await target.read_file("keep.py") == b"retained"
            assert len(events) == 1
        finally:
            bus.unregister("resource-delete-reporting")

    async def test_delete_path_uses_tree_semantics_for_directory(self) -> None:
        lease = {"lease_ref": "root-tree"}
        vfs = _FakeVikingFS()
        target = AgfsResourceTarget(
            viking_fs=vfs,
            root_uri=_ROOT,
            ctx=_Ctx(),
            lease_ref=lease,
        )

        await target.delete_path("old-dir", is_dir=True)

        assert vfs.removed == [(f"{_ROOT}/old-dir", True, lease)]

    async def test_read_file_returns_existing_bytes(self) -> None:
        vfs = _FakeVikingFS(existing={f"{_ROOT}/a.py": b"body"})
        target = _target(vfs)

        assert await target.read_file("a.py") == b"body"


@pytest.mark.asyncio
@pytest.mark.parametrize("is_dir", [False, True])
async def test_incremental_resource_delete_reports_committed_storage_change(
    tmp_path, monkeypatch, is_dir
):
    from openviking.observability.events import (
        register_event_subscriber,
        unregister_event_subscriber,
    )
    from openviking.observability.reporting import runtime
    from openviking.server.identity import RequestContext, Role
    from openviking.service.task_work_index import bind_task_context
    from openviking_cli.session.user_id import UserIdentifier

    monkeypatch.setattr(runtime, "_reporter", object())
    backend = MockLocalAGFS(root_path=tmp_path)
    fs = VikingFS(agfs=backend)
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
    rel_path = "removed" if is_dir else "a.md"
    uri = f"{_ROOT}/{rel_path}"
    path = fs._uri_to_path(uri, ctx=ctx)
    backend.write(path + "/a.md" if is_dir else path, b"old")
    backend.write(fs._uri_to_path(f"{_ROOT}/b.md", ctx=ctx), b"retained")
    events = []
    register_event_subscriber("resource_diff_delete", events.append)
    try:
        with bind_task_context("reimport", "acct", "alice", task_type="add_resource"):
            await AgfsResourceTarget(viking_fs=fs, root_uri=_ROOT, ctx=ctx).delete_path(
                rel_path, is_dir=is_dir
            )
        assert not backend.exists(path)
        assert backend.exists(fs._uri_to_path(f"{_ROOT}/b.md", ctx=ctx))
        assert len(events) == 1
        event = events[0]
        assert event.event_name == "file_mutation"
        assert event.payload["uri"] == uri
        assert event.payload["change_type"] == "deleted"
        assert event.payload["is_dir"] is is_dir
        assert event.payload["task_id"] == "reimport"
        assert (event.account_id, event.user_id) == ("acct", "alice")
    finally:
        unregister_event_subscriber("resource_diff_delete")


@pytest.mark.asyncio
async def test_failed_incremental_delete_does_not_report_success(monkeypatch):
    from unittest.mock import AsyncMock

    from openviking.observability.events import (
        register_event_subscriber,
        unregister_event_subscriber,
    )
    from openviking.observability.reporting import runtime

    monkeypatch.setattr(runtime, "_reporter", object())
    fs = _FakeVikingFS()
    fs.remove_files = AsyncMock(side_effect=RuntimeError("storage delete failed"))
    events = []
    register_event_subscriber("failed_resource_delete", events.append)
    try:
        with pytest.raises(RuntimeError, match="storage delete failed"):
            await _target(fs).delete_path("a.md", is_dir=False)
        assert not events
    finally:
        unregister_event_subscriber("failed_resource_delete")
