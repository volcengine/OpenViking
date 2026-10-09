# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for tiered rerank routing, profiles, and retry behavior (T-20261008-03)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from openviking.models.rerank.openai_rerank import OpenAIRerankClient
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role, UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.rerank_config import RerankConfig


def _make_dummy_ctx(rerank_lane=None) -> RequestContext:
    user = UserIdentifier(account_id="acc_1", user_id="usr_1")
    return RequestContext(
        user=user,
        role=Role.USER,
        rerank_lane=rerank_lane,
    )


class TestRerankConfigMaxRetries:
    """Test max_retries field on RerankConfig."""

    def test_default_max_retries(self):
        config = RerankConfig()
        assert config.max_retries == 2

    def test_custom_max_retries(self):
        config = RerankConfig(max_retries=5)
        assert config.max_retries == 5

    def test_negative_max_retries_rejected(self):
        with pytest.raises(ValueError):
            RerankConfig(max_retries=-1)


class TestOpenAIRerankClientRetry:
    """Test retry behavior in OpenAIRerankClient with max_retries."""

    def test_client_respects_max_retries_from_config(self):
        config = RerankConfig(
            provider="openai",
            api_key="test-key",
            api_base="http://localhost:8082/v1/rerank",
            model="test-model",
            max_retries=3,
        )
        client = OpenAIRerankClient.from_config(config)
        assert client.max_retries == 3

    @patch("time.sleep", return_value=None)
    @patch("requests.post")
    def test_connection_error_retries_and_succeeds(self, mock_post, mock_sleep):
        # 1st attempt: ConnectionError, 2nd attempt: Success
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "results": [{"index": 0, "relevance_score": 0.95}]
        }
        mock_post.side_effect = [
            requests.exceptions.ConnectionError("Connection refused"),
            success_response,
        ]

        client = OpenAIRerankClient(
            api_key="key",
            api_base="http://localhost:8082/v1/rerank",
            model_name="test-model",
            max_retries=2,
        )
        scores = client.rerank_batch("test query", ["doc 1"])
        assert scores == [0.95]
        assert mock_post.call_count == 2
        assert mock_sleep.call_count == 1

    @patch("time.sleep", return_value=None)
    @patch("requests.post")
    def test_connection_error_exhausts_retries(self, mock_post, mock_sleep):
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        client = OpenAIRerankClient(
            api_key="key",
            api_base="http://localhost:8082/v1/rerank",
            model_name="test-model",
            max_retries=2,
        )
        scores = client.rerank_batch("test query", ["doc 1"])
        assert scores is None
        # Initial attempt + 2 retries = 3 attempts total
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.5)
        mock_sleep.assert_any_call(1.0)

    @patch("requests.post")
    def test_zero_retries_fails_immediately(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        client = OpenAIRerankClient(
            api_key="key",
            api_base="http://localhost:8082/v1/rerank",
            model_name="test-model",
            max_retries=0,
        )
        scores = client.rerank_batch("test query", ["doc 1"])
        assert scores is None
        assert mock_post.call_count == 1


class TestRerankProfilesAndRoutingConfig:
    """Test rerank_profiles and rerank_routing parsing and back-compatibility."""

    def test_legacy_config_without_profiles_or_routing(self):
        raw_dict = {
            "rerank": {
                "provider": "openai",
                "api_key": "legacy-key",
                "api_base": "http://127.0.0.1:8081/v1/rerank",
                "model": "legacy-model",
            }
        }
        config = OpenVikingConfig(**raw_dict)
        assert config.rerank.is_available()
        assert config.rerank.model == "legacy-model"
        assert config.rerank_profiles == {}
        assert config.rerank_routing == {}

    def test_config_with_profiles_and_routing(self):
        raw_dict = {
            "rerank": {
                "provider": "openai",
                "api_key": "fallback-key",
                "api_base": "http://127.0.0.1:8081/v1/rerank",
                "model": "fallback-model",
            },
            "rerank_profiles": {
                "light": {
                    "provider": "openai",
                    "api_key": "local",
                    "api_base": "http://127.0.0.1:8082/v1/rerank",
                    "model": "Qwen3-Reranker-4B-MLX",
                    "timeout": 20.0,
                    "threshold": 0.1,
                },
                "heavy": {
                    "provider": "openai",
                    "api_key": "cloud-key",
                    "api_base": "https://ai.gitee.com/v1/rerank",
                    "model": "Qwen3-Reranker-8B",
                    "timeout": 75.0,
                    "threshold": 0.15,
                },
            },
            "rerank_routing": {
                "context": "light",
                "find": "heavy",
                "default": "light",
            },
        }
        config = OpenVikingConfig(**raw_dict)
        assert len(config.rerank_profiles) == 2
        assert config.rerank_profiles["light"].model == "Qwen3-Reranker-4B-MLX"
        assert config.rerank_profiles["light"].timeout == 20.0
        assert config.rerank_profiles["heavy"].model == "Qwen3-Reranker-8B"
        assert config.rerank_profiles["heavy"].threshold == 0.15
        assert config.rerank_routing["context"] == "light"
        assert config.rerank_routing["find"] == "heavy"


class TestHierarchicalRetrieverRouting:
    """Test lane resolution and retriever behavior with profiles."""

    def test_legacy_single_config_byte_identical_behavior(self):
        legacy_cfg = RerankConfig(
            provider="openai",
            api_key="k",
            api_base="http://127.0.0.1:8081/v1/rerank",
            model="Q8_0",
            threshold=0.1,
        )
        retriever = HierarchicalRetriever(
            storage=MagicMock(),
            embedder=MagicMock(),
            rerank_config=legacy_cfg,
        )
        ctx = _make_dummy_ctx()
        assert retriever._resolve_lane(ctx) is None
        client, cfg, threshold, max_tokens = retriever._get_lane_resources(None)
        assert client is not None
        assert cfg == legacy_cfg
        assert threshold == 0.1

    def test_profile_routing_context_to_light_and_find_to_heavy(self):
        light_cfg = RerankConfig(
            provider="openai",
            api_key="k1",
            api_base="http://127.0.0.1:8082/v1/rerank",
            model="4B-MLX",
            threshold=0.08,
            max_input_tokens=512,
        )
        heavy_cfg = RerankConfig(
            provider="openai",
            api_key="k2",
            api_base="https://ai.gitee.com/v1/rerank",
            model="8B-Cloud",
            threshold=0.15,
            max_input_tokens=1024,
        )
        profiles = {"light": light_cfg, "heavy": heavy_cfg}
        routing = {"context": "light", "find": "heavy", "default": "light"}

        retriever = HierarchicalRetriever(
            storage=MagicMock(),
            embedder=MagicMock(),
            rerank_config=None,
            rerank_profiles=profiles,
            rerank_routing=routing,
        )

        # Context request routed to light lane
        ctx_light = _make_dummy_ctx(rerank_lane="light")
        resolved_light = retriever._resolve_lane(ctx_light)
        assert resolved_light == "light"
        client_l, cfg_l, thresh_l, max_tok_l = retriever._get_lane_resources("light")
        assert cfg_l.model == "4B-MLX"
        assert thresh_l == 0.08
        assert max_tok_l == 512

        # Find request routed to heavy lane
        ctx_heavy = _make_dummy_ctx(rerank_lane="heavy")
        resolved_heavy = retriever._resolve_lane(ctx_heavy)
        assert resolved_heavy == "heavy"
        client_h, cfg_h, thresh_h, max_tok_h = retriever._get_lane_resources("heavy")
        assert cfg_h.model == "8B-Cloud"
        assert thresh_h == 0.15
        assert max_tok_h == 1024

    def test_fallback_to_default_profile_when_lane_unknown(self):
        light_cfg = RerankConfig(
            provider="openai",
            api_key="k1",
            api_base="http://127.0.0.1:8082/v1/rerank",
            model="4B-MLX",
        )
        profiles = {"light": light_cfg}
        routing = {"default": "light"}

        retriever = HierarchicalRetriever(
            storage=MagicMock(),
            embedder=MagicMock(),
            rerank_profiles=profiles,
            rerank_routing=routing,
        )

        ctx_unknown = _make_dummy_ctx(rerank_lane="nonexistent")
        # Should fall back to default profile "light"
        lane = retriever._resolve_lane(ctx_unknown)
        assert lane == "light"
        client, cfg, _, _ = retriever._get_lane_resources(lane)
        assert cfg.model == "4B-MLX"
