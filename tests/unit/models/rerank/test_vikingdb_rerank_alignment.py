# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingDB scores must remain attached to their input documents."""

from unittest.mock import Mock, patch

import pytest

from openviking.models.rerank.volcengine_rerank import RerankClient


@pytest.fixture
def client():
    return RerankClient(ak="test-ak", sk="test-sk")


def rerank(client, data, documents=None):
    response = Mock(status_code=200)
    response.json.return_value = {"result": {"data": data}}
    with patch(
        "openviking.models.rerank.volcengine_rerank.requests.request", return_value=response
    ):
        return client.rerank_batch("query", documents or ["doc A", "doc B", "doc C"])


@pytest.mark.parametrize("order", [(0, 1, 2), (1, 0, 2), (2, 1, 0)])
def test_response_order_does_not_change_document_scores(client, order):
    expected = [0.2, 0.9, 0.6]
    data = [{"id": index, "score": expected[index]} for index in order]
    assert rerank(client, data) == expected


def test_numeric_string_ids_preserve_document_scores(client):
    assert rerank(
        client, [{"id": "2", "score": 0.6}, {"id": "0", "score": 0.2}, {"id": "1", "score": 0.9}]
    ) == [0.2, 0.9, 0.6]


@pytest.mark.parametrize("bad_id", [None, -1, 3, 0.5, True, "not-an-id"])
def test_invalid_document_id_fails_batch(client, bad_id):
    assert (
        rerank(
            client, [{"id": bad_id, "score": 0.2}, {"id": 1, "score": 0.9}, {"id": 2, "score": 0.6}]
        )
        is None
    )


def test_missing_document_id_fails_batch(client):
    assert (
        rerank(client, [{"score": 0.2}, {"id": 1, "score": 0.9}, {"id": 2, "score": 0.6}]) is None
    )


def test_duplicate_id_cannot_hide_a_missing_document(client):
    assert (
        rerank(client, [{"id": 0, "score": 0.2}, {"id": 0, "score": 0.9}, {"id": 2, "score": 0.6}])
        is None
    )


def test_incomplete_response_fails_batch(client):
    assert rerank(client, [{"id": 0, "score": 0.2}, {"id": 2, "score": 0.6}]) is None


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf"), None])
def test_invalid_score_fails_batch(client, score):
    assert (
        rerank(
            client, [{"id": 0, "score": score}, {"id": 1, "score": 0.9}, {"id": 2, "score": 0.6}]
        )
        is None
    )


@pytest.mark.parametrize("data", [None, {}, ["bad-item", {}, {}]])
def test_malformed_results_fail_batch(client, data):
    assert rerank(client, data) is None


def test_empty_input_does_not_call_provider(client):
    with patch("openviking.models.rerank.volcengine_rerank.requests.request") as request:
        assert client.rerank_batch("query", []) == []
    request.assert_not_called()
