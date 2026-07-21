# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

import openviking.storage.vectordb.engine as engine


def _native_packed_abi_available() -> bool:
    backend = getattr(engine, "_BACKEND", None)
    return backend is not None and hasattr(backend, "_index_engine_evaluate_filter_packed")


@pytest.mark.skipif(
    not _native_packed_abi_available(),
    reason="the packaged native extension predates the additive packed filter ABI",
)
def test_native_packed_filter_abi_has_exact_little_endian_layout_and_shapes():
    config = json.dumps(
        {
            "CollectionName": "packed_filter_abi_test",
            "IndexName": "default",
            "VectorIndex": {
                "IndexType": "flat",
                "ElementCount": 0,
                "MaxElementCount": 96,
                "Dimension": 1,
                "Distance": "l2",
                "Quant": "float",
            },
            "ScalarIndex": [{"FieldName": "uri", "FieldType": "path"}],
        }
    )
    index = engine.IndexEngine(config)
    selected_rows = {0, 8, 16, 24, 31, 32, 63, 64}
    labels = list(range(1000, 1065))
    requests = []
    for row, label in enumerate(labels):
        request = engine.AddDataRequest()
        request.label = label
        request.vector = [float(row)]
        request.fields_str = json.dumps(
            {"uri": f"/keep/item-{row}" if row in selected_rows else f"/drop/item-{row}"}
        )
        requests.append(request)
    assert index.add_data(requests) == 0
    assert index.set_filter_layout(labels) == 0

    keep_filter = json.dumps({"op": "must", "field": "uri", "conds": ["/keep"], "para": "-d=-1"})
    missing_filter = json.dumps(
        {"op": "must", "field": "uri", "conds": ["/missing"], "para": "-d=-1"}
    )
    expected_words = [0x81010101, 0x80000001, 0x00000001]
    expected_bytes = b"\x01\x01\x01\x81\x01\x00\x00\x80\x01\x00\x00\x00"

    legacy = index.evaluate_filter(keep_filter)
    generic = index.evaluate_filter_packed(keep_filter)
    cached = index.evaluate_filter_packed(keep_filter, max_cached_candidates=10)
    routed_narrow = index.evaluate_filter_for_routing_packed(keep_filter, native_threshold=10)
    routed_wide = index.evaluate_filter_for_routing_packed(keep_filter, native_threshold=1)
    empty = index.evaluate_filter_packed(missing_filter)

    assert legacy.bitset_words == expected_words
    assert generic.bitset_words == expected_bytes
    assert generic.eligible_count == 8
    assert generic.native_filter_token == 0
    assert cached.bitset_words == expected_bytes
    assert cached.native_filter_token > 0
    assert routed_narrow.bitset_words == b""
    assert routed_narrow.eligible_count == 8
    assert routed_narrow.native_filter_token > 0
    assert routed_wide.bitset_words == expected_bytes
    assert routed_wide.native_filter_token == 0
    assert empty.bitset_words == b"\x00" * 12
    assert empty.eligible_count == 0
