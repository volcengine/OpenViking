# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for SemanticLockScope recovery from expired handoffs.

When a semantic message's original ``LockHandle`` expires (e.g. the request
context that issued the resource ingest returned before the worker dequeued),
``OwnedLockLease.from_handoff`` raises ``LockAcquisitionError``. The fallback
path in ``SemanticLockScope.resolve`` re-acquires locks for the paths recorded
in ``LockHandoffRef.lock_paths``.

Historically that fallback only recognised TREE-lock entries (suffix
``/.path.ovlock``). Messages whose original locks were EXACT-path locks
(``acquire_exact_path``) had no recoverable paths, so the fallback re-raised
and ``semantic_processor`` infinitely re-enqueued the same message — a
production-visible dead-loop for callers that ingest individual files instead
of whole directories.

These tests pin the recovery behaviour for both lock styles so the regression
can't return.
"""

from __future__ import annotations

import pytest

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs.semantic_lock import (
    SemanticLockScope,
    _recoverable_tree_paths,
)
from openviking.storage.transaction import LockHandoffRef


# ---------------------------------------------------------------------------
# _recoverable_tree_paths
# ---------------------------------------------------------------------------


def test_recoverable_tree_paths_preserves_tree_lock_entries():
    """Tree-lock entries are unchanged: suffix stripped to get the directory."""
    paths = _recoverable_tree_paths(
        [
            "/a/dir/.path.ovlock",
            "/another/dir/.path.ovlock",
        ]
    )
    assert paths == ["/a/dir", "/another/dir"]


def test_recoverable_tree_paths_widens_exact_lock_to_parent_dir():
    """Exact-path locks recover via the parent directory as a tree lock.

    This is the regression fix: before, exact-path lock entries were silently
    dropped, leaving the worker no recoverable paths.
    """
    paths = _recoverable_tree_paths(
        [
            "/tenant/products/sku1/.exact.ovlock..abstract.md.abc123",
            "/tenant/products/sku1/.exact.ovlock..detail.md.def456",
        ]
    )
    # Both files share the same parent directory; we dedupe.
    assert paths == ["/tenant/products/sku1"]


def test_recoverable_tree_paths_handles_mixed_tree_and_exact_locks():
    paths = _recoverable_tree_paths(
        [
            "/tenant/products/sku1/.path.ovlock",
            "/tenant/products/sku2/.exact.ovlock.meta.json.xyz",
        ]
    )
    assert paths == ["/tenant/products/sku1", "/tenant/products/sku2"]


def test_recoverable_tree_paths_dedupes_and_preserves_order():
    paths = _recoverable_tree_paths(
        [
            "/dir-a/.path.ovlock",
            "/dir-b/.exact.ovlock.x.1",
            "/dir-a/.exact.ovlock.y.2",  # duplicates /dir-a from first entry
            "/dir-b/.path.ovlock",  # duplicates /dir-b from second entry
        ]
    )
    assert paths == ["/dir-a", "/dir-b"]


def test_recoverable_tree_paths_skips_unrecognized_entries():
    """Anything that's neither a tree-lock suffix nor a recognizable exact-lock
    name is skipped — we must not invent locks out of arbitrary strings."""
    paths = _recoverable_tree_paths(
        [
            "",
            "/just/a/file/path",  # no lock-file marker at all
            "/dir/.path.ovlock",
            "/dir2/.exact.ovlock.real.lock",
        ]
    )
    assert paths == ["/dir", "/dir2"]


def test_recoverable_tree_paths_refuses_to_collapse_to_root():
    """An exact-lock entry whose file lives at the root (no parent dir) must
    NOT be widened to ``/``: that would re-lock the whole namespace."""
    paths = _recoverable_tree_paths(["/.exact.ovlock.toplevel.123"])
    assert paths == []


def test_recoverable_tree_paths_returns_empty_for_empty_input():
    assert _recoverable_tree_paths([]) == []


def test_recoverable_tree_paths_legacy_alias_still_works():
    """Some callers may have imported the original ``_tree_paths_from_handoff``
    name. Keep it as an alias to avoid breakage."""
    from openviking.storage.queuefs.semantic_lock import _tree_paths_from_handoff

    assert _tree_paths_from_handoff == _recoverable_tree_paths


# ---------------------------------------------------------------------------
# SemanticLockScope.resolve recovery integration
# ---------------------------------------------------------------------------


class _FakeHandle:
    def __init__(self, handle_id: str, locks=()):
        self.id = handle_id
        self.locks = list(locks)


class _FakeLockManager:
    """Minimal lock manager: ``from_handoff`` always fails so we exercise the
    recovery path. ``acquire_tree`` records what the recovery tried."""

    def __init__(self):
        self.created_handles: list[_FakeHandle] = []
        self.acquire_tree_calls: list[str] = []
        self.acquire_tree_batch_calls: list[list[str]] = []
        self.released: list[str] = []

    def create_handle(self):
        handle = _FakeHandle(f"recovered-{len(self.created_handles)}")
        self.created_handles.append(handle)
        return handle

    async def acquire_tree(self, handle, path, timeout=None):
        del timeout
        self.acquire_tree_calls.append(path)
        handle.locks.append(f"{path}/.path.ovlock")
        return True

    async def acquire_tree_batch(self, handle, paths, timeout=None):
        del timeout
        self.acquire_tree_batch_calls.append(list(paths))
        for p in paths:
            handle.locks.append(f"{p}/.path.ovlock")
        return True

    async def release(self, handle):
        self.released.append(handle.id)


async def _failing_from_handoff(*args, **kwargs):  # noqa: D401 - test stub
    del args, kwargs
    raise LockAcquisitionError("Lock handle is no longer active: stub")


@pytest.mark.asyncio
async def test_resolve_recovers_from_exact_path_lock_handoff(monkeypatch):
    """End-to-end: a handoff containing ONLY exact-path locks recovers by
    re-acquiring the parent directory as a tree lock. Pre-fix this raised
    LockAcquisitionError and the worker dead-looped."""
    manager = _FakeLockManager()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.get_lock_manager",
        lambda: manager,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.OwnedLockLease.from_handoff",
        _failing_from_handoff,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.OwnedLockLease.from_handle",
        lambda mgr, handle: object(),  # opaque sentinel — we only check the manager calls
    )

    handoff = LockHandoffRef(
        handle_id="dead-handle",
        lock_paths=(
            "/tenant/products/sku1/.exact.ovlock..abstract.md.abc",
            "/tenant/products/sku1/.exact.ovlock..detail.md.def",
        ),
    )
    scope = await SemanticLockScope.resolve(handoff)

    assert scope is not None
    # Both exact locks collapse to the same parent dir → single acquire_tree call.
    assert manager.acquire_tree_calls == ["/tenant/products/sku1"]
    assert manager.acquire_tree_batch_calls == []
    assert manager.released == []


@pytest.mark.asyncio
async def test_resolve_still_raises_when_no_recoverable_paths(monkeypatch):
    """If the handoff carries no recoverable paths, the original failure must
    surface — we don't swallow LockAcquisitionError silently."""
    manager = _FakeLockManager()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.get_lock_manager",
        lambda: manager,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.OwnedLockLease.from_handoff",
        _failing_from_handoff,
    )

    handoff = LockHandoffRef(
        handle_id="dead-handle",
        lock_paths=("/just/some/file",),  # no lock-file pattern at all
    )

    with pytest.raises(LockAcquisitionError):
        await SemanticLockScope.resolve(handoff)

    assert manager.acquire_tree_calls == []
    assert manager.acquire_tree_batch_calls == []
