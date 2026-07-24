# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pydantic import BaseModel, Field


class RetrievalConfig(BaseModel):
    """Configuration for retrieval ranking behavior."""

    hotness_alpha: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Weight for blending hotness into final retrieval scores. "
            "0 disables hotness boost; 1 uses only hotness."
        ),
    )
    score_propagation_alpha: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Weight for each child result's own score when blending with its parent score "
            "during hierarchical retrieval. 0 uses only the parent score; "
            "1 uses only the child score."
        ),
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

    model_config = {"extra": "forbid"}
