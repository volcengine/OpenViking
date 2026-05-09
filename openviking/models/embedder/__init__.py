# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
OpenViking Embedder Module

Provides three embedder abstractions:
- DenseEmbedderBase: Returns dense vectors
- SparseEmbedderBase: Returns sparse vectors
- HybridEmbedderBase: Returns both dense and sparse vectors

Supported providers:
- OpenAI: Dense only
- Volcengine: Dense, Sparse, Hybrid
- Jina AI: Dense only
- Voyage AI: Dense only
- Cohere: Dense only
- Google Gemini: Dense only
- LiteLLM: Dense only (bridges to OpenRouter, Ollama, vLLM, and many others)
"""

from openviking.models.embedder.base import (
    CompositeHybridEmbedder,
    DenseEmbedderBase,
    EmbedderBase,
    EmbedResult,
    HybridEmbedderBase,
    SparseEmbedderBase,
)
from openviking.models.embedder.cohere_embedders import CohereDenseEmbedder
from openviking.models.embedder.dashscope_embedders import DashScopeDenseEmbedder

try:
    from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
except ImportError:
    GeminiDenseEmbedder = None  # google-genai not installed
from openviking.models.embedder.jina_embedders import JinaDenseEmbedder
from openviking.models.embedder.local_embedders import LocalDenseEmbedder

try:
    from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder
except ImportError:
    LiteLLMDenseEmbedder = None  # litellm not installed
from openviking.models.embedder.minimax_embedders import MinimaxDenseEmbedder
from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
from openviking.models.embedder.vikingdb_embedders import (
    VikingDBDenseEmbedder,
    VikingDBHybridEmbedder,
    VikingDBSparseEmbedder,
)
from openviking.models.embedder.volcengine_embedders import (
    VolcengineDenseEmbedder,
    VolcengineHybridEmbedder,
    VolcengineSparseEmbedder,
)
from openviking.models.embedder.voyage_embedders import VoyageDenseEmbedder

__all__ = [
    # Cohere implementations
    "CohereDenseEmbedder",
    # DashScope implementations
    "DashScopeDenseEmbedder",
    # Base classes
    "EmbedResult",
    "EmbedderBase",
    "DenseEmbedderBase",
    "SparseEmbedderBase",
    "HybridEmbedderBase",
    "CompositeHybridEmbedder",
    # Google Gemini implementations
    "GeminiDenseEmbedder",
    # Jina AI implementations
    "JinaDenseEmbedder",
    "LocalDenseEmbedder",
    # LiteLLM implementations
    "LiteLLMDenseEmbedder",
    # MiniMax implementations
    "MinimaxDenseEmbedder",
    # OpenAI implementations
    "OpenAIDenseEmbedder",
    # Voyage implementations
    "VoyageDenseEmbedder",
    # Volcengine implementations
    "VolcengineDenseEmbedder",
    "VolcengineSparseEmbedder",
    "VolcengineHybridEmbedder",
    # VikingDB implementations
    "VikingDBDenseEmbedder",
    "VikingDBSparseEmbedder",
    "VikingDBHybridEmbedder",
]
