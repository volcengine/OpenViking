# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/stats endpoints."""

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class HotnessDistribution(BaseModel):
    """Memory-hotness bucket counts."""

    model_config = ConfigDict(extra="allow")

    cold: int = 0
    warm: int = 0
    hot: int = 0


class StalenessStats(BaseModel):
    """Staleness metrics emitted by the stats aggregator."""

    model_config = ConfigDict(extra="allow")

    not_accessed_7d: int = 0
    not_accessed_30d: int = 0
    oldest_memory_age_days: Optional[float] = None


class MemoryStats(BaseModel):
    """Result payload of ``GET /api/v1/stats/memories``.

    ``by_category`` is kept as a free dict because the category set can
    grow (see ``MEMORY_CATEGORIES``).
    """

    model_config = ConfigDict(extra="allow")

    total_memories: int = 0
    by_category: Dict[str, int] = Field(default_factory=dict)
    hotness_distribution: Optional[HotnessDistribution] = None
    staleness: Optional[StalenessStats] = None


class SessionExtractionStats(BaseModel):
    """Result payload of ``GET /api/v1/stats/sessions/{session_id}``."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    total_turns: int = 0
    memories_extracted: int = 0
    contexts_used: int = 0
    skills_used: int = 0


class LLMTokenUsage(BaseModel):
    """LLM token usage bucket."""

    model_config = ConfigDict(extra="allow")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class EmbeddingTokenUsage(BaseModel):
    """Embedding token usage bucket."""

    model_config = ConfigDict(extra="allow")

    total_tokens: int = 0


class TokenStats(BaseModel):
    """Result payload of ``GET /api/v1/stats/tokens``."""

    model_config = ConfigDict(extra="allow")

    total_tokens: int = 0
    llm: Optional[LLMTokenUsage] = None
    embedding: Optional[EmbeddingTokenUsage] = None
