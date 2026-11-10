# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import gc
import json

import numpy as np
import pytest

from openviking.storage.vectordb.index.cuvs_index import (
    CuVSDenseIndex,
    CuVSSearchTelemetry,
    _CuVSRuntime,
)
from openviking.storage.vectordb.store.data import CandidateData


class _HostOnlyRuntime:
    pass


def _candidate(label: int) -> CandidateData:
    return CandidateData(label=label, vector=[float(label)], fields=json.dumps({"group": "a"}))


def _index(row_count: int) -> CuVSDenseIndex:
    index = CuVSDenseIndex(
        dimension=1,
        distance="ip",
        normalize_vectors=False,
        field_types={"group": "string"},
        config={},
        runtime=_HostOnlyRuntime(),
    )
    index.add_candidates(_candidate(label) for label in range(1, row_count + 1))
    with index._lock:
        index._filter_layout_generation = index._records_generation
    return index


def test_packed_filter_rejects_malformed_byte_length():
    index = _index(1)

    with pytest.raises(ValueError, match="multiple of 4 bytes"):
        index._resolve_native_filter({}, lambda _filters: (b"\x01", 1))


def test_packed_filter_completeness_uses_word_count_not_byte_count():
    index = _index(33)

    with pytest.raises(RuntimeError, match=r"got 1 words.*expected at least 2"):
        index._resolve_native_filter({}, lambda _filters: (b"\x01\x00\x00\x00", 1))


def test_packed_filter_preserves_legacy_host_mask_fallback_and_telemetry():
    index = _index(2)
    filters = {"op": "must", "field": "group", "conds": ["a"]}
    telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    packed = b"\x02\x00\x00\x00"

    host_filter = index._prepare_host_filter(
        filters,
        lambda _filters: (packed, 1),
        lambda _labels: None,
        telemetry,
    )
    assert host_filter is not None
    resolved = host_filter.resolved_native_filter
    assert resolved is not None
    prepared = index._prepare_filter(
        filters,
        [1, 2],
        lambda _filters: (packed, 1),
        resolved,
    )

    assert resolved.bitset_words is packed
    assert prepared.prepared == (False, True)
    assert prepared.eligible_count == 1
    assert telemetry.as_dict()["filter_words_packed"] is True

    cache_hit_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    cached_host_filter = index._prepare_host_filter(
        filters,
        lambda _filters: (_ for _ in ()).throw(AssertionError("resolver should not run")),
        lambda _labels: None,
        cache_hit_telemetry,
    )
    assert cached_host_filter is not None
    assert cached_host_filter.cached_metadata is not None
    assert cache_hit_telemetry.filter_cache_hit is True
    assert cache_hit_telemetry.filter_words_packed is True


def test_legacy_filter_words_keep_packed_origin_false_across_cache_hits():
    index = _index(2)
    filters = {"op": "must", "field": "group", "conds": ["a"]}
    first_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)

    first_host_filter = index._prepare_host_filter(
        filters,
        lambda _filters: ([0b01], 1),
        lambda _labels: None,
        first_telemetry,
    )
    assert first_host_filter is not None
    resolved = first_host_filter.resolved_native_filter
    assert resolved is not None
    index._prepare_filter(filters, [1, 2], lambda _filters: ([0b01], 1), resolved)

    cache_hit_telemetry = CuVSSearchTelemetry(algorithm="brute_force", auto_mode=False)
    index._prepare_host_filter(
        filters,
        lambda _filters: (_ for _ in ()).throw(AssertionError("resolver should not run")),
        lambda _labels: None,
        cache_hit_telemetry,
    )

    assert first_telemetry.filter_words_packed is False
    assert cache_hit_telemetry.filter_cache_hit is True
    assert cache_hit_telemetry.filter_words_packed is False


def test_in_gate_resolver_fallback_retains_packed_origin_and_native_token():
    index = _index(2)
    filters = {"op": "must", "field": "group", "conds": ["a"]}

    prepared = index._prepare_filter(
        filters,
        [1, 2],
        lambda _filters: (b"\x01\x00\x00\x00", 1, 23),
    )

    assert prepared.filter_words_packed is True
    assert prepared.native_filter_token == 23
    assert prepared.prepared == (True, False)


def test_runtime_uses_numpy_frombuffer_for_packed_filter_words():
    class _Device:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class _Cuda:
        @staticmethod
        def Device(_device_id):
            return _Device()

    class _CuPy:
        uint32 = object()
        cuda = _Cuda()

        def __init__(self):
            self.host_words = None
            self.dtype = None

        def empty(self, shape, dtype):
            assert shape == (2,)
            self.dtype = dtype
            owner = self

            class _DeviceWords:
                def set(self, values):
                    owner.host_words = values

            return _DeviceWords()

    runtime = _CuVSRuntime.__new__(_CuVSRuntime)
    runtime.cp = _CuPy()
    runtime.device_id = 0
    packed = b"\x04\x03\x02\x01\xdd\xcc\xbb\xaa"

    result = runtime.prepare_filter_words(packed)

    assert result is not None
    assert isinstance(runtime.cp.host_words, np.ndarray)
    assert runtime.cp.host_words.dtype.str == "<u4"
    assert runtime.cp.host_words.tolist() == [0x01020304, 0xAABBCCDD]
    assert runtime.cp.host_words.base is packed
    assert runtime.cp.dtype is runtime.cp.uint32


def test_runtime_rejects_malformed_packed_words_before_cupy_conversion():
    runtime = _CuVSRuntime.__new__(_CuVSRuntime)
    runtime.cp = object()
    runtime.device_id = 0

    with pytest.raises(ValueError, match="multiple of 4 bytes"):
        # Validation happens before touching CUDA, so malformed native output
        # cannot enter the device-copy path.
        runtime.prepare_filter_words(b"\x00")


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_failed_packed_copy_releases_device_words_in_captured_scope(failure_type):
    class _State:
        current_device = 9
        releases = []

    class _Device:
        def __init__(self, device_id):
            self.device_id = device_id

        def __enter__(self):
            self.previous = _State.current_device
            _State.current_device = self.device_id
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            _State.current_device = self.previous
            return False

    class _DeviceWords:
        def set(self, _values):
            raise failure_type("injected packed copy failure")

        def __del__(self):
            _State.releases.append(_State.current_device)

    class _Cuda:
        Device = _Device

    class _CuPy:
        uint32 = object()
        cuda = _Cuda()

        @staticmethod
        def empty(_shape, dtype):
            assert dtype is _CuPy.uint32
            return _DeviceWords()

    runtime = _CuVSRuntime.__new__(_CuVSRuntime)
    runtime.cp = _CuPy()
    runtime.device_id = 3

    with pytest.raises(failure_type, match="injected packed copy failure"):
        runtime.prepare_filter_words(b"\x01\x00\x00\x00")
    gc.collect()

    assert _State.releases == [3]
    assert _State.current_device == 9
