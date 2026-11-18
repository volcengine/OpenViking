# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from unittest.mock import Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import bind_task_context, get_task_context
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize(
    ("mode", "configured_provider", "expected"),
    [("logical_path", "filesystem", "tree"), ("lock_path", "postgres", None)],
)
def test_missing_snapshot_target_uses_runtime_handle_mode(
    monkeypatch, mode, configured_provider, expected
):
    config = Mock()
    config.storage.agfs.pathlock.provider = configured_provider
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config", lambda: config
    )
    client = Mock()
    client.pathlock_handle_mode.return_value = mode
    assert VikingFS(agfs=client)._snapshot_missing_target_lock_kind() == expected
    client.pathlock_handle_mode.assert_called_once_with()


def test_missing_snapshot_target_rejects_unknown_handle_mode():
    client = Mock()
    client.pathlock_handle_mode.return_value = "unknown"
    with pytest.raises(ValueError, match="Unknown PathLock handle mode"):
        VikingFS(agfs=client)._snapshot_missing_target_lock_kind()


def test_missing_snapshot_target_propagates_capability_query_failure():
    client = Mock()
    client.pathlock_handle_mode.side_effect = RuntimeError("binding failure")
    with pytest.raises(RuntimeError, match="binding failure"):
        VikingFS(agfs=client)._snapshot_missing_target_lock_kind()


@pytest.mark.asyncio
async def test_fallback_rebuild_detaches_all_operations(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.ROOT)
    vfs = VikingFS(agfs=Mock())
    seen = []
    finished = asyncio.Event()

    async def record(op, uri, level, request_ctx):
        seen.append((op, get_task_context(), request_ctx))
        if len(seen) == 3:
            finished.set()

    class Executor:
        async def reindex_directory_marker(self, *, dir_uri, level, ctx):
            await record("marker", dir_uri, level, ctx)

        async def execute(self, *, uri, mode, wait, ctx):
            assert mode == "vectors_only" and wait
            await record("file", uri, 2, ctx)

        async def delete_uri_level(self, *, uri, level, ctx):
            await record("delete", uri, level, ctx)

    monkeypatch.setattr(
        "openviking.service.reindex_executor.ReindexExecutor", lambda **kwargs: Executor()
    )
    with bind_task_context("restore-caller", "account", "user"):
        owner = get_task_context()
        vfs._schedule_vector_rebuild(
            written=["resources/proj/.abstract.md", "resources/proj/file.md"],
            deleted=["resources/proj/deleted.md"],
            ctx=ctx,
        )
        assert get_task_context() is owner
    await asyncio.wait_for(finished.wait(), timeout=1)
    assert {op for op, _, _ in seen} == {"marker", "file", "delete"}
    assert all(context is None and request_ctx is ctx for _, context, request_ctx in seen)
