# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Event tags survive from V3 operations to vectorization inputs."""

from openviking.session.compressor_v3 import _apply_event_search_tags
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


def test_event_tags_flow_to_each_event_uri_without_overwriting_caller_tags():
    operations = _ops(
        _op(["viking://events/a", "viking://events/b"]),
        _op(["viking://experiences/x"], memory_type="experiences"),
    )

    _apply_event_search_tags(operations, ["team=search"])
    result = _collect_search_tags_by_uri(
        operations,
        {"viking://events/a": ["outcome=success"]},
    )

    assert operations.upsert_operations[1].search_tags is None
    assert result == {
        "viking://events/a": ["outcome=success"],
        "viking://events/b": ["team=search"],
    }
