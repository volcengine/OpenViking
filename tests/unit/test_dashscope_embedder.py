# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for DashScope (Alibaba Tongyi) embedder support."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder import DashScopeDenseEmbedder
from openviking.models.embedder.dashscope_embedders import (
    DASHSCOPE_MODEL_DIMENSIONS,
    DEFAULT_CN_ENDPOINT,
    DEFAULT_INTL_ENDPOINT,
    get_dashscope_model_default_dimension,
)


class TestDashScopeDenseEmbedder:
    def test_init_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            DashScopeDenseEmbedder(model_name="tongyi-embedding-vision-flash")

    def test_default_cn_endpoint(self):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-x",
        )
        assert embedder.api_base == DEFAULT_CN_ENDPOINT

    def test_intl_endpoint_alias(self):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-x",
            endpoint="intl",
        )
        assert embedder.api_base == DEFAULT_INTL_ENDPOINT

    def test_api_base_overrides_endpoint(self):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-x",
            api_base="https://custom.example.com/",
            endpoint="intl",
        )
        assert embedder.api_base == "https://custom.example.com"

    def test_model_default_dimension(self):
        assert DASHSCOPE_MODEL_DIMENSIONS["qwen3-vl-embedding"] == 2560
        assert DASHSCOPE_MODEL_DIMENSIONS["tongyi-embedding-vision-flash"] == 768
        assert get_dashscope_model_default_dimension("tongyi-embedding-vision-flash") == 768
        assert get_dashscope_model_default_dimension("unknown") == 1024
        assert get_dashscope_model_default_dimension(None) == 1024

    def test_text_payload_shape(self):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-x",
            input_type="text",
            dimension=512,
        )
        payload = embedder._build_text_payload(["hello", "world"])
        assert payload == {
            "model": "text-embedding-v4",
            "input": ["hello", "world"],
            "dimensions": 512,
        }

    def test_multimodal_payload_shape(self):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash-2026-03-06",
            api_key="sk-x",
            input_type="multimodal",
            dimension=768,
            enable_fusion=True,
        )
        payload = embedder._build_multimodal_payload(["hi"])
        assert payload["model"] == "tongyi-embedding-vision-flash-2026-03-06"
        assert payload["input"] == {"contents": [{"text": "hi"}]}
        assert payload["parameters"] == {"dimension": 768, "enable_fusion": True}

    def test_multimodal_payload_without_fusion_flag(self):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-x",
            input_type="multimodal",
        )
        payload = embedder._build_multimodal_payload(["hi"])
        assert "parameters" not in payload or "enable_fusion" not in payload.get(
            "parameters", {}
        )

    def test_is_multimodal_routing(self):
        e1 = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-x",
            input_type="multimodal",
        )
        e2 = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-x",
            input_type="text",
        )
        assert e1._is_multimodal() is True
        assert e2._is_multimodal() is False

    def test_raise_for_status_401(self):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-x",
        )
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"error": "invalid key"}
        with pytest.raises(RuntimeError, match="401"):
            embedder._raise_for_status(resp)

    def test_raise_for_status_400(self):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-x",
        )
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"error": "bad input"}
        with pytest.raises(RuntimeError, match="400"):
            embedder._raise_for_status(resp)

    def test_parse_text_response(self):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-x",
        )
        data = {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
        assert embedder._parse_text_response(data) == [[0.1, 0.2], [0.3, 0.4]]

    def test_parse_multimodal_response(self):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-x",
        )
        data = {"output": {"embeddings": [{"embedding": [0.5, 0.6]}]}}
        assert embedder._parse_multimodal_response(data) == [[0.5, 0.6]]

    def test_parse_multimodal_response_missing_output(self):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-x",
        )
        with pytest.raises(RuntimeError, match="missing 'output'"):
            embedder._parse_multimodal_response({"message": "err"})

    def test_text_embed_calls_openai_compatible_endpoint(self):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-x",
            input_type="text",
            dimension=1024,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"embedding": [0.1] * 1024}]}
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        with patch.object(embedder, "_get_client", return_value=mock_client):
            result = embedder.embed("hello")
        assert len(result.dense_vector) == 1024
        call_url = mock_client.post.call_args[0][0]
        assert "/compatible-mode/v1/embeddings" in call_url

    def test_multimodal_embed_calls_native_endpoint(self):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-x",
            input_type="multimodal",
            dimension=768,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "output": {"embeddings": [{"embedding": [0.2] * 768}]}
        }
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        with patch.object(embedder, "_get_client", return_value=mock_client):
            result = embedder.embed("hi")
        assert len(result.dense_vector) == 768
        call_url = mock_client.post.call_args[0][0]
        assert "/multimodal-embedding/multimodal-embedding" in call_url
