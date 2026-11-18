# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""`delete_all_data` must not destroy indexes when the reset cannot finish."""

from __future__ import annotations

import threading
from contextlib import nullcontext

import pytest

from openviking.storage.vectordb.collection.local_collection import LocalCollection


class _FakeBarrier:
    def exclusive_mutation(self):
        return nullcontext()


class _FakeIndexes:
    def __init__(self, indexes):
        self._indexes = indexes

    def get_all_with_lock(self):
        return nullcontext(self._indexes)


class _FakeIndex:
    def __init__(self, name, events, *, drop_error=None):
        self.name = name
        self.events = events
        self.drop_error = drop_error
        self.dropped = False

    def get_meta_data(self):
        return {"IndexName": self.name}

    def drop(self):
        self.dropped = True
        self.events.append(f"drop:{self.name}")
        if self.drop_error is not None:
            raise self.drop_error


class _FakeStore:
    def __init__(self, events, *, clear_error=None):
        self.events = events
        self.clear_error = clear_error
        self.cleared = False

    def clear(self):
        self.cleared = True
        self.events.append("clear")
        if self.clear_error is not None:
            raise self.clear_error


class _FakeRebuiltIndex:
    def __init__(self, name):
        self.name = name
        self.bulk_ingest_started = False

    def begin_bulk_ingest(self):
        self.bulk_ingest_started = True


def _make_collection(indexes, store, *, events, new_index=None):
    collection = LocalCollection.__new__(LocalCollection)
    collection.indexes = _FakeIndexes(indexes)
    collection.store_mgr = store
    collection._index_mutation_barrier = _FakeBarrier()
    collection._bulk_ingest_lock = threading.Lock()
    collection._bulk_ingest_depth = 0
    collection.collection_name = "test_collection"
    if new_index is None:

        def new_index(name, meta, candidates, *, defer_dense_rebuild_start):
            events.append(f"rebuild:{name}")
            return _FakeRebuiltIndex(name)

    collection._new_index = new_index
    return collection


def test_clear_failure_keeps_existing_indexes():
    events = []
    first = _FakeIndex("first", events)
    second = _FakeIndex("second", events)
    indexes = {"first": first, "second": second}
    store = _FakeStore(events, clear_error=RuntimeError("disk offline"))
    collection = _make_collection(indexes, store, events=events)

    with pytest.raises(RuntimeError, match="disk offline"):
        collection.delete_all_data()

    assert store.cleared
    assert not first.dropped
    assert not second.dropped
    assert indexes == {"first": first, "second": second}
    # Nothing may be dropped or rebuilt before the storage clear succeeds.
    assert events == ["clear"]


def test_rebuild_failure_is_reported_after_other_indexes_rebuild():
    events = []
    broken = _FakeIndex("broken", events)
    healthy = _FakeIndex("healthy", events)
    indexes = {"broken": broken, "healthy": healthy}
    store = _FakeStore(events)

    def new_index(name, meta, candidates, *, defer_dense_rebuild_start):
        if name == "broken":
            raise RuntimeError("index directory unavailable")
        events.append(f"rebuild:{name}")
        return _FakeRebuiltIndex(name)

    collection = _make_collection(indexes, store, events=events, new_index=new_index)

    with pytest.raises(
        RuntimeError,
        match=r"1 of 2 index\(es\).*broken: index directory unavailable",
    ):
        collection.delete_all_data()

    assert broken.dropped
    assert healthy.dropped
    assert "broken" not in indexes
    assert isinstance(indexes["healthy"], _FakeRebuiltIndex)


def test_successful_reset_clears_storage_before_dropping_indexes():
    events = []
    first = _FakeIndex("first", events)
    second = _FakeIndex("second", events)
    indexes = {"first": first, "second": second}
    store = _FakeStore(events)
    collection = _make_collection(indexes, store, events=events)

    collection.delete_all_data()

    assert events == [
        "clear",
        "drop:first",
        "drop:second",
        "rebuild:first",
        "rebuild:second",
    ]
    assert set(indexes) == {"first", "second"}
    assert all(isinstance(index, _FakeRebuiltIndex) for index in indexes.values())
