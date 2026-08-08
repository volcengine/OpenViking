# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for per-operation event search-tag aggregation.

Event memories carry their custom scalar tags on each ResolvedOperation
(op.search_tags). MemoryUpdater aggregates those into the per-URI map that
_vectorize_memories consumes, alongside any caller-supplied map (e.g.
trajectory tags).
"""

from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_updater import _collect_search_tags_by_uri


def _op(uris, memory_type="events", search_tags=None):
    return ResolvedOperation(
        memory_fields={},
        memory_type=memory_type,
        uris=list(uris),
        search_tags=search_tags,
    )


def _ops(*operations):
    return ResolvedOperations(
        upsert_operations=list(operations),
        delete_file_contents=[],
        errors=[],
    )


class TestCollectSearchTagsByUri:
    def test_default_search_tags_is_none(self):
        op = _op(["viking://a"])
        assert op.search_tags is None

    def test_per_op_tags_map_to_each_uri(self):
        ops = _ops(_op(["viking://a", "viking://b"], search_tags=["team=search"]))
        result = _collect_search_tags_by_uri(ops)
        assert result == {
            "viking://a": ["team=search"],
            "viking://b": ["team=search"],
        }

    def test_op_without_tags_is_skipped(self):
        ops = _ops(_op(["viking://a"], search_tags=None), _op(["viking://b"], search_tags=[]))
        assert _collect_search_tags_by_uri(ops) == {}

    def test_caller_map_takes_precedence_over_per_op(self):
        ops = _ops(_op(["viking://a"], search_tags=["team=search"]))
        result = _collect_search_tags_by_uri(ops, {"viking://a": ["outcome=success"]})
        assert result == {"viking://a": ["outcome=success"]}

    def test_per_op_fills_gaps_left_by_caller_map(self):
        ops = _ops(
            _op(["viking://a"], search_tags=["team=search"]),
            _op(["viking://b"], search_tags=["channel=web"]),
        )
        result = _collect_search_tags_by_uri(ops, {"viking://a": ["outcome=success"]})
        assert result == {
            "viking://a": ["outcome=success"],
            "viking://b": ["channel=web"],
        }
