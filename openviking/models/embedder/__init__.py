# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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
"""

from openviking.models.embedder.base import (
    CompositeHybridEmbedder,
    DenseEmbedderBase,
    EmbedderBase,
    EmbedResult,
    HybridEmbedderBase,
    SparseEmbedderBase,
)
from openviking.models.embedder.jina_embedders import JinaDenseEmbedder
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

__all__ = [
    # Base classes
    "EmbedResult",
    "EmbedderBase",
    "DenseEmbedderBase",
    "SparseEmbedderBase",
    "HybridEmbedderBase",
    "CompositeHybridEmbedder",
    # Jina AI implementations
    "JinaDenseEmbedder",
    # OpenAI implementations
    "OpenAIDenseEmbedder",
    # Volcengine implementations
    "VolcengineDenseEmbedder",
    "VolcengineSparseEmbedder",
    "VolcengineHybridEmbedder",
    # VikingDB implementations
    "VikingDBDenseEmbedder",
    "VikingDBSparseEmbedder",
    "VikingDBHybridEmbedder",
]
