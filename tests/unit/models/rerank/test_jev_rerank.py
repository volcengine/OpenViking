# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the Jev (TypeSafe System One) rerank client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from openviking.models.rerank import JevRerankClient, RerankClient
from openviking.models.rerank.jev_rerank import MODE_CHOICE, MODE_NOUL
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

        assert client.mode == MODE_NOUL
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
        mock_client.post.assert_called_once_with("https://api.typesafe.ai/v1/systemone", json=body)

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_vercel_gateway_uses_typesafe_compatible_endpoint(self, mock_client_class):
        """Vercel is just another api_base; the wire protocol stays TypeSafe System One."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([0.91, 0.08]))

        client = JevRerankClient(
            api_key="vercel-key",
            api_base="https://ai-gateway.vercel.sh/typesafe",
            model_name="typesafe-ai/jev",
            mode=MODE_NOUL,
        )
        assert client.rerank_batch("query", ["first", "second"]) == [0.91, 0.08]

        call = mock_client.post.call_args
        assert call.args == ("https://ai-gateway.vercel.sh/typesafe/v1/systemone",)
        assert call.kwargs["json"]["model"] == "typesafe-ai/jev"
        assert call.kwargs["json"]["questions"]["relevance_0"]["type"] == "noul"
        assert "headers" not in call.kwargs

    @patch("openviking.models.rerank.jev_rerank.logger.warning")
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_logs_request_and_response_without_api_key(self, mock_client_class, mock_log):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([0.9]))

        client = JevRerankClient(api_key="secret-key", log_payloads=True, mode=MODE_NOUL)
        assert client.rerank_batch("query", ["document"]) == [0.9]

        messages = [str(call) for call in mock_log.call_args_list]
        assert any("[JevRerank] Request" in message for message in messages)
        assert any("[JevRerank] Response" in message for message in messages)
        assert all("secret-key" not in message for message in messages)

    @patch("openviking.models.rerank.jev_rerank.logger.warning")
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_payload_logging_disabled_by_default(self, mock_client_class, mock_log):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([0.9]))

        client = JevRerankClient(api_key="secret-key", mode=MODE_NOUL)
        assert client.rerank_batch("query", ["document"]) == [0.9]
        mock_log.assert_not_called()

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

        client = JevRerankClient(api_key="bad-key", mode=MODE_NOUL)
        assert client.rerank_batch("query", ["doc"]) is None

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_missing_answer_returns_none(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(
            {"answers": {}, "usage": {"input_tokens": 1, "output_tokens": 1}}
        )

        client = JevRerankClient(api_key="key", mode=MODE_NOUL)
        assert client.rerank_batch("q", ["doc"]) is None

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_each_document_gets_an_independent_question(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([0.2, 0.8, 0.4]))

        client = JevRerankClient(api_key="key", mode=MODE_NOUL)
        scores = client.rerank_batch("q", ["a", "b", "c"])

        assert scores == [0.2, 0.8, 0.4]
        questions = mock_client.post.call_args[1]["json"]["questions"]
        assert set(questions) == {"relevance_0", "relevance_1", "relevance_2"}

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_invalid_score_returns_none(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.post.return_value = _mock_response(_systemone_payload([1.1]))

        client = JevRerankClient(api_key="key", mode=MODE_NOUL)
        assert client.rerank_batch("q", ["doc"]) is None

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_close(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        client = JevRerankClient(api_key="key")
        client.close()
        mock_client.close.assert_called_once()


class TestChoiceRerank:
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_choice_preserves_document_order(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_client.post.return_value = _mock_response(
            {
                "answers": {
                    "relevance": {
                        "type": "choice",
                        "choice": "candidate_1",
                        "confidence": 0.55,
                        "probabilities": {
                            "candidate_2": 0.2,
                            "candidate_1": 0.7,
                            "candidate_0": 0.1,
                        },
                    }
                },
                "usage": {"input_tokens": None, "output_tokens": 1},
            }
        )
        client = JevRerankClient(api_key="key", mode=MODE_CHOICE)
        assert client.mode == MODE_CHOICE
        assert client.rerank_batch("query", ["first", "second", "third"]) == [0.1, 0.7, 0.2]
        body = mock_client.post.call_args.kwargs["json"]
        assert body["state"] == {
            "query": "query",
            "candidate_documents": ["first", "second", "third"],
        }
        assert body["questions"] == {
            "relevance": {
                "type": "choice",
                "instructions": (
                    "Which candidate document best answers or matches the retrieval intent of query?"
                ),
                "criteria": {
                    "candidate_0": (
                        "candidate_documents[0] directly answers or is relevant to the query"
                    ),
                    "candidate_1": (
                        "candidate_documents[1] directly answers or is relevant to the query"
                    ),
                    "candidate_2": (
                        "candidate_documents[2] directly answers or is relevant to the query"
                    ),
                },
            }
        }
        mock_client.post.assert_called_once_with("https://api.typesafe.ai/v1/systemone", json=body)

    @pytest.mark.parametrize("count", [1, 30])
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_choice_does_not_impose_candidate_limit(self, mock_client_class, count):
        probabilities = {f"candidate_{index}": 1 / count for index in range(count)}
        mock_client_class.return_value.post.return_value = _mock_response(
            {"answers": {"relevance": {"type": "choice", "probabilities": probabilities}}}
        )
        client = JevRerankClient(api_key="key", mode=MODE_CHOICE)
        assert client.rerank_batch("query", ["doc"] * count) == [1 / count] * count
        criteria = mock_client_class.return_value.post.call_args.kwargs["json"]["questions"][
            "relevance"
        ]["criteria"]
        assert list(criteria) == list(probabilities)

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_choice_empty_input_does_not_send_request(self, mock_client_class):
        client = JevRerankClient(api_key="key", mode=MODE_CHOICE)
        assert client.rerank_batch("query", []) == []
        mock_client_class.return_value.post.assert_not_called()

    @pytest.mark.parametrize(
        "answer",
        [
            None,
            {"type": "noul", "noul": 0.8},
            {"type": "choice", "choice": "candidate_0", "confidence": 0.8},
            {"type": "choice", "probabilities": []},
            {"type": "choice", "probabilities": {"candidate_0": 0.8}},
            {
                "type": "choice",
                "probabilities": {"candidate_0": 0.8, "candidate_1": "0.2"},
            },
            {"type": "choice", "probabilities": {"candidate_0": 0.8, "candidate_1": True}},
            {"type": "choice", "probabilities": {"candidate_0": 0.8, "candidate_1": -0.1}},
            {"type": "choice", "probabilities": {"candidate_0": 0.8, "candidate_1": 1.1}},
            {
                "type": "choice",
                "probabilities": {"candidate_0": 0.8, "candidate_1": float("nan")},
            },
        ],
    )
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_invalid_choice_answer_returns_none(self, mock_client_class, answer):
        mock_client_class.return_value.post.return_value = _mock_response(
            {"answers": {"relevance": answer}}
        )
        client = JevRerankClient(api_key="key", mode=MODE_CHOICE)
        assert client.rerank_batch("query", ["first", "second"]) is None
        mock_client_class.return_value.post.assert_called_once()

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_unknown_mode_rejected_before_client_creation(self, mock_client_class):
        with pytest.raises(ValueError, match="Unknown Jev rerank mode"):
            JevRerankClient(api_key="key", mode="unknown")
        mock_client_class.assert_not_called()


class TestJevRerankConfig:
    def test_explicit_jev_provider(self):
        config = RerankConfig(provider="jev", api_key="key")
        assert config._effective_provider() == "jev"
        assert config.is_available() is True
        assert config.mode == MODE_NOUL

    @pytest.mark.parametrize("mode", [MODE_CHOICE, "CHOICE"])
    def test_explicit_choice_mode(self, mode):
        config = RerankConfig(provider="jev", api_key="key", mode=mode)
        assert config.mode == MODE_CHOICE

    def test_null_mode_uses_client_default(self):
        config = RerankConfig(provider="jev", api_key="key", mode=None)
        assert config.mode is None

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="Jev rerank mode"):
            RerankConfig(provider="jev", api_key="key", mode="unknown")

    def test_non_jev_provider_does_not_validate_jev_mode(self):
        config = RerankConfig(
            provider="openai",
            api_key="key",
            api_base="https://example.com/rerank",
            mode="listwise",
        )
        assert config.mode == "listwise"

    def test_jev_auto_detected_from_api_base(self):
        config = RerankConfig(api_key="key", api_base="https://api.typesafe.ai")
        assert config._effective_provider() == "jev"

    def test_jev_auto_detected_from_vercel_typesafe_base(self):
        config = RerankConfig(api_key="key", api_base="https://ai-gateway.vercel.sh/typesafe")
        assert config._effective_provider() == "jev"

    def test_plain_vercel_base_is_not_jev(self):
        config = RerankConfig(api_key="key", api_base="https://ai-gateway.vercel.sh/v1")
        assert config._effective_provider() == "openai"

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
        assert client.mode == MODE_NOUL

    @pytest.mark.parametrize("mode", [MODE_CHOICE, None])
    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_from_config_resolves_mode(self, mock_client_class, mode):
        config = RerankConfig(provider="jev", api_key="key", mode=mode)
        client = RerankClient.from_config(config)
        assert client.mode == (mode or MODE_NOUL)

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_from_config_uses_custom_model(self, mock_client_class):
        config = RerankConfig(provider="jev", api_key="key", model="jev-1.13.0")
        client = RerankClient.from_config(config)
        assert client.model_name == "jev-1.13.0"

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_from_config_enables_payload_logging(self, mock_client_class):
        config = RerankConfig(provider="jev", api_key="key", log_payloads=True)
        client = RerankClient.from_config(config)
        assert client.log_payloads is True

    @patch("openviking.models.rerank.jev_rerank.httpx.Client")
    def test_from_config_uses_vercel_model_default(self, mock_client_class):
        config = RerankConfig(
            provider="jev",
            api_key="key",
            api_base="https://ai-gateway.vercel.sh/typesafe",
        )
        client = RerankClient.from_config(config)
        assert client.model_name == "typesafe-ai/jev"
        assert client.api_url == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
