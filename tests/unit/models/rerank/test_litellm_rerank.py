# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the LiteLLM rerank client (covers Voyage via litellm)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openviking.models.rerank.litellm_rerank import LiteLLMRerankClient


def _make_response(results):
    response = MagicMock()
    response.model_dump.return_value = {}
    response.results = results
    return response


class TestLiteLLMRerankClient:
    """Test cases for LiteLLMRerankClient."""

    @patch("litellm.rerank")
    def test_documents_passed_as_plain_strings(self, mock_rerank):
        # Voyage's rerank API rejects [{"text": ...}] document wrappers; litellm
        # expects a list of plain strings.
        mock_rerank.return_value = _make_response(
            [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
                {"index": 2, "relevance_score": 0.7},
            ]
        )

        client = LiteLLMRerankClient(api_key="k", api_base=None, model_name="voyage/rerank-2.5")
        client.rerank_batch("q", ["doc A", "doc B", "doc C"])

        assert mock_rerank.call_args.kwargs["documents"] == ["doc A", "doc B", "doc C"]

    @patch("litellm.rerank")
    def test_parses_dict_results_and_preserves_order(self, mock_rerank):
        # litellm returns dict result items for Voyage; scores must map back to
        # the original document order.
        mock_rerank.return_value = _make_response(
            [
                {"index": 2, "relevance_score": 0.99},
                {"index": 0, "relevance_score": 0.50},
                {"index": 1, "relevance_score": 0.01},
            ]
        )

        client = LiteLLMRerankClient(api_key="k", api_base=None, model_name="voyage/rerank-2.5")
        scores = client.rerank_batch("q", ["first", "second", "third"])

        assert scores == [0.50, 0.01, 0.99]

    @patch("litellm.rerank")
    def test_parses_object_results(self, mock_rerank):
        # Object-shaped result items (e.g. Cohere via litellm) must still work.
        mock_rerank.return_value = _make_response(
            [
                SimpleNamespace(index=1, relevance_score=0.6),
                SimpleNamespace(index=0, relevance_score=0.4),
            ]
        )

        client = LiteLLMRerankClient(api_key="k", api_base=None, model_name="rerank-model")
        scores = client.rerank_batch("q", ["a", "b"])

        assert scores == [0.4, 0.6]

    @patch("litellm.rerank")
    def test_missing_index_falls_back_to_none(self, mock_rerank):
        # A result item lacking an index must trigger the safe no-op fallback
        # (caller then keeps the pre-rerank order) rather than mis-mapping.
        mock_rerank.return_value = _make_response(
            [
                {"relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
            ]
        )

        client = LiteLLMRerankClient(api_key="k", api_base=None, model_name="voyage/rerank-2.5")
        assert client.rerank_batch("q", ["a", "b"]) is None

    @patch("litellm.rerank")
    def test_empty_documents_short_circuits(self, mock_rerank):
        client = LiteLLMRerankClient(api_key="k", api_base=None, model_name="voyage/rerank-2.5")
        assert client.rerank_batch("q", []) == []
        mock_rerank.assert_not_called()
