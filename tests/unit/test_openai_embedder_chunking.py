# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenAI Embedder text chunking functionality (Issue #616)

Uses importlib to load modules directly, avoiding the deep import chain
from openviking/__init__.py that requires the full build (C++ engine, litellm, etc.).
"""

import importlib.util
import math
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# -- Direct module loading to avoid openviking/__init__.py import chain ---
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_module_from_file(module_name: str, file_path: str):
    """Load a Python module directly from file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load base module first
_base_mod = _load_module_from_file(
    "openviking.models.embedder.base",
    os.path.join(_PROJECT_ROOT, "openviking", "models", "embedder", "base.py"),
)
EmbedResult = _base_mod.EmbedResult
EmbedderBase = _base_mod.EmbedderBase
DenseEmbedderBase = _base_mod.DenseEmbedderBase


DIMENSION = 8


def _make_mock_openai_client(dimension=DIMENSION):
    """Create a mock OpenAI client that returns deterministic embeddings."""
    mock_client = MagicMock()

    def fake_create(**kwargs):
        inp = kwargs["input"]
        if isinstance(inp, list):
            items = []
            for text in inp:
                vec = _deterministic_vector(text, dimension)
                item = MagicMock()
                item.embedding = vec
                items.append(item)
            resp = MagicMock()
            resp.data = items
            return resp
        else:
            vec = _deterministic_vector(inp, dimension)
            item = MagicMock()
            item.embedding = vec
            resp = MagicMock()
            resp.data = [item]
            return resp

    mock_client.embeddings.create.side_effect = fake_create
    return mock_client


def _deterministic_vector(text, dim):
    """Generate a simple deterministic vector from text for testing."""
    seed = sum(ord(c) for c in text) % 1000
    vec = [(seed + i) * 0.001 for i in range(dim)]
    # Normalize
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _load_openai_embedders():
    """Load the openai_embedders module with mocked dependencies."""
    return _load_module_from_file(
        "openviking.models.embedder.openai_embedders",
        os.path.join(_PROJECT_ROOT, "openviking", "models", "embedder", "openai_embedders.py"),
    )


@patch("openai.OpenAI")
class TestOpenAIEmbedderChunking:
    """Test cases for OpenAI embedder text chunking (Issue #616)"""

    def _make_embedder(self, mock_openai_class, max_tokens=100):
        """Helper to create a test embedder with a mocked client."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=DIMENSION,
        )

        # Override max_tokens for testing (lower limit to trigger chunking easily)
        type(embedder).max_tokens = property(lambda self: max_tokens)

        return embedder, mock_client

    def test_short_text_no_chunking(self, mock_openai_class):
        """Short text (< max_tokens) should embed normally without chunking."""
        embedder, mock_client = self._make_embedder(mock_openai_class, max_tokens=8000)
        result = embedder.embed("Hello world")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == DIMENSION
        assert mock_client.embeddings.create.call_count >= 1

    def test_oversized_text_triggers_chunking(self, mock_openai_class):
        """Text exceeding max_tokens should trigger chunking and return correct dimension."""
        embedder, mock_client = self._make_embedder(mock_openai_class, max_tokens=10)

        # Create text that will exceed 10 tokens (using fallback: len//3 > 10 means len > 30)
        long_text = "This is a sentence. " * 20  # ~400 chars -> ~133 estimated tokens

        result = embedder.embed(long_text)

        assert result.dense_vector is not None
        assert len(result.dense_vector) == DIMENSION
        # Multiple API calls should have been made (one per chunk + dimension detection)
        assert mock_client.embeddings.create.call_count > 2

    def test_very_long_text_chunking(self, mock_openai_class):
        """Text 10x over the limit should still chunk and return normally."""
        embedder, mock_client = self._make_embedder(mock_openai_class, max_tokens=10)

        # Very long text: ~3000 chars -> ~1000 estimated tokens (100x over limit of 10)
        very_long_text = "A" * 3000

        result = embedder.embed(very_long_text)

        assert result.dense_vector is not None
        assert len(result.dense_vector) == DIMENSION

    def test_empty_text_no_error(self, mock_openai_class):
        """Empty text should not raise an error."""
        embedder, _ = self._make_embedder(mock_openai_class, max_tokens=8000)
        result = embedder.embed("")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == DIMENSION

    def test_embed_batch_mixed_lengths(self, mock_openai_class):
        """embed_batch with mixed short and long texts should handle each correctly."""
        embedder, mock_client = self._make_embedder(mock_openai_class, max_tokens=10)

        short_text = "Hi"
        long_text = "This is a very long sentence. " * 20  # exceeds 10 tokens

        results = embedder.embed_batch([short_text, long_text])

        assert len(results) == 2
        for result in results:
            assert result.dense_vector is not None
            assert len(result.dense_vector) == DIMENSION

    def test_embed_batch_empty_list(self, mock_openai_class):
        """embed_batch with empty list should return empty list."""
        embedder, _ = self._make_embedder(mock_openai_class, max_tokens=8000)
        results = embedder.embed_batch([])
        assert results == []

    def test_merged_vector_is_normalized(self, mock_openai_class):
        """Merged vector from chunking should be L2 normalized."""
        embedder, _ = self._make_embedder(mock_openai_class, max_tokens=10)

        long_text = "Word " * 100  # definitely exceeds 10 tokens

        result = embedder.embed(long_text)
        assert result.dense_vector is not None

        # Check L2 norm is approximately 1.0
        norm = math.sqrt(sum(v * v for v in result.dense_vector))
        assert abs(norm - 1.0) < 1e-6

    def test_paragraph_splitting(self, mock_openai_class):
        """Text with paragraph breaks should be split by paragraphs first."""
        embedder, mock_client = self._make_embedder(mock_openai_class, max_tokens=10)

        # Create text with clear paragraph separators
        paragraphs = ["Paragraph one content."] * 5
        text_with_paragraphs = "\n\n".join(paragraphs)

        result = embedder.embed(text_with_paragraphs)
        assert result.dense_vector is not None
        assert len(result.dense_vector) == DIMENSION


@patch("openai.OpenAI")
class TestTiktokenFallback:
    """Test tiktoken fallback behavior."""

    def test_fallback_when_tiktoken_unavailable(self, mock_openai_class):
        """When tiktoken is not available, should fall back to character-based estimation."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=DIMENSION,
        )

        # Force tiktoken encoder to None to simulate unavailability
        embedder._tiktoken_enc = None

        # Character-based estimation: len("hello world") // 3 = 3
        estimated = embedder._estimate_tokens("hello world")
        assert estimated == len("hello world") // 3

    def test_estimate_tokens_with_tiktoken_encoder(self, mock_openai_class):
        """When tiktoken encoder is available, should use it for estimation."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=DIMENSION,
        )

        # If tiktoken is available, _tiktoken_enc should not be None
        # and estimation should use it
        if embedder._tiktoken_enc is not None:
            tokens = embedder._estimate_tokens("hello world")
            assert isinstance(tokens, int)
            assert tokens > 0


class TestBaseChunkText:
    """Test _chunk_text and helper methods on a concrete subclass."""

    def _make_simple_embedder(self, max_tokens=10):
        """Create a minimal concrete embedder for testing chunking logic."""

        class SimpleEmbedder(DenseEmbedderBase):
            @property
            def max_tokens(self_inner):
                return max_tokens

            def _estimate_tokens(self_inner, text):
                # Simple: 1 token per word
                return len(text.split())

            def embed(self_inner, text, is_query=False):
                dim = 4
                vec = [1.0 / dim] * dim
                return EmbedResult(dense_vector=vec)

            def _embed_single(self_inner, text, is_query=False):
                return self_inner.embed(text, is_query=is_query)

            def get_dimension(self_inner):
                return 4

        return SimpleEmbedder(model_name="test")

    def test_chunk_text_short(self):
        """Text within limit should return single chunk."""
        embedder = self._make_simple_embedder(max_tokens=100)
        chunks = embedder._chunk_text("hello world")
        assert chunks == ["hello world"]

    def test_chunk_text_paragraph_split(self):
        """Text with paragraphs should split by paragraph boundaries."""
        embedder = self._make_simple_embedder(max_tokens=5)
        text = "one two three\n\nfour five six"
        chunks = embedder._chunk_text(text)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert embedder._estimate_tokens(chunk) <= 5

    def test_chunk_text_sentence_split(self):
        """Text with sentences but no paragraphs should split by sentences."""
        embedder = self._make_simple_embedder(max_tokens=5)
        text = "One two three four. Five six seven eight."
        chunks = embedder._chunk_text(text)
        assert len(chunks) >= 2

    def test_chunk_text_fixed_length_fallback(self):
        """Text without good natural boundaries should fall back to fixed-length split."""
        embedder = self._make_simple_embedder(max_tokens=3)
        # Use a long text with no sentence/paragraph boundaries, >100 chars to exceed min chunk_size
        text = "word " * 200  # 200 words = 200 tokens, 1000 chars
        chunks = embedder._chunk_text(text)
        assert len(chunks) >= 2

    def test_chunk_and_embed_returns_normalized(self):
        """_chunk_and_embed should return a normalized vector."""
        embedder = self._make_simple_embedder(max_tokens=3)
        text = "one two three four five six seven eight"
        result = embedder._chunk_and_embed(text)

        assert result.dense_vector is not None
        norm = math.sqrt(sum(v * v for v in result.dense_vector))
        assert abs(norm - 1.0) < 1e-6

    def test_chunk_and_embed_short_text_no_split(self):
        """_chunk_and_embed on short text should just call _embed_single."""
        embedder = self._make_simple_embedder(max_tokens=100)
        result = embedder._chunk_and_embed("hello")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == 4


@patch("openai.OpenAI")
class TestMaxTokensConfigurable:
    """Test cases for configurable max_tokens parameter."""

    def test_custom_max_tokens_via_constructor(self, mock_openai_class):
        """Custom max_tokens passed to OpenAIDenseEmbedder should be used."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=DIMENSION,
            max_tokens=32000,
        )

        assert embedder.max_tokens == 32000

    def test_default_max_tokens_when_not_provided(self, mock_openai_class):
        """When max_tokens is not provided, should default to 8000."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=DIMENSION,
        )

        assert embedder.max_tokens == 8000

    def test_custom_max_tokens_affects_chunking(self, mock_openai_class):
        """A higher max_tokens should allow longer texts without chunking."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        # Create embedder with high max_tokens
        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=DIMENSION,
            max_tokens=100000,
        )
        # Force fallback estimation (len//3)
        embedder._tiktoken_enc = None

        # This text would exceed 8000 tokens with fallback estimation (30000 chars -> 10000 tokens)
        # but should NOT trigger chunking with max_tokens=100000
        long_text = "word " * 6000  # 30000 chars -> 10000 estimated tokens

        mock_client.embeddings.create.reset_mock()
        result = embedder.embed(long_text)

        assert result.dense_vector is not None
        # Should be a single API call (plus dimension detection), NOT chunked
        assert mock_client.embeddings.create.call_count == 1

    def test_low_custom_max_tokens_triggers_chunking(self, mock_openai_class):
        """A lower max_tokens should trigger chunking for shorter texts."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=DIMENSION,
            max_tokens=5,
        )
        # Force fallback estimation
        embedder._tiktoken_enc = None

        # Need text long enough to produce multiple chunks.
        # Fallback estimation: len(text)//3. With max_tokens=5, need >5 tokens.
        # Fixed-length split has min chunk_size=100, so text must be >100 chars to split.
        text = (
            "Hello world test. " * 30
        )  # 540 chars -> 180 estimated tokens, well over max_tokens=5

        mock_client.embeddings.create.reset_mock()
        result = embedder.embed(text)

        assert result.dense_vector is not None
        # Should have chunked (multiple API calls)
        assert mock_client.embeddings.create.call_count > 1


class TestBaseMaxTokensConfigurable:
    """Test configurable max_tokens on EmbedderBase directly."""

    def test_base_default_max_tokens(self):
        """EmbedderBase without max_tokens should return 8000."""

        class ConcreteEmbedder(DenseEmbedderBase):
            def embed(self, text, is_query=False):
                return EmbedResult(dense_vector=[0.0])

            def get_dimension(self):
                return 1

        embedder = ConcreteEmbedder(model_name="test")
        assert embedder.max_tokens == 8000

    def test_base_custom_max_tokens(self):
        """EmbedderBase with max_tokens should return the custom value."""

        class ConcreteEmbedder(DenseEmbedderBase):
            def embed(self, text, is_query=False):
                return EmbedResult(dense_vector=[0.0])

            def get_dimension(self):
                return 1

        embedder = ConcreteEmbedder(model_name="test", max_tokens=32768)
        assert embedder.max_tokens == 32768


@patch("openai.OpenAI")
class TestOpenAIEmbedderWithoutApiKey:
    """Test cases for OpenAI embedder without api_key (local servers)."""

    def test_init_with_api_base_no_api_key(self, mock_openai_class):
        """OpenAIDenseEmbedder should initialize with api_base but no api_key."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        # Should NOT raise when api_base is set but api_key is not
        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_base="http://localhost:11434/v1",
            dimension=DIMENSION,
        )

        assert embedder is not None
        # Verify OpenAI client was initialized with placeholder key
        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs["api_key"] == "no-key"
        assert call_kwargs["base_url"] == "http://localhost:11434/v1"

    def test_init_without_api_key_or_api_base_raises(self, mock_openai_class):
        """OpenAIDenseEmbedder should raise when neither api_key nor api_base is set."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        # Should raise when neither is provided
        with pytest.raises(ValueError, match="api_key is required"):
            OpenAIDenseEmbedder(
                model_name="text-embedding-3-small",
                dimension=DIMENSION,
            )

    def test_init_with_api_key_works_normally(self, mock_openai_class):
        """OpenAIDenseEmbedder should work normally with api_key provided."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=DIMENSION,
        )

        assert embedder is not None
        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs["api_key"] == "test-api-key"

    def test_embed_with_api_base_no_api_key(self, mock_openai_class):
        """Embedding should work with api_base but no api_key (local server)."""
        mock_client = _make_mock_openai_client()
        mock_openai_class.return_value = mock_client

        mod = _load_openai_embedders()
        OpenAIDenseEmbedder = mod.OpenAIDenseEmbedder

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_base="http://localhost:11434/v1",
            dimension=DIMENSION,
        )

        result = embedder.embed("Hello world")

        assert result.dense_vector is not None
        assert len(result.dense_vector) == DIMENSION
        mock_client.embeddings.create.assert_called()
