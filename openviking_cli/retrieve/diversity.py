# Copyright (c) 2025 Beijing Volcano Engine Technology Ltd.
# SPDX-License-Identifier: AGPL-3.0

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DiversityStrategy = Literal["mmr", "group_limit", "combined"]
DiversityGroupBy = Literal["parent", "source_root"]


class DiversityOptions(BaseModel):
    """Request-scoped retrieval diversity settings."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    strategy: DiversityStrategy = "combined"
    relevance_weight: float = Field(default=0.7, ge=0.0, le=1.0, alias="lambda")
    group_by: DiversityGroupBy = "source_root"
    max_per_group: int = Field(default=2, ge=1, le=100)
    candidate_multiplier: int = Field(default=4, ge=1, le=10)
    similarity_threshold: float = Field(default=0.98, ge=0.8, le=1.0)

    def resolve_candidate_limit(self, limit: int) -> int:
        """Return the bounded candidate pool size for a public result limit."""
        return min(max(limit * self.candidate_multiplier, limit), 500)
