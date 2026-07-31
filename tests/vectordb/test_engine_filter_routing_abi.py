# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.vectordb.engine._python_api import build_abi3_exports


class _CurrentBackend:
    def __init__(self):
        self.calls = []

    def _new_index_engine(self, path_or_json):
        self.calls.append(("new", path_or_json))
        return "engine-handle"

    def _index_engine_evaluate_filter(self, handle, dsl):
        self.calls.append(("generic", handle, dsl))
        return {"eligible_count": 1, "bitset_words": [1], "native_filter_token": 0}

    def _index_engine_evaluate_filter_cached(self, handle, dsl, threshold):
        self.calls.append(("cached", handle, dsl, threshold))
        return {"eligible_count": 1, "bitset_words": [1], "native_filter_token": 7}

    def _index_engine_evaluate_filter_for_routing(self, handle, dsl, threshold):
        self.calls.append(("routed", handle, dsl, threshold))
        if threshold == 0:
            return {"eligible_count": 1, "bitset_words": [1], "native_filter_token": 0}
        return {"eligible_count": 1, "bitset_words": [], "native_filter_token": 11}


class _LegacyBackend:
    def __init__(self):
        self.calls = []

    def _new_index_engine(self, path_or_json):
        return "legacy-handle"

    def _index_engine_evaluate_filter(self, handle, dsl):
        self.calls.append(("generic", handle, dsl))
        return {"eligible_count": 1, "bitset_words": [2], "native_filter_token": 0}

    def _index_engine_evaluate_filter_cached(self, handle, dsl, threshold):
        self.calls.append(("cached", handle, dsl, threshold))
        return {"eligible_count": 1, "bitset_words": [2], "native_filter_token": 13}


class _PackedBackend(_CurrentBackend):
    def _index_engine_evaluate_filter_packed(self, handle, dsl):
        self.calls.append(("generic-packed", handle, dsl))
        return {
            "eligible_count": 2,
            "bitset_words_le": b"\x04\x03\x02\x01\xdd\xcc\xbb\xaa",
            "native_filter_token": 0,
        }

    def _index_engine_evaluate_filter_cached_packed(self, handle, dsl, threshold):
        self.calls.append(("cached-packed", handle, dsl, threshold))
        return {
            "eligible_count": 2,
            "bitset_words_le": b"\x04\x03\x02\x01\xdd\xcc\xbb\xaa",
            "native_filter_token": 17,
        }

    def _index_engine_evaluate_filter_for_routing_packed(self, handle, dsl, threshold):
        self.calls.append(("routed-packed", handle, dsl, threshold))
        if threshold >= 2:
            return {
                "eligible_count": 2,
                "bitset_words_le": b"",
                "native_filter_token": 19,
            }
        return {
            "eligible_count": 2,
            "bitset_words_le": b"\x04\x03\x02\x01\xdd\xcc\xbb\xaa",
            "native_filter_token": 0,
        }


def test_routed_filter_uses_additive_abi_without_changing_generic_calls():
    backend = _CurrentBackend()
    index_engine = build_abi3_exports(backend)["IndexEngine"]("config")

    routed = index_engine.evaluate_filter_for_routing("dsl", native_threshold=5)
    generic = index_engine.evaluate_filter("dsl", max_cached_candidates=5)

    assert routed.eligible_count == 1
    assert routed.bitset_words == []
    assert routed.native_filter_token == 11
    assert generic.bitset_words == [1]
    assert generic.native_filter_token == 7
    assert ("routed", "engine-handle", "dsl", 5) in backend.calls
    assert ("cached", "engine-handle", "dsl", 5) in backend.calls


def test_routed_filter_threshold_zero_reaches_new_abi():
    backend = _CurrentBackend()
    index_engine = build_abi3_exports(backend)["IndexEngine"]("config")

    result = index_engine.evaluate_filter_for_routing("dsl", native_threshold=0)

    assert result.eligible_count == 1
    assert result.bitset_words == [1]
    assert result.native_filter_token == 0
    assert ("routed", "engine-handle", "dsl", 0) in backend.calls


def test_routed_filter_falls_back_with_a_legacy_extension():
    backend = _LegacyBackend()
    index_engine = build_abi3_exports(backend)["IndexEngine"]("config")

    result = index_engine.evaluate_filter_for_routing("dsl", native_threshold=5)
    threshold_zero = index_engine.evaluate_filter_for_routing("dsl", native_threshold=0)

    assert result.eligible_count == 1
    assert result.bitset_words == [2]
    assert result.native_filter_token == 13
    assert threshold_zero.bitset_words == [2]
    assert threshold_zero.native_filter_token == 0
    assert backend.calls == [
        ("cached", "legacy-handle", "dsl", 5),
        ("generic", "legacy-handle", "dsl"),
    ]


def test_packed_filter_abi_preserves_little_endian_bytes_and_route_shapes():
    backend = _PackedBackend()
    index_engine = build_abi3_exports(backend)["IndexEngine"]("config")

    generic = index_engine.evaluate_filter_packed("wide")
    cached = index_engine.evaluate_filter_packed("wide", max_cached_candidates=4)
    routed = index_engine.evaluate_filter_for_routing_packed("narrow", native_threshold=2)

    expected = b"\x04\x03\x02\x01\xdd\xcc\xbb\xaa"
    assert generic.bitset_words == expected
    assert cached.bitset_words == expected
    assert cached.native_filter_token == 17
    assert routed.bitset_words == b""
    assert routed.eligible_count == 2
    assert routed.native_filter_token == 19
    assert backend.calls[-3:] == [
        ("generic-packed", "engine-handle", "wide"),
        ("cached-packed", "engine-handle", "wide", 4),
        ("routed-packed", "engine-handle", "narrow", 2),
    ]


def test_packed_filter_methods_fall_back_to_legacy_backend_results():
    backend = _LegacyBackend()
    index_engine = build_abi3_exports(backend)["IndexEngine"]("config")

    generic = index_engine.evaluate_filter_packed("wide")
    cached = index_engine.evaluate_filter_packed("wide", max_cached_candidates=5)
    routed = index_engine.evaluate_filter_for_routing_packed("narrow", native_threshold=5)

    assert generic.bitset_words == [2]
    assert cached.bitset_words == [2]
    assert cached.native_filter_token == 13
    assert routed.bitset_words == [2]
    assert routed.native_filter_token == 13
    assert backend.calls == [
        ("generic", "legacy-handle", "wide"),
        ("cached", "legacy-handle", "wide", 5),
        ("cached", "legacy-handle", "narrow", 5),
    ]


def test_packed_filter_abi_rejects_a_non_bytes_payload():
    backend = _PackedBackend()
    backend._index_engine_evaluate_filter_packed = lambda _handle, _dsl: {
        "eligible_count": 1,
        "bitset_words_le": [1],
        "native_filter_token": 0,
    }
    index_engine = build_abi3_exports(backend)["IndexEngine"]("config")

    with pytest.raises(TypeError, match="bitset_words_le as bytes"):
        index_engine.evaluate_filter_packed("dsl")


def test_packed_filter_abi_rejects_a_missing_payload():
    backend = _PackedBackend()
    backend._index_engine_evaluate_filter_packed = lambda _handle, _dsl: {
        "eligible_count": 0,
        "native_filter_token": 0,
    }
    index_engine = build_abi3_exports(backend)["IndexEngine"]("config")

    with pytest.raises(KeyError, match="missing bitset_words_le"):
        index_engine.evaluate_filter_packed("dsl")
