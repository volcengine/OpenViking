# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""A hybrid search must look up its sparse terms without adding them to the index."""

import json

import pytest

import openviking.storage.vectordb.engine as engine

DIM = 4
DENSE = [0.5, 0.5, 0.5, 0.5]


def _index(distance):
    return engine.IndexEngine(
        json.dumps(
            {
                "CollectionName": "sparse_query_terms_test",
                "IndexName": "default",
                "VectorIndex": {
                    "IndexType": "flat",
                    "ElementCount": 0,
                    "MaxElementCount": 16,
                    "Dimension": DIM,
                    "Distance": distance,
                    "Quant": "float",
                    "EnableSparse": True,
                    "SearchWithSparseLogitAlpha": 0.5,
                },
                "ScalarIndex": [{"FieldName": "uri", "FieldType": "path"}],
            }
        )
    )


def _add(index, label, terms):
    request = engine.AddDataRequest()
    request.label = label
    request.vector = DENSE
    request.sparse_raw_terms = terms
    request.sparse_values = [1.0] * len(terms)
    request.fields_str = json.dumps({"uri": f"/records/{label}"})
    assert index.add_data([request]) == 0


def _delete(index, label):
    request = engine.DeleteDataRequest()
    request.label = label
    assert index.delete_data([request]) == 0


def _scores(index, terms):
    request = engine.SearchRequest()
    request.query = DENSE
    request.topk = 16
    request.sparse_raw_terms = terms
    request.sparse_values = [1.0] * len(terms)
    result = index.search(request)
    return dict(zip(result.labels, result.scores, strict=True))


def _dumped_sparse_terms(index, directory):
    index.dump(str(directory))
    return (directory / "vector_index" / "sparse_retrieval_row_base.bin").read_bytes()


@pytest.mark.parametrize("distance", ["ip", "l2"])
def test_search_does_not_add_query_terms_to_the_index(distance, tmp_path):
    index = _index(distance)
    _add(index, 1, ["alpha", "shared"])
    _add(index, 2, ["beta", "shared"])
    before = _dumped_sparse_terms(index, tmp_path / "before")

    _scores(index, ["alpha", "unseen-1", "unseen-2"])

    assert _dumped_sparse_terms(index, tmp_path / "after") == before, (
        "the search changed the sparse term dictionary"
    )


@pytest.mark.parametrize("distance", ["ip", "l2"])
def test_unseen_query_term_scores_like_a_term_no_record_has(distance):
    index = _index(distance)
    _add(index, 1, ["alpha", "shared"])
    _add(index, 2, ["beta", "shared"])
    # "gone" stays in the term dictionary after its only record is deleted.
    _add(index, 3, ["gone"])
    _delete(index, 3)

    assert _scores(index, ["alpha", "never-seen"]) == _scores(index, ["alpha", "gone"])
