# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for embedding providers with unified retry logic.

Tests cover (using OpenAI and VikingDB as representatives):
- embed retries on transient error (mock API client)
- embed does NOT retry on permanent error
- uses config max_retries
- VikingDB now has retry
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _HttpError(Exception):
    """Fake HTTP error carrying a numeric status code."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def _make_fake_embedding_response(vector=None):
    """Build a minimal fake OpenAI embeddings response."""
    if vector is None:
        vector = [0.1] * 10
    item = SimpleNamespace(embedding=vector)
    usage = SimpleNamespace(prompt_tokens=5, total_tokens=5)
    return SimpleNamespace(data=[item], usage=usage)


# ---------------------------------------------------------------------------
# OpenAI Embedder Tests
# ---------------------------------------------------------------------------

class TestOpenAIEmbedderRetry:

    @pytest.fixture()
    def openai_embedder(self):
        """Create an OpenAIDenseEmbedder with mocked client."""
        from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
            dimension=10,
            config={"max_retries": 2},
        )
        embedder.client = MagicMock()
        return embedder

    def test_embed_retries_on_transient_error(self, openai_embedder):
        """embed() should retry on 429 (transient) and succeed."""
        errors = [_HttpError(429)]
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return _make_fake_embedding_response()

        openai_embedder.client.embeddings.create = fake_create

        with patch("time.sleep"):
            result = openai_embedder.embed("test text")

        assert result.dense_vector == [0.1] * 10
        assert call_count == 2  # 1 failure + 1 success

    def test_embed_no_retry_on_permanent_error(self, openai_embedder):
        """embed() should NOT retry on 401 (permanent)."""
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            raise _HttpError(401, "Unauthorized")

        openai_embedder.client.embeddings.create = fake_create

        with patch("time.sleep"):
            # 401 is permanent, transient_retry won't retry it.
            # It will propagate and be caught by the except block, re-raised as RuntimeError.
            with pytest.raises((RuntimeError, _HttpError)):
                openai_embedder.embed("test text")

        assert call_count == 1  # no retries

    def test_uses_config_max_retries(self):
        """Embedder should use self.max_retries from config."""
        from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
            dimension=10,
            config={"max_retries": 5},
        )
        assert embedder.max_retries == 5

        # Default
        embedder2 = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="sk-test",
            dimension=10,
        )
        assert embedder2.max_retries == 3

    def test_openai_sdk_retry_disabled(self):
        """OpenAI client should be created with max_retries=0."""
        from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder

        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value = MagicMock()
            OpenAIDenseEmbedder(
                model_name="text-embedding-3-small",
                api_key="sk-test",
                dimension=10,
            )
            call_kwargs = mock_openai.call_args
            assert call_kwargs.kwargs.get("max_retries") == 0


# ---------------------------------------------------------------------------
# VikingDB Embedder Tests
# ---------------------------------------------------------------------------

class TestVikingDBEmbedderRetry:

    @pytest.fixture()
    def vikingdb_embedder(self):
        """Create a VikingDBDenseEmbedder with mocked client."""
        from openviking.models.embedder.vikingdb_embedders import VikingDBDenseEmbedder

        with patch(
            "openviking.storage.vectordb.collection.volcengine_clients.ClientForDataApi"
        ):
            embedder = VikingDBDenseEmbedder(
                model_name="test-model",
                model_version="1.0",
                ak="test-ak",
                sk="test-sk",
                region="cn-beijing",
                dimension=10,
                config={"max_retries": 2},
            )
        return embedder

    def test_embed_retries_on_transient_error(self, vikingdb_embedder):
        """embed() should retry on transient error and succeed."""
        errors = [_HttpError(503)]
        call_count = 0

        original_call_api = vikingdb_embedder._call_api

        def fake_call_api(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return [{"dense_embedding": [0.1] * 10}]

        vikingdb_embedder._call_api = fake_call_api

        with patch("time.sleep"):
            result = vikingdb_embedder.embed("test text")

        assert result.dense_vector == [0.1] * 10
        assert call_count == 2  # 1 failure + 1 success

    def test_embed_no_retry_on_permanent_error(self, vikingdb_embedder):
        """embed() should NOT retry on 401 (permanent)."""
        call_count = 0

        def fake_call_api(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise _HttpError(401, "Unauthorized")

        vikingdb_embedder._call_api = fake_call_api

        with patch("time.sleep"):
            with pytest.raises(_HttpError):
                vikingdb_embedder.embed("test text")

        assert call_count == 1  # no retries

    def test_uses_config_max_retries(self):
        """VikingDB embedder should use self.max_retries from config."""
        from openviking.models.embedder.vikingdb_embedders import VikingDBDenseEmbedder

        with patch(
            "openviking.storage.vectordb.collection.volcengine_clients.ClientForDataApi"
        ):
            embedder = VikingDBDenseEmbedder(
                model_name="test-model",
                model_version="1.0",
                ak="test-ak",
                sk="test-sk",
                region="cn-beijing",
                dimension=10,
                config={"max_retries": 7},
            )
        assert embedder.max_retries == 7

    def test_vikingdb_now_has_retry(self, vikingdb_embedder):
        """VikingDB embed() should retry on 429 (was zero retry before unified retry)."""
        errors = [_HttpError(429), _HttpError(429)]
        call_count = 0

        def fake_call_api(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return [{"dense_embedding": [0.2] * 10}]

        vikingdb_embedder._call_api = fake_call_api

        with patch("time.sleep"):
            result = vikingdb_embedder.embed("test text")

        assert result.dense_vector == [0.2] * 10
        assert call_count == 3  # 2 failures + 1 success
