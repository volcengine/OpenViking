# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Cohere rerank client."""

from unittest.mock import MagicMock, patch

from openviking.models.rerank import CohereRerankClient


class TestCohereRerankClient:
    """Test cases for CohereRerankClient."""

    @patch("openviking.models.rerank.cohere_rerank.httpx.Client")
    def test_rerank_batch_basic(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.42},
                {"index": 2, "relevance_score": 0.10},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        client = CohereRerankClient(api_key="test-key")
        scores = client.rerank_batch("What is UCW?", ["doc A", "doc B", "doc C"])

        assert scores == [0.42, 0.95, 0.10]
        payload = mock_client.post.call_args[1]["json"]
        assert payload["model"] == "rerank-v3.5"
        assert payload["query"] == "What is UCW?"
        assert payload["documents"] == ["doc A", "doc B", "doc C"]
        assert payload["return_documents"] is False

    @patch("openviking.models.rerank.cohere_rerank.httpx.Client")
    def test_rerank_batch_empty(self, mock_client_class):
        client = CohereRerankClient(api_key="test-key")
        assert client.rerank_batch("query", []) == []

    @patch("openviking.models.rerank.cohere_rerank.httpx.Client")
    def test_rerank_batch_api_error_returns_none(self, mock_client_class):
        import httpx

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=mock_response
        )
        mock_client.post.return_value = mock_response

        client = CohereRerankClient(api_key="bad-key")
        result = client.rerank_batch("query", ["doc"])
        assert result is None

    @patch("openviking.models.rerank.cohere_rerank.httpx.Client")
    def test_rerank_preserves_original_order(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Cohere returns sorted by score desc, we must map back to original order
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"index": 2, "relevance_score": 0.99},
                {"index": 0, "relevance_score": 0.50},
                {"index": 1, "relevance_score": 0.01},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        client = CohereRerankClient(api_key="test-key")
        scores = client.rerank_batch("q", ["first", "second", "third"])

        assert scores[0] == 0.50  # "first" was index 0
        assert scores[1] == 0.01  # "second" was index 1
        assert scores[2] == 0.99  # "third" was index 2

    @patch("openviking.models.rerank.cohere_rerank.httpx.Client")
    def test_close(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        client = CohereRerankClient(api_key="test-key")
        client.close()
        mock_client.close.assert_called_once()


class TestRerankConfig:
    """Test RerankConfig provider detection."""

    def test_cohere_provider_auto_detected(self):
        from openviking_cli.utils.config.rerank_config import RerankConfig

        config = RerankConfig(api_key="cohere-key")
        assert config._effective_provider() == "cohere"
        assert config.is_available() is True

    def test_vikingdb_provider_auto_detected(self):
        from openviking_cli.utils.config.rerank_config import RerankConfig

        config = RerankConfig(ak="ak", sk="sk")
        assert config._effective_provider() == "vikingdb"
        assert config.is_available() is True

    def test_explicit_provider_overrides(self):
        from openviking_cli.utils.config.rerank_config import RerankConfig

        config = RerankConfig(provider="cohere", api_key="key", ak="ak", sk="sk")
        assert config._effective_provider() == "cohere"

    def test_empty_config_not_available(self):
        from openviking_cli.utils.config.rerank_config import RerankConfig

        config = RerankConfig()
        assert config.is_available() is False
        assert config._effective_provider() is None


class TestUnifiedRerankDispatch:
    """Test that RerankClient.from_config() dispatches all providers uniformly."""

    @patch("openviking.models.rerank.cohere_rerank.httpx.Client")
    def test_from_config_creates_cohere_client(self, mock_client_class):
        from openviking.models.rerank import RerankClient
        from openviking_cli.utils.config.rerank_config import RerankConfig

        config = RerankConfig(api_key="cohere-key")
        client = RerankClient.from_config(config)
        assert isinstance(client, CohereRerankClient)
        assert client.api_key == "cohere-key"
        assert client.model == "rerank-v3.5"

    @patch("openviking.models.rerank.cohere_rerank.httpx.Client")
    def test_from_config_cohere_explicit_provider(self, mock_client_class):
        from openviking.models.rerank import RerankClient
        from openviking_cli.utils.config.rerank_config import RerankConfig

        config = RerankConfig(provider="cohere", api_key="key")
        client = RerankClient.from_config(config)
        assert isinstance(client, CohereRerankClient)

    def test_from_config_returns_none_when_unavailable(self):
        from openviking.models.rerank import RerankClient
        from openviking_cli.utils.config.rerank_config import RerankConfig

        config = RerankConfig()
        assert RerankClient.from_config(config) is None

    @patch("openviking.models.rerank.cohere_rerank.httpx.Client")
    def test_cohere_from_config_uses_custom_model(self, mock_client_class):
        from openviking.models.rerank import RerankClient
        from openviking_cli.utils.config.rerank_config import RerankConfig

        config = RerankConfig(api_key="key", model_name="rerank-v4.0")
        client = RerankClient.from_config(config)
        assert isinstance(client, CohereRerankClient)
        assert client.model == "rerank-v4.0"
