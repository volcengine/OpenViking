# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for query_instruction on OpenAI-compatible embedders.

Asymmetric embedding models (qwen3-embedding, bge, gte) expect an instruction
prepended to the query text itself. query_param cannot express that: it travels
as an API field, which those models never read.
"""

from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

INSTRUCTION = "Instruct: Given a web search query, retrieve relevant passages\nQuery: "


def _embedder(**kwargs) -> OpenAIDenseEmbedder:
    return OpenAIDenseEmbedder(
        model_name="qwen3-embedding",
        api_key="no-key",
        api_base="http://localhost:11434/v1",
        **kwargs,
    )


class TestQueryInstructionConfig:
    def test_field_accepts_value(self):
        config = EmbeddingModelConfig(
            model="qwen3-embedding",
            provider="ollama",
            api_base="http://localhost:11434/v1",
            query_instruction=INSTRUCTION,
        )
        assert config.query_instruction == INSTRUCTION

    def test_field_defaults_to_none(self):
        config = EmbeddingModelConfig(
            model="qwen3-embedding",
            provider="ollama",
            api_base="http://localhost:11434/v1",
        )
        assert config.query_instruction is None


class TestQueryInstructionApplied:
    def test_query_is_prefixed(self):
        embedder = _embedder(query_instruction=INSTRUCTION)
        kwargs = embedder._build_kwargs("what is a cable", is_query=True)
        assert kwargs["input"] == INSTRUCTION + "what is a cable"

    def test_document_is_not_prefixed(self):
        """Documents must stay unprefixed, or an existing index is invalidated."""
        embedder = _embedder(query_instruction=INSTRUCTION)
        kwargs = embedder._build_kwargs("a cable connects two ports", is_query=False)
        assert kwargs["input"] == "a cable connects two ports"

    def test_batched_queries_are_prefixed(self):
        embedder = _embedder(query_instruction=INSTRUCTION)
        kwargs = embedder._build_kwargs(["first", "second"], is_query=True)
        assert kwargs["input"] == [INSTRUCTION + "first", INSTRUCTION + "second"]

    def test_batched_documents_are_not_prefixed(self):
        embedder = _embedder(query_instruction=INSTRUCTION)
        kwargs = embedder._build_kwargs(["first", "second"], is_query=False)
        assert kwargs["input"] == ["first", "second"]

    def test_unset_leaves_query_unchanged(self):
        """Default behavior is unchanged for symmetric models."""
        embedder = _embedder()
        kwargs = embedder._build_kwargs("what is a cable", is_query=True)
        assert kwargs["input"] == "what is a cable"

    def test_non_text_input_is_untouched(self):
        """Multimodal parts carry no text to prefix."""
        embedder = _embedder(query_instruction=INSTRUCTION)
        parts = [{"type": "image_url", "image_url": {"url": "http://example/x.png"}}]
        kwargs = embedder._build_kwargs(parts, is_query=True)
        assert kwargs["input"] == parts
