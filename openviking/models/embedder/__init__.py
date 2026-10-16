# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Embedding providers with a shared dense / dense_sparse output contract."""

from openviking.models.embedder.base import (
    AllCredentialsFailedError,
    EmbedderBase,
    EmbedResult,
    FailoverEmbedder,
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
from openviking.models.embedder.retry_embedder import RetryEmbedder
from openviking.models.embedder.vikingdb_embedders import (
    VikingDBEmbedder,
)
from openviking.models.embedder.volcengine_embedders import (
    VolcengineEmbedder,
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
    "FailoverEmbedder",
    "RetryEmbedder",
    "AllCredentialsFailedError",
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
    "VolcengineEmbedder",
    # VikingDB implementations
    "VikingDBEmbedder",
]
