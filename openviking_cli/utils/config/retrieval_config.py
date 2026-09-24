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
            "planning with session context or an explicitly configured query_planner. "
            "false skips session load, "
            "get_context_for_search, and IntentAnalyzer — searches with the raw query only "
            "without invoking the planner."
        ),
    )
    query_rewrite_only: bool = Field(
        default=False,
        description=(
            "Use only query text from the search planner, ignoring its context type, "
            "intent and priority. Retrieval remains constrained by caller targets and "
            "filters. Requires enable_intent and a planner invocation."
        ),
    )
