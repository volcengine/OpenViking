# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Diversity selection for vector-index search candidates."""

import math
from typing import List, Sequence

from pydantic import BaseModel, ConfigDict, Field


class VectorDiversityOptions(BaseModel):
    """Optional MMR settings evaluated inside the vector index layer."""

    model_config = ConfigDict(extra="forbid", strict=True)

    relevance_weight: float = Field(ge=0.0, le=1.0)
    candidate_multiplier: int = Field(default=4, ge=1, le=10)
    similarity_threshold: float = Field(default=0.98, ge=0.8, le=1.0)

    def candidate_limit(self, requested: int) -> int:
        """Bound selection cost and full-payload reads for deep or large result windows."""
        return min(requested * self.candidate_multiplier, 500)


def _validate_vector(vector: Sequence[float]) -> float:
    if not vector:
        raise ValueError("dense vectors must be non-empty")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        raise ValueError("dense vectors must have non-zero magnitude")
    return norm


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("dense vectors must have equal dimensions")
    left_norm = _validate_vector(left)
    right_norm = _validate_vector(right)
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
    return max(-1.0, min(1.0, similarity))


def select_diverse_indices(
    query_vector: Sequence[float],
    vectors: Sequence[Sequence[float]],
    limit: int,
    options: VectorDiversityOptions,
) -> List[int]:
    """Select stable MMR candidates ordered by the index's original relevance rank."""
    if limit <= 0 or not vectors:
        return []
    _validate_vector(query_vector)

    deduplicated: List[int] = []
    for candidate_index, vector in enumerate(vectors):
        _validate_vector(vector)
        if any(
            _cosine_similarity(vector, vectors[retained_index]) >= options.similarity_threshold
            for retained_index in deduplicated
        ):
            continue
        deduplicated.append(candidate_index)

    selected: List[int] = []
    remaining = list(deduplicated)
    while remaining and len(selected) < limit:
        best_index = None
        best_key = None
        for candidate_index in remaining:
            similarities = [
                _cosine_similarity(vectors[candidate_index], vectors[selected_index])
                for selected_index in selected
            ]
            maximum_similarity = max(similarities, default=0.0)
            relevance = _cosine_similarity(query_vector, vectors[candidate_index])
            mmr_score = (
                options.relevance_weight * relevance
                - (1.0 - options.relevance_weight) * maximum_similarity
            )
            ranking_key = (mmr_score, -candidate_index)
            if best_key is None or ranking_key > best_key:
                best_index = candidate_index
                best_key = ranking_key

        if best_index is None:
            break
        selected.append(best_index)
        remaining.remove(best_index)

    return selected
