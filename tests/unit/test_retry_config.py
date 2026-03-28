# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for retry configuration fields on VLMConfig and EmbeddingConfig.

Verifies that:
- VLMConfig default max_retries = 3
- EmbeddingConfig has max_retries field, default = 3
- EmbeddingConfig accepts custom max_retries
"""

from __future__ import annotations

import pytest


class TestVLMConfigMaxRetries:

    def test_default_max_retries(self):
        """VLMConfig should default max_retries to 3."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        cfg = VLMConfig(
            model="gpt-4o-mini",
            api_key="sk-test",
            provider="openai",
        )
        assert cfg.max_retries == 3

    def test_custom_max_retries(self):
        """VLMConfig should accept custom max_retries."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        cfg = VLMConfig(
            model="gpt-4o-mini",
            api_key="sk-test",
            provider="openai",
            max_retries=10,
        )
        assert cfg.max_retries == 10


class TestEmbeddingConfigMaxRetries:

    def test_has_max_retries_field(self):
        """EmbeddingConfig should have a max_retries field."""
        from openviking_cli.utils.config.embedding_config import EmbeddingConfig

        fields = EmbeddingConfig.model_fields
        assert "max_retries" in fields, (
            f"EmbeddingConfig is missing 'max_retries' field. Fields: {list(fields.keys())}"
        )

    def test_default_max_retries(self):
        """EmbeddingConfig should default max_retries to 3."""
        from openviking_cli.utils.config.embedding_config import (
            EmbeddingConfig,
            EmbeddingModelConfig,
        )

        cfg = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                model="text-embedding-3-small",
                api_key="sk-test",
                provider="openai",
            ),
        )
        assert cfg.max_retries == 3

    def test_custom_max_retries(self):
        """EmbeddingConfig should accept custom max_retries."""
        from openviking_cli.utils.config.embedding_config import (
            EmbeddingConfig,
            EmbeddingModelConfig,
        )

        cfg = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                model="text-embedding-3-small",
                api_key="sk-test",
                provider="openai",
            ),
            max_retries=7,
        )
        assert cfg.max_retries == 7
