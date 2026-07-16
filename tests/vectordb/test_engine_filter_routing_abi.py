# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

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
