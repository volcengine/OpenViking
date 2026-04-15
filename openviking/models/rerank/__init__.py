# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Rerank models for OpenViking.

Provides rerank functionality for hierarchical retrieval with multiple provider support:
- vikingdb: VikingDB's native rerank service
- cohere: Cohere Rerank v3.5 API
- litellm: LiteLLM rerank (supports multiple providers)
- openai: OpenAI-compatible rerank API
"""

from openviking.models.rerank.base import RerankBase
from openviking.models.rerank.cohere_rerank import CohereRerankClient
from openviking.models.rerank.litellm_rerank import LiteLLMRerankClient
from openviking.models.rerank.openai_rerank import OpenAIRerankClient
from openviking.models.rerank.volcengine_rerank import RerankClient

__all__ = [
    "RerankBase",
    "RerankClient",
    "CohereRerankClient",
    "LiteLLMRerankClient",
    "OpenAIRerankClient",
]
