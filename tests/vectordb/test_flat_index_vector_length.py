# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""The flat index must never score a record from bytes that are not its own vector."""

import json
import math
import random

import pytest

import openviking.storage.vectordb.engine as engine

DIM = 64


def _unit_vector(rng):
    values = [rng.gauss(0.0, 1.0) for _ in range(DIM)]
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def _index(quant):
    return engine.IndexEngine(
        json.dumps(
            {
                "CollectionName": "flat_index_vector_length_test",
                "IndexName": "default",
                "VectorIndex": {
                    "IndexType": "flat",
                    "ElementCount": 0,
                    "MaxElementCount": 16,
                    "Dimension": DIM,
                    "Distance": "ip",
                    "Quant": quant,
                },
                "ScalarIndex": [{"FieldName": "uri", "FieldType": "path"}],
            }
        )
    )


def _add(index, label, vector):
    request = engine.AddDataRequest()
    request.label = label
    request.vector = vector
    request.fields_str = json.dumps({"uri": f"/records/{label}"})
    assert index.add_data([request]) == 0


def _delete(index, label):
    request = engine.DeleteDataRequest()
    request.label = label
    assert index.delete_data([request]) == 0


def _scores(index, query):
    request = engine.SearchRequest()
    request.query = query
    request.topk = 16
    result = index.search(request)
    return dict(zip(result.labels, result.scores, strict=True))


@pytest.mark.parametrize("quant", ["int8", "float"])
def test_record_without_vector_does_not_inherit_a_deleted_records_vector(quant):
    rng = random.Random(0)
    index = _index(quant)
    vectors = [_unit_vector(rng) for _ in range(4)]
    for label, vector in enumerate(vectors):
        _add(index, label, vector)

    # Removing the tail record leaves its bytes in the slot the next new record takes.
    _delete(index, 3)
    _add(index, 100, [])

    assert _scores(index, vectors[3])[100] == 0.0


@pytest.mark.parametrize("quant", ["int8", "float"])
def test_vector_of_the_wrong_length_is_not_read(quant):
    rng = random.Random(1)
    index = _index(quant)
    kept = _unit_vector(rng)
    _add(index, 1, kept)

    short = _unit_vector(rng)[: DIM // 4]
    _add(index, 2, short)
    # An update with a short vector keeps the record's previous vector.
    _add(index, 1, short)

    scores = _scores(index, short + [0.0] * (DIM - DIM // 4))
    assert scores[2] == 0.0
    assert _scores(index, kept)[1] == pytest.approx(1.0, abs=0.02)
