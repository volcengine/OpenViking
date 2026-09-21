# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the Jev (TypeSafe System One) rerank client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from openviking.models.rerank import JevRerankClient, RerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig


def _mock_response(payload: dict, status_error=None):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock(side_effect=status_error)
    response.status_code = 401 if status_error else 200
    response.text = "error" if status_error else ""
    return response


def _systemone_payload(scores: list[float]) -> dict:
    answers = {
        f"relevance_{index}": {"type": "noul", "noul": score} for index, score in enumerate(scores)
    }
    return {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


class TestJevRerankClient:
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_rerank_batch_basic(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([0.97, 0.04, 0.07]))

        client = JevRerankClient(api_key="test-key")
        scores = client.rerank_batch("What is UCW?", ["doc A", "doc B", "doc C"])

        assert scores == [0.97, 0.04, 0.07]
        body = mock_client.post.call_args[1]["json"]
        assert body["model"] == "jev-latest"
        assert body["state"] == {
            "query": "What is UCW?",
            "candidate_documents": ["doc A", "doc B", "doc C"],
        }
        assert len(body["questions"]) == 3
        assert body["questions"]["relevance_0"]["type"] == "noul"
        assert body["questions"]["relevance_2"]["instructions"]["candidate_index"] == 2

    @patch("openviking.models.rerank.jev_rerank.logger.info")
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_logs_request_and_response_without_api_key(self, mock_client_class, mock_log):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([0.9]))

        client = JevRerankClient(api_key="secret-key")
        assert client.rerank_batch("query", ["document"]) == [0.9]

        messages = [str(call) for call in mock_log.call_args_list]
        assert any("[JevRerank] Request" in message for message in messages)
        assert any("[JevRerank] Response" in message for message in messages)
        assert all("secret-key" not in message for message in messages)

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_rerank_batch_empty(self, mock_client_class):
        client = JevRerankClient(api_key="test-key")
        assert client.rerank_batch("query", []) == []

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_rerank_batch_api_error_returns_none(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        error = httpx.HTTPStatusError("401", request=MagicMock(), response=MagicMock())
        mock_client.post.return_value = _mock_response({}, status_error=error)

        client = JevRerankClient(api_key="bad-key")
        assert client.rerank_batch("query", ["doc"]) is None

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_missing_answer_returns_none(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(
            {"answers": {}, "usage": {"input_tokens": 1, "output_tokens": 1}}
        )

        client = JevRerankClient(api_key="key")
        assert client.rerank_batch("q", ["doc"]) is None

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_each_document_gets_an_independent_question(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([0.2, 0.8, 0.4]))

        client = JevRerankClient(api_key="key")
        scores = client.rerank_batch("q", ["a", "b", "c"])

        assert scores == [0.2, 0.8, 0.4]
        questions = mock_client.post.call_args[1]["json"]["questions"]
        assert set(questions) == {"relevance_0", "relevance_1", "relevance_2"}

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_invalid_score_returns_none(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([1.1]))

        client = JevRerankClient(api_key="key")
        assert client.rerank_batch("q", ["doc"]) is None

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_close(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        client = JevRerankClient(api_key="key")
        client.close()
        mock_client.close.assert_called_once()


class TestJevRerankConfig:
    def test_explicit_jev_provider(self):
        config = RerankConfig(provider="jev", api_key="key")
        assert config._effective_provider() == "jev"
        assert config.is_available() is True

    def test_jev_auto_detected_from_api_base(self):
        config = RerankConfig(api_key="key", api_base="https://api.typesafe.ai")
        assert config._effective_provider() == "jev"

    def test_jev_requires_api_key(self):
        with pytest.raises(ValueError, match="Jev"):
            RerankConfig(provider="jev")

    def test_unknown_provider_rejected(self):
        with pytest.raises(ValueError, match="Rerank provider"):
            RerankConfig(provider="nope", api_key="k")


class TestJevDispatch:
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_from_config_creates_jev_client(self, mock_client_class):
        config = RerankConfig(provider="jev", api_key="jev-key")
        client = RerankClient.from_config(config)
        assert isinstance(client, JevRerankClient)
        assert client.api_key == "jev-key"
        assert client.model_name == "jev-latest"

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_from_config_uses_custom_model(self, mock_client_class):
        config = RerankConfig(provider="jev", api_key="key", model="jev-1.13.0")
        client = RerankClient.from_config(config)
        assert client.model_name == "jev-1.13.0"
