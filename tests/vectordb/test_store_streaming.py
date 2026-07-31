# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import gc
import weakref

import pytest

import openviking.storage.vectordb.engine as engine
from openviking.storage.vectordb.store.data import CandidateData
from openviking.storage.vectordb.store.local_store import (
    STORE_SCAN_PAGE_SIZE,
    StoreEngineProxy,
)
from openviking.storage.vectordb.store.store_manager import StoreManager


class _TrackedPage(list):
    pass


class _PagedStorageEngine:
    def __init__(self, rows):
        self.rows = sorted(rows)
        self.page_refs = []
        self.page_sizes = []
        self.peak_live_pages = 0
        self.full_scan_calls = 0

    def seek_range(self, _start_key, _end_key):
        self.full_scan_calls += 1
        raise AssertionError("paged candidate recovery must not use a full range scan")

    def seek_range_page(
        self,
        start_key,
        end_key,
        limit,
        max_bytes,
        start_exclusive=False,
    ):
        gc.collect()
        selected = []
        selected_bytes = 0
        for row in self.rows:
            after_start = row[0] > start_key if start_exclusive else row[0] >= start_key
            if after_start and row[0] < end_key:
                row_bytes = len(row[0].encode("utf-8")) + len(row[1])
                if selected and (
                    selected_bytes >= max_bytes or row_bytes > max_bytes - selected_bytes
                ):
                    break
                selected.append(row)
                selected_bytes += row_bytes
                if len(selected) == limit:
                    break
        page = _TrackedPage(selected)
        self.page_refs.append(weakref.ref(page))
        self.page_sizes.append(len(page))
        self.peak_live_pages = max(
            self.peak_live_pages,
            sum(page_ref() is not None for page_ref in self.page_refs),
        )
        return page


class _LegacyStorageEngine:
    def __init__(self, rows):
        self.rows = sorted(rows)
        self.full_scan_calls = 0

    def seek_range(self, start_key, end_key):
        self.full_scan_calls += 1
        return [row for row in self.rows if start_key <= row[0] < end_key]


def _decode_candidate_payloads(monkeypatch):
    candidate_refs = []
    peak_live_candidates = 0

    def decode(payload):
        nonlocal peak_live_candidates
        candidate = CandidateData(label=int(payload.decode("ascii")), vector=[1.0, 0.0])
        candidate_refs.append(weakref.ref(candidate))
        peak_live_candidates = max(
            peak_live_candidates,
            sum(candidate_ref() is not None for candidate_ref in candidate_refs),
        )
        return candidate

    monkeypatch.setattr(CandidateData, "from_bytes", staticmethod(decode))
    return candidate_refs, lambda: peak_live_candidates


def test_candidate_recovery_bounds_encoded_pages_and_deserialized_rows(monkeypatch):
    prefix = StoreManager.CandsTable
    row_count = STORE_SCAN_PAGE_SIZE + 7
    keys = [str(index) for index in range(row_count)]
    rows = [(prefix + key, key.encode("ascii")) for key in keys]
    storage_engine = _PagedStorageEngine(rows)
    store = StoreEngineProxy(storage_engine)
    manager = StoreManager(store)
    candidate_refs, peak_live_candidates = _decode_candidate_payloads(monkeypatch)

    labels = []
    for candidate in manager.iter_all_cands_data():
        labels.append(candidate.label)

    assert labels == [int(key) for key in sorted(keys)]
    assert storage_engine.full_scan_calls == 0
    assert storage_engine.page_sizes == [STORE_SCAN_PAGE_SIZE, 7, 0]
    assert storage_engine.peak_live_pages == 1
    assert peak_live_candidates() <= 2
    del candidate
    gc.collect()
    assert not any(candidate_ref() is not None for candidate_ref in candidate_refs)


def test_store_proxy_pages_without_retaining_the_previous_encoded_page():
    rows = [
        ("candidate:" + key, ("value-" + key).encode("ascii"))
        for key in ("1", "10", "2", "20", "3", "30", "4")
    ]
    storage_engine = _PagedStorageEngine(rows)
    store = StoreEngineProxy(storage_engine)

    keys = []
    for key, value in store.iter_all("candidate:", page_size=100, page_bytes=40):
        keys.append(key)
        assert value == ("value-" + key).encode("ascii")

    assert keys == ["1", "10", "2", "20", "3", "30", "4"]
    assert storage_engine.page_sizes == [2, 2, 2, 1, 0]
    assert storage_engine.peak_live_pages == 1
    assert storage_engine.full_scan_calls == 0

    oversized_engine = _PagedStorageEngine(rows)
    oversized_items = list(
        StoreEngineProxy(oversized_engine).iter_all(
            "candidate:",
            page_size=100,
            page_bytes=1,
        )
    )
    assert [key for key, _value in oversized_items] == keys
    assert oversized_engine.page_sizes == [1] * len(rows) + [0]


def test_store_proxy_rejects_older_engines_without_bounded_scan():
    prefix = StoreManager.CandsTable
    rows = [(prefix + "1", b"1"), (prefix + "2", b"2")]
    storage_engine = _LegacyStorageEngine(rows)
    manager = StoreManager(StoreEngineProxy(storage_engine))

    with pytest.raises(RuntimeError, match="does not support bounded store scans"):
        manager.get_all_cands_data()

    assert storage_engine.full_scan_calls == 0


def test_store_proxy_rejects_a_nonadvancing_native_cursor():
    class StalledStorageEngine:
        def seek_range_page(
            self,
            _start_key,
            _end_key,
            _limit,
            _max_bytes,
            _start_exclusive=False,
        ):
            return [("candidate:1", b"value")]

    store = StoreEngineProxy(StalledStorageEngine())

    with pytest.raises(RuntimeError, match="did not advance its continuation cursor"):
        list(store.iter_all("candidate:", page_size=1))


def test_store_proxy_accepts_the_table_prefix_as_the_first_inclusive_key():
    storage_engine = _PagedStorageEngine([("candidate:", b"root"), ("candidate:1", b"child")])

    items = list(StoreEngineProxy(storage_engine).iter_all("candidate:", page_size=1))

    assert items == [("", b"root"), ("1", b"child")]
    assert storage_engine.page_sizes == [1, 1, 0]


def test_closing_store_stream_releases_the_current_encoded_page():
    rows = [(f"candidate:{index}", b"value") for index in range(4)]
    storage_engine = _PagedStorageEngine(rows)
    stream = StoreEngineProxy(storage_engine).iter_all("candidate:", page_size=2)

    assert next(stream)[0] == "0"
    stream.close()
    gc.collect()

    assert not any(page_ref() is not None for page_ref in storage_engine.page_refs)


def test_native_volatile_store_paged_scan_abi():
    if engine.ENGINE_VARIANT == "unavailable":
        pytest.skip("No native VectorDB engine is packaged in this test environment")

    store = engine.VolatileStore()
    assert (
        store.put_data(
            ["candidate:1", "candidate:10", "candidate:2"],
            [b"one", b"ten", b"two"],
        )
        == 0
    )

    first = store.seek_range_page("candidate:", "candidate;", 2, 1024, False)
    second = store.seek_range_page(first[-1][0], "candidate;", 2, 1024, True)
    byte_limited = store.seek_range_page("candidate:", "candidate;", 10, 14, False)
    oversized_first = store.seek_range_page("candidate:", "candidate;", 10, 1, False)

    assert first == [("candidate:1", b"one"), ("candidate:10", b"ten")]
    assert second == [("candidate:2", b"two")]
    assert byte_limited == [("candidate:1", b"one")]
    assert oversized_first == [("candidate:1", b"one")]
