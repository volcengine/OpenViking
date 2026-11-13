# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MiniMax Embedder (Simple)"""

import os
import unittest

from openviking.models.embedder.minimax_embedders import MinimaxDenseEmbedder
from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig


class TestMinimaxRealCall(unittest.TestCase):
    """Test cases for MinimaxDenseEmbedder with REAL API calls"""

    def setUp(self):
        # Retrieve API key and Group ID from environment variables
        self.api_key = os.environ.get("MINIMAX_API_KEY")
        self.group_id = os.environ.get("MINIMAX_GROUP_ID")

        if not self.api_key:
            self.skipTest("MINIMAX_API_KEY not set")

    def test_real_embedding(self):
        """Test real embedding call to MiniMax API"""
        print("\n[Real API] Testing MiniMax Embedder (embo-01)")

        embedder = MinimaxDenseEmbedder(
            model_name="embo-01",
            api_key=self.api_key,
            extra_headers={"GroupId": self.group_id} if self.group_id else None,
            document_param="db",
        )

        text = "OpenViking integration test for MiniMax."

        try:
            result = embedder.embed(text)

            # Verify result
            self.assertIsNotNone(result.dense_vector)
            dim = len(result.dense_vector)

            self.assertEqual(dim, 1536, "Expected dimension 1536")

        except Exception as e:
            self.fail(f"Real API call failed: {e}")


class TestEmbeddingModelConfig(unittest.TestCase):
    def test_minimax_provider_valid(self):
        config = EmbeddingModelConfig(provider="minimax", model="embo-01", api_key="test-key")
        self.assertEqual(config.provider, "minimax")
        self.assertEqual(config.model, "embo-01")

    def test_minimax_provider_requires_api_key(self):
        with self.assertRaisesRegex(ValueError, "MiniMax provider requires 'api_key'"):
            EmbeddingModelConfig(provider="minimax", model="embo-01")

    def test_extra_headers_and_param_fields(self):
        config = EmbeddingModelConfig(
            provider="minimax",
            model="embo-01",
            api_key="test-key",
            extra_headers={"GroupId": "group-123"},
            query_param="query",
            document_param="db",
        )
        self.assertEqual(config.extra_headers, {"GroupId": "group-123"})
        self.assertEqual(config.query_param, "query")
        self.assertEqual(config.document_param, "db")


if __name__ == "__main__":
    unittest.main()
