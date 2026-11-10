# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Score recalled documents with a rerank model, falling back to their vector scores."""

import asyncio
import math
from dataclasses import dataclass
from typing import Any, List, Optional

from openviking.models.rerank import RerankBase
from openviking.utils.token_estimation import (
    estimate_text_tokens,
    truncate_text_to_token_budget,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RerankScores:
    """Per-document scores; ``reranked[i]`` is True when the model produced ``scores[i]``."""

    scores: List[float]
    reranked: List[bool]

    @property
    def model_used(self) -> bool:
        return any(self.reranked)


@dataclass(frozen=True)
class Reranker:
    """A rerank client bound to the rerank config values its callers apply."""

    client: RerankBase
    threshold: float = 0.0
    max_input_tokens: int = 0

    async def score(
        self, query: str, documents: List[str], fallback_scores: List[float]
    ) -> RerankScores:
        return await rerank_scores(
            self.client,
            query,
            documents,
            fallback_scores,
            max_input_tokens=self.max_input_tokens,
        )


def finite_score(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return score if math.isfinite(score) else default


async def rerank_scores(
    client: Optional[RerankBase],
    query: str,
    documents: List[str],
    fallback_scores: List[float],
    max_input_tokens: int = 0,
) -> RerankScores:
    """Return rerank scores, keeping the fallback score for every document the model skipped."""
    fallback = RerankScores(list(fallback_scores), [False] * len(fallback_scores))
    if not client or not documents:
        return fallback

    rerank_query = query
    rerank_documents = [
        (index, document) for index, document in enumerate(documents) if document.strip()
    ]
    if not rerank_documents:
        return fallback

    if max_input_tokens > 0:
        max_query_tokens = max_input_tokens * 3 // 4
        if estimate_text_tokens(query) > max_query_tokens:
            rerank_query = truncate_text_to_token_budget(query, max_query_tokens)
        document_tokens = max_input_tokens - estimate_text_tokens(rerank_query)
        rerank_documents = [
            (index, truncate_text_to_token_budget(document, document_tokens))
            for index, document in rerank_documents
        ]

    try:
        scores = await asyncio.to_thread(
            client.rerank_batch,
            rerank_query,
            [document for _, document in rerank_documents],
        )
    except Exception as e:
        logger.warning("[Rerank] Rerank failed, fallback to vector scores: %s", e)
        return fallback

    if not scores or len(scores) != len(rerank_documents):
        logger.warning("[Rerank] Invalid rerank result, fallback to vector scores")
        return fallback

    normalized_scores = list(fallback_scores)
    reranked = [False] * len(fallback_scores)
    for score, (index, _) in zip(scores, rerank_documents, strict=True):
        model_score = finite_score(score, default=math.nan)
        if math.isfinite(model_score):
            normalized_scores[index] = model_score
            reranked[index] = True
    return RerankScores(normalized_scores, reranked)
