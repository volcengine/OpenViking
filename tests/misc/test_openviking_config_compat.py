# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_embedding_config_accepts_env_backed_openai_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
        )
    )

    assert config.dense is not None
    assert config.dense.get_effective_api_key() == "test-openai-key"


def test_vlm_config_accepts_env_backed_openai_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    config = VLMConfig(
        provider="openai",
        model="gpt-4o-mini",
    )

    provider_config, provider_name = config.get_provider_config()

    assert config._get_effective_api_key() == "test-openai-key"
    assert provider_name == "openai"
    assert provider_config == {"api_key": "test-openai-key"}


def test_openviking_config_accepts_sources_section(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    config = OpenVikingConfig.from_dict(
        {
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "dimension": 1536,
                }
            },
            "sources": {
                "sessions": [
                    {
                        "name": "codex",
                        "glob": "**/*.jsonl",
                    }
                ]
            },
        }
    )

    assert config.sources["sessions"][0]["name"] == "codex"
