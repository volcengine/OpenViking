# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Behaviour-equivalence tests for semantic coalesce versioning.

The coalesce version was migrated from a process-local module dict to the
cross-instance Coordinator. With the default in-process backend the observable
behaviour must be identical to the pre-migration singleton: each enqueue for a
key bumps the version monotonically, and a message is stale once a newer
version exists for its key.
"""

import pytest

from openviking.service.coordinator import InProcessCoordinator, set_coordinator
from openviking.storage.queuefs.semantic_queue import (
    _coalesce_coord_key,
    is_semantic_coalesce_stale,
)


@pytest.fixture(autouse=True)
def fresh_coordinator():
    """Isolate each test behind a clean in-process coordinator."""
    set_coordinator(InProcessCoordinator())
    yield


class TestCoalesceStaleness:
    def test_empty_key_is_never_stale(self):
        assert is_semantic_coalesce_stale("", 5) is False

    def test_nonpositive_version_is_never_stale(self):
        assert is_semantic_coalesce_stale("k", 0) is False
        assert is_semantic_coalesce_stale("k", -1) is False

    def test_unset_key_is_not_stale(self):
        # No enqueue has happened, so current version is 0; v1 is not behind it.
        assert is_semantic_coalesce_stale("k", 1) is False

    def test_latest_version_is_not_stale(self):
        coord = InProcessCoordinator()
        set_coordinator(coord)
        v = coord.incr(_coalesce_coord_key("k"))
        assert is_semantic_coalesce_stale("k", v) is False

    def test_older_version_is_stale_after_newer_enqueue(self):
        coord = InProcessCoordinator()
        set_coordinator(coord)
        v1 = coord.incr(_coalesce_coord_key("k"))
        v2 = coord.incr(_coalesce_coord_key("k"))
        assert v2 == v1 + 1
        assert is_semantic_coalesce_stale("k", v1) is True
        assert is_semantic_coalesce_stale("k", v2) is False

    def test_versions_are_isolated_per_key(self):
        coord = InProcessCoordinator()
        set_coordinator(coord)
        a1 = coord.incr(_coalesce_coord_key("a"))
        b1 = coord.incr(_coalesce_coord_key("b"))
        assert a1 == 1
        assert b1 == 1
        coord.incr(_coalesce_coord_key("a"))
        # Bumping "a" must not make "b"'s v1 stale.
        assert is_semantic_coalesce_stale("b", b1) is False
        assert is_semantic_coalesce_stale("a", a1) is True
