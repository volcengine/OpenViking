# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory Deduplicator for OpenViking.

LLM-assisted deduplication with CREATE/MERGE/SKIP decisions
"""

from dataclasses import dataclass
from enum import Enum
from typing import List

from openviking.core.context import Context
from openviking.models.embedder.base import EmbedResult
from openviking.prompts import render_prompt
from openviking.storage import VikingDBManager
from openviking.utils import get_logger
from openviking.utils.config import get_openviking_config

from .memory_extractor import CandidateMemory

logger = get_logger(__name__)


class DedupDecision(str, Enum):
    """Deduplication decision types."""

    CREATE = "create"  # New memory, create directly
    MERGE = "merge"  # Merge with existing memories
    SKIP = "skip"  # Duplicate, skip


@dataclass
class DedupResult:
    """Result of deduplication decision."""

    decision: DedupDecision
    candidate: CandidateMemory
    similar_memories: List[Context]  # Similar existing memories
    reason: str = ""


class MemoryDeduplicator:
    """Handles memory deduplication with LLM decision making."""

    SIMILARITY_THRESHOLD = 0.7  # Vector similarity threshold for pre-filtering

    def __init__(
        self,
        vikingdb: VikingDBManager,
    ):
        """Initialize deduplicator."""
        self.vikingdb = vikingdb
        self.embedder = vikingdb.get_embedder()

    async def deduplicate(
        self,
        candidate: CandidateMemory,
    ) -> DedupResult:
        """Decide how to handle a candidate memory."""
        # Step 1: Vector pre-filtering - find similar memories in same category
        similar_memories = await self._find_similar_memories(candidate)

        if not similar_memories:
            # No similar memories, create directly
            return DedupResult(
                decision=DedupDecision.CREATE,
                candidate=candidate,
                similar_memories=[],
                reason="No similar memories found",
            )

        # Step 2: LLM decision
        decision, reason = await self._llm_decision(candidate, similar_memories)

        return DedupResult(
            decision=decision,
            candidate=candidate,
            similar_memories=similar_memories,
            reason=reason,
        )

    async def _find_similar_memories(
        self,
        candidate: CandidateMemory,
    ) -> List[Context]:
        """Find similar existing memories using vector search."""
        if not self.embedder:
            return []

        # Generate embedding for candidate
        query_text = f"{candidate.abstract} {candidate.content}"
        embed_result: EmbedResult = self.embedder.embed(query_text)
        query_vector = embed_result.dense_vector

        # Determine collection and filter based on category
        collection = "context"

        # Build category filter
        category_value = candidate.category.value

        try:
            # Search with category filter
            results = await self.vikingdb.search(
                collection=collection,
                query_vector=query_vector,
                limit=5,
                filter= {"op": "and", "conds": [{"field": "category", "op": "must", "conds": [category_value]}, {"field": "is_leaf", "op": "must", "conds": [True]}]},
            )

            # Filter by similarity threshold
            similar = []
            for result in results:
                if result.get("score", 0) >= self.SIMILARITY_THRESHOLD:
                    # Reconstruct Context object
                    context = Context.from_dict(result)
                    if context:
                        similar.append(context)
            return similar

        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    async def _llm_decision(
        self,
        candidate: CandidateMemory,
        similar_memories: List[Context],
    ) -> tuple[DedupDecision, str]:
        """Use LLM to decide deduplication action."""
        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            # Without LLM, default to CREATE (conservative)
            return DedupDecision.CREATE, "LLM not available, defaulting to CREATE"

        # Format existing memories for prompt
        existing_formatted = []
        for i, mem in enumerate(similar_memories[:3]):  # Max 3 for context
            abstract = mem._abstract_cache or mem.meta.get("abstract", "")
            existing_formatted.append(f"{i + 1}. {abstract}")

        prompt = render_prompt(
            "compression.dedup_decision",
            {
                "candidate_content": candidate.content,
                "candidate_abstract": candidate.abstract,
                "candidate_overview": candidate.overview,
                "existing_memories": "\n".join(existing_formatted),
            },
        )

        try:
            from openviking.utils.llm import parse_json_from_response

            response = await vlm.get_completion_async(prompt)
            data = parse_json_from_response(response) or {}

            decision_str = data.get("decision", "create").lower()
            reason = data.get("reason", "")

            # Map to enum
            decision_map = {
                "create": DedupDecision.CREATE,
                "merge": DedupDecision.MERGE,
                "skip": DedupDecision.SKIP,
            }
            decision = decision_map.get(decision_str, DedupDecision.CREATE)

            return decision, reason

        except Exception as e:
            logger.warning(f"LLM dedup decision failed: {e}")
            return DedupDecision.CREATE, f"LLM failed: {e}"

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(vec_a) != len(vec_b):
            return 0.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        mag_a = sum(a * a for a in vec_a) ** 0.5
        mag_b = sum(b * b for b in vec_b) ** 0.5

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot / (mag_a * mag_b)
