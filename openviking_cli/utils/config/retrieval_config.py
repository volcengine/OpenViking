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
    graph_alpha: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Weight for blending graph connectivity into final retrieval scores. "
            "0 disables graph-aware scoring; higher values reward well-connected results."
        ),
    )
    graph_saturation_k: float = Field(
        default=15.0,
        ge=1.0,
        description=(
            "Controls the saturation point of the tanh mapping for graph_score. "
            "Lower values = faster saturation (fewer edges needed to reach max score). "
            "Used in graph_score = tanh(total_relations / graph_saturation_k)."
        ),
    )

    model_config = {"extra": "forbid"}
