# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pydantic import BaseModel, Field


class EntityLinkingConfig(BaseModel):
    """Optional local entity extraction and candidate ranking; no extra LLM."""

    enabled: bool = False
    nlp_model: str = Field(default="en_core_web_sm", min_length=1)
    weight: float = Field(default=0.5, ge=0, le=1)
    similarity_threshold: float = Field(default=0.5, ge=0, le=1)
    max_query_entities: int = Field(default=8, ge=1, le=32)
    max_memory_entities: int = Field(default=64, ge=1, le=256)
    max_entity_matches: int = Field(default=500, ge=1, le=1000)
    embedding_concurrency: int = Field(default=4, ge=1, le=32)
    timeout_s: float = Field(default=10.0, gt=0)


class RetrievalConfig(BaseModel):
    """Configuration for query planning and context assembly."""

    entity_linking: EntityLinkingConfig = Field(default_factory=EntityLinkingConfig)

    recall_intent_timeout_s: float = Field(
        default=5.0,
        gt=0.0,
        description="Timeout in seconds for optional context query expansion.",
    )
    recall_rewrite_timeout_s: float = Field(
        default=30.0,
        gt=0.0,
        description="Timeout in seconds for optional context digest rewriting.",
    )
    enable_intent: bool = Field(
        default=True,
        description=(
            "Whether search() loads session context and runs LLM intent analysis / query "
            "planning when session_id is present. false skips session load, "
            "get_context_for_search, and IntentAnalyzer — searches with the raw query only "
            "(same path as no-session search)."
        ),
    )
