# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for event-memory operation tagging in the V3 compressor.

Only ``events``-type operations receive the caller-provided custom scalar
tags; other memory types are untouched. Empty/None tags are a no-op.
"""

from openviking.session.compressor_v3 import _apply_event_search_tags
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations


def _op(memory_type, uris):
    return ResolvedOperation(memory_fields={}, memory_type=memory_type, uris=list(uris))


def _ops(*operations):
    return ResolvedOperations(
        upsert_operations=list(operations),
        delete_file_contents=[],
        errors=[],
    )


class TestApplyEventSearchTags:
    def test_tags_only_events_operations(self):
        ops = _ops(
            _op("events", ["viking://e1"]),
            _op("experiences", ["viking://x1"]),
        )
        _apply_event_search_tags(ops, ["team=search", "channel=web"])
        event_op, other_op = ops.upsert_operations
        assert event_op.search_tags == ["team=search", "channel=web"]
        assert other_op.search_tags is None

    def test_empty_tags_is_noop(self):
        ops = _ops(_op("events", ["viking://e1"]))
        _apply_event_search_tags(ops, [])
        assert ops.upsert_operations[0].search_tags is None

    def test_none_tags_is_noop(self):
        ops = _ops(_op("events", ["viking://e1"]))
        _apply_event_search_tags(ops, None)
        assert ops.upsert_operations[0].search_tags is None

    def test_none_operations_is_safe(self):
        # orchestrator may return None; helper must tolerate it.
        _apply_event_search_tags(None, ["team=search"])
