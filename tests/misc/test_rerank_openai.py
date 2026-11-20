# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAI-compatible rerank client and factory dispatch."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from openviking.models.rerank import OpenAIRerankClient, RerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig


class TestOpenAIRerankClient:
    def _make_client(self):
        return OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/services/rerank",
            model_name="gte-rerank-v2",
        )

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://dashscope.aliyuncs.com/api/v1/services/rerank",
            "https://gateway.example/v1/rerank",
        ],
        ids=["native", "compatible"],
    )
    def test_rerank_batch_unexpected_format_returns_none(self, endpoint):
        client = OpenAIRerankClient(
            api_key="test-key", api_base=endpoint, model_name="qwen3-rerank"
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": "format"}
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ):
            result = client.rerank_batch("query", ["doc1"])

        assert result is None

    def test_rerank_batch_out_of_bounds_index_returns_none(self):
        """An index that is >= len(documents) should return None."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 5, "relevance_score": 0.9},  # only 1 doc
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ):
            result = client.rerank_batch("query", ["doc1"])

        assert result is None

    def test_rerank_batch_missing_index_field_returns_none(self):
        """A result item with no 'index' key should return None."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"relevance_score": 0.9},  # missing 'index'
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ):
            result = client.rerank_batch("query", ["doc1"])

        assert result is None

    def test_rerank_batch_http_error_returns_none(self):
        client = self._make_client()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post",
            side_effect=Exception("connection error"),
        ):
            result = client.rerank_batch("query", ["doc1"])

        assert result is None


class TestRerankClientFactoryDispatch:
    def test_factory_dispatches_to_vikingdb_client(self):
        config = RerankConfig(
            provider="vikingdb",
            ak="test-ak",
            sk="test-sk",
        )
        client = RerankClient.from_config(config)
        assert isinstance(client, RerankClient)
        assert not isinstance(client, OpenAIRerankClient)

    def test_factory_defaults_to_vikingdb(self):
        """Config without provider field defaults to vikingdb."""
        config = RerankConfig(ak="test-ak", sk="test-sk")
        client = RerankClient.from_config(config)
        assert isinstance(client, RerankClient)
        assert not isinstance(client, OpenAIRerankClient)

    def test_factory_returns_none_for_none_config(self):
        assert RerankClient.from_config(None) is None

    def test_factory_returns_none_for_unavailable_vikingdb_config(self):
        config = RerankConfig()  # no ak/sk
        assert RerankClient.from_config(config) is None


class TestRerankConfig:
    def test_openai_requires_api_key_and_api_base(self):
        with pytest.raises(ValidationError):
            RerankConfig(provider="openai", api_key="key")

        with pytest.raises(ValidationError):
            RerankConfig(provider="openai", api_base="https://example.com/rerank")

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValidationError, match="provider"):
            RerankConfig(provider="unknown", api_key="key")

    def test_explicit_provider_requires_its_own_credentials(self):
        with pytest.raises(ValidationError, match="api_key"):
            RerankConfig(provider="cohere", ak="ak", sk="sk")

        with pytest.raises(ValidationError, match="ak"):
            RerankConfig(provider="vikingdb", api_key="key")
