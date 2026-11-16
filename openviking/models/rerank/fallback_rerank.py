# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rerank client that tries a secondary provider when the primary fails."""

from typing import List, Optional

from openviking.models.rerank.base import RerankBase
from openviking.utils.token_estimation import (
    estimate_text_tokens,
    truncate_text_to_token_budget,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class FallbackRerankClient(RerankBase):
    """Run a fallback reranker after a failed or invalid primary result."""

    def __init__(
        self,
        primary: RerankBase,
        fallback: RerankBase,
        fallback_max_input_tokens: int = 0,
    ):
        super().__init__()
        self.primary = primary
        self.fallback = fallback
        self.fallback_max_input_tokens = fallback_max_input_tokens
        self.provider = f"{primary.provider}->{fallback.provider}"
        self.model_name = getattr(primary, "model_name", None) or "fallback-rerank"

    @staticmethod
    def _valid_scores(scores: Optional[List[float]], document_count: int) -> bool:
        return scores is not None and len(scores) == document_count

    @staticmethod
    def _fit_fallback_input(
        query: str, documents: List[str], max_input_tokens: int
    ) -> tuple[str, List[str]]:
        if max_input_tokens <= 0:
            return query, documents

        max_query_tokens = max_input_tokens * 3 // 4
        if estimate_text_tokens(query) > max_query_tokens:
            query = truncate_text_to_token_budget(query, max_query_tokens)

        document_tokens = max_input_tokens - estimate_text_tokens(query)
        if document_tokens <= 0:
            return query, [""] * len(documents)
        return query, [
            truncate_text_to_token_budget(document, document_tokens) for document in documents
        ]

    def rerank_batch(self, query: str, documents: List[str]) -> Optional[List[float]]:
        if not documents:
            return []

        try:
            scores = self.primary.rerank_batch(query, documents)
        except Exception as exc:
            logger.warning(
                "[FallbackRerank] Primary provider raised provider=%s error=%s",
                self.primary.provider,
                exc,
            )
        else:
            if self._valid_scores(scores, len(documents)):
                return scores

        logger.warning(
            "[FallbackRerank] Primary result unusable; trying fallback provider=%s primary=%s",
            self.fallback.provider,
            self.primary.provider,
        )
        fallback_query, fallback_documents = self._fit_fallback_input(
            query, documents, self.fallback_max_input_tokens
        )
        try:
            return self.fallback.rerank_batch(fallback_query, fallback_documents)
        except Exception as exc:
            logger.warning(
                "[FallbackRerank] Fallback provider raised provider=%s error=%s",
                self.fallback.provider,
                exc,
            )
            return None

    def close(self) -> None:
        for client in (self.primary, self.fallback):
            close = getattr(client, "close", None)
            if callable(close):
                close()
