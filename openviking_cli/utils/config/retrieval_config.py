# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pydantic import BaseModel, Field


class MemoryAssociationConfig(BaseModel):
    """Optional file-based cue links. Does not use the vector collection."""

    enabled: bool = False
    nlp_model: str = "en_core_web_sm"
    max_memory_cues: int = Field(default=64, ge=1, le=256)
    max_query_cues: int = Field(default=8, ge=1, le=64)
    max_cue_matches: int = Field(default=500, ge=1, le=10000)
    timeout_s: float = Field(default=10.0, gt=0)


class RetrievalConfig(BaseModel):
    """Configuration for query planning and context assembly."""

    memory_association: MemoryAssociationConfig = Field(default_factory=MemoryAssociationConfig)

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
