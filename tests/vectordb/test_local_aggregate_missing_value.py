# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for issue #5877.

On the local vector backend, a count whose filter references a scalar value
that was never inserted used to travel the engine error path (null filter
bitmap -> failed search -> empty ``extra_json``) and was rendered by the
Python layers as a legitimate zero, alongside an
"Aggregation results not available: extra_json is empty" warning. These tests
pin the contract:

* a filter on a value that was never inserted is a valid no-match query and
  must produce an explicit ``_total: 0`` without any warning;
* an engine-side aggregation failure must raise instead of being laundered
  into an empty aggregation result.
"""

import json
import logging
import uuid

import pytest

import openviking.storage.vectordb.engine as engine
from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)

_WARNING_TEXT = "Aggregation results not available: extra_json is empty"


class _ExtraJsonWarningCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        if _WARNING_TEXT in record.getMessage():
            self.messages.append(record.getMessage())

    @property
    def triggered(self):
        return bool(self.messages)


@pytest.fixture()
def warning_capture():
    capture = _ExtraJsonWarningCapture()
    # local_index logs through openviking_cli's default_logger ("openviking").
    loggers = [logging.getLogger("openviking"), logging.getLogger()]
    for target in loggers:
        target.addHandler(capture)
    yield capture
    for target in loggers:
        target.removeHandler(capture)


def _build_collection(path):
    meta = {
        "CollectionName": "issue_5877_regression",
        "Fields": [
            {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            {"FieldName": "category", "FieldType": "string"},
        ],
    }
    collection = get_or_create_local_collection(meta_data=meta, path=str(path / str(uuid.uuid4())))
    collection.upsert_data(
        [
            {"id": 1, "vector": [0.1, 0.1, 0.1, 0.1], "category": "success"},
            {"id": 2, "vector": [0.2, 0.2, 0.2, 0.2], "category": "success"},
            {"id": 3, "vector": [0.3, 0.3, 0.3, 0.3], "category": "failure"},
        ]
    )
    collection.create_index(
        "idx",
        {
            "IndexName": "idx",
            "VectorIndex": {"IndexType": "flat", "Distance": "ip"},
            "ScalarIndex": ["category"],
        },
    )
    return collection


def _count(collection, filters):
    return collection.aggregate_data("idx", op="count", filters=filters).agg


def _must(value):
    return {"op": "must", "field": "category", "conds": [value]}


def test_count_unfiltered_returns_total(tmp_path, warning_capture):
    collection = _build_collection(tmp_path)
    try:
        assert _count(collection, None) == {"_total": 3}
    finally:
        collection.close()
    assert not warning_capture.triggered


def test_count_inserted_value_returns_exact_matches(tmp_path, warning_capture):
    collection = _build_collection(tmp_path)
    try:
        assert _count(collection, _must("success")) == {"_total": 2}
        assert _count(collection, _must("failure")) == {"_total": 1}
    finally:
        collection.close()
    assert not warning_capture.triggered


def test_count_never_inserted_value_is_explicit_zero_without_warning(tmp_path, warning_capture):
    """A filter on a scalar value that was never inserted matches nothing.

    It must behave like any other no-match query (explicit zero) and must not
    take the engine error path that used to emit the "extra_json is empty"
    warning (#5877).
    """
    collection = _build_collection(tmp_path)
    try:
        assert _count(collection, _must("absent")) == {"_total": 0}
    finally:
        collection.close()
    assert not warning_capture.triggered


def test_count_empty_intersection_is_zero_without_warning(tmp_path, warning_capture):
    """Control: a genuine empty intersection already returned an explicit zero."""
    collection = _build_collection(tmp_path)
    try:
        filters = {
            "op": "and",
            "conds": [_must("success"), _must("failure")],
        }
        assert _count(collection, filters) == {"_total": 0}
    finally:
        collection.close()
    assert not warning_capture.triggered


def test_aggregate_raises_when_engine_serves_no_payload(tmp_path):
    """An aggregation without an engine payload must surface as an exception.

    Before #5877 the missing payload was swallowed into ``{}`` (after a
    warning), which callers cannot distinguish from a legitimate zero.
    """
    collection = _build_collection(tmp_path)
    try:
        index = collection._Collection__collection.indexes.get("idx")
        # An empty DSL: the engine serves the request but has neither sorter
        # nor counter to run, so it returns no aggregation payload.
        with pytest.raises(RuntimeError, match="extra_json is empty"):
            index.aggregate({})
    finally:
        collection.close()


def test_native_index_engine_search_raises_on_bad_dsl(tmp_path):
    """Native regression: IndexEngine::search propagates parse failures."""
    collection = _build_collection(tmp_path)
    try:
        index = collection._Collection__collection.indexes.get("idx")
        native = index.engine_proxy.index_engine
        request = engine.SearchRequest()
        request.topk = 1
        request.dsl = '{"sorter": not-parseable'
        with pytest.raises(RuntimeError):
            native.search(request)
    finally:
        collection.close()


def test_native_index_engine_search_count_on_missing_value(tmp_path):
    """Native regression: never-inserted value yields an explicit zero."""
    collection = _build_collection(tmp_path)
    try:
        index = collection._Collection__collection.indexes.get("idx")
        native = index.engine_proxy.index_engine
        request = engine.SearchRequest()
        request.topk = 1
        request.dsl = json.dumps({"sorter": {"op": "count"}, "filter": _must("absent")})
        result = native.search(request)
        assert json.loads(result.extra_json) == {"__total_count__": 0}
    finally:
        collection.close()
