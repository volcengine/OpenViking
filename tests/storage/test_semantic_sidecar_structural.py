# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.queuefs import semantic_sidecar


def test_peer_root_semantics_support_user_shorthand():
    semantics = semantic_sidecar.structural_directory_semantics("viking://user/peers/visitor-a")

    assert semantics is not None
    overview, abstract = semantics
    assert overview.startswith("# Peer visitor-a")
    assert "visitor-a" in abstract


def test_non_peer_directory_has_no_structural_override():
    assert (
        semantic_sidecar.structural_directory_semantics("viking://user/alice/memories/projects")
        is None
    )


@pytest.mark.asyncio
async def test_peer_root_writeback_replaces_child_derived_semantics(monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr(semantic_sidecar, "_write_sidecars", write)
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aenter__",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aexit__",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: MagicMock(),
    )
    viking_fs = SimpleNamespace(_uri_to_path=lambda uri, ctx=None: uri)

    wrote = await semantic_sidecar.write_semantic_sidecars(
        viking_fs=viking_fs,
        dir_uri="viking://user/alice/peers/customer-42",
        overview="# Tax records\n\nContains quarterly tax filing details.",
        abstract="Contains quarterly tax filing details.",
        ctx=None,
        is_stale=lambda: False,
    )

    assert wrote is True
    write.assert_awaited_once()
    _, dir_uri, overview, abstract, _, _ = write.await_args.args
    assert dir_uri == "viking://user/alice/peers/customer-42"
    assert overview.startswith("# Peer customer-42")
    assert "Tax records" not in overview
    assert abstract.startswith(
        "Private knowledge and memory scoped to interaction peer customer-42"
    )


@pytest.mark.asyncio
async def test_non_peer_writeback_keeps_generated_semantics(monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr(semantic_sidecar, "_write_sidecars", write)
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aenter__",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aexit__",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: MagicMock(),
    )
    viking_fs = SimpleNamespace(_uri_to_path=lambda uri, ctx=None: uri)

    await semantic_sidecar.write_semantic_sidecars(
        viking_fs=viking_fs,
        dir_uri="viking://user/alice/memories/projects",
        overview="# Projects\n\nProject summaries.",
        abstract="Project summaries.",
        ctx=None,
        is_stale=lambda: False,
    )

    _, _, overview, abstract, _, _ = write.await_args.args
    assert overview == "# Projects\n\nProject summaries."
    assert abstract == "Project summaries."
