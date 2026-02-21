# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory Deduplicator for OpenViking.

LLM-assisted deduplication with candidate-level skip/create/none decisions and
per-existing merge/delete actions.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from openviking.core.context import Context
from openviking.models.embedder.base import EmbedResult
from openviking.prompts import render_prompt
from openviking.storage import VikingDBManager
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

from .memory_extractor import CandidateMemory

logger = get_logger(__name__)


class DedupDecision(str, Enum):
    """Deduplication decision types."""

    SKIP = "skip"  # Duplicate, skip
    CREATE = "create"  # Create candidate memory
    NONE = "none"  # No candidate creation; resolve existing memories only


class MemoryActionDecision(str, Enum):
    """Decision for each existing memory candidate."""

    MERGE = "merge"  # Merge candidate into existing memory
    DELETE = "delete"  # Delete conflicting existing memory


@dataclass
class ExistingMemoryAction:
    """Decision for one existing memory."""

    memory: Context
    decision: MemoryActionDecision
    reason: str = ""


@dataclass
class DedupResult:
    """Result of deduplication decision."""

    decision: DedupDecision
    candidate: CandidateMemory
    similar_memories: List[Context]  # Similar existing memories
    actions: Optional[List[ExistingMemoryAction]] = None
    reason: str = ""


class MemoryDeduplicator:
    """Handles memory deduplication with LLM decision making."""

    SIMILARITY_THRESHOLD = 0.0  # Vector similarity threshold for pre-filtering
    MAX_PROMPT_SIMILAR_MEMORIES = 5  # Number of similar memories sent to LLM
    CATEGORY_URI_PREFIX = {
        "preferences": "viking://user/memories/preferences/",
        "entities": "viking://user/memories/entities/",
        "events": "viking://user/memories/events/",
        "cases": "viking://agent/memories/cases/",
        "patterns": "viking://agent/memories/patterns/",
    }

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
                actions=[],
                reason="No similar memories found",
            )

        # Step 2: LLM decision
        decision, reason, actions = await self._llm_decision(candidate, similar_memories)

        return DedupResult(
            decision=decision,
            candidate=candidate,
            similar_memories=similar_memories,
            actions=None if decision == DedupDecision.SKIP else actions,
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

        category_uri_prefix = self.CATEGORY_URI_PREFIX.get(candidate.category.value, "")

        # Build filter by memory scope + uri prefix (schema does not have category field yet).
        filter_conds = [
            {"field": "context_type", "op": "must", "conds": ["memory"]},
            {"field": "is_leaf", "op": "must", "conds": [True]},
        ]
        if category_uri_prefix:
            filter_conds.append({"field": "uri", "op": "prefix", "prefix": category_uri_prefix})
        dedup_filter = {"op": "and", "conds": filter_conds}
        logger.debug(
            "Dedup prefilter candidate category=%s filter=%s",
            candidate.category.value,
            dedup_filter,
        )

        try:
            # Search with memory-scope filter.
            results = await self.vikingdb.search(
                collection=collection,
                query_vector=query_vector,
                limit=5,
                filter=dedup_filter,
            )

            # Filter by similarity threshold
            similar = []
            logger.debug(
                "Dedup prefilter raw hits=%d threshold=%.2f",
                len(results),
                self.SIMILARITY_THRESHOLD,
            )
            for result in results:
                score = float(result.get("_score", result.get("score", 0)) or 0)
                logger.debug(
                    "Dedup hit score=%.4f uri=%s abstract=%s",
                    score,
                    result.get("uri", ""),
                    result.get("abstract", ""),
                )
                if score >= self.SIMILARITY_THRESHOLD:
                    # Reconstruct Context object
                    context = Context.from_dict(result)
                    if context:
                        # Keep retrieval score for later destructive-action guardrails.
                        context.meta = {**(context.meta or {}), "_dedup_score": score}
                        similar.append(context)
            logger.debug("Dedup similar memories after threshold=%d", len(similar))
            return similar

        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    async def _llm_decision(
        self,
        candidate: CandidateMemory,
        similar_memories: List[Context],
    ) -> tuple[DedupDecision, str, List[ExistingMemoryAction]]:
        """Use LLM to decide deduplication action."""
        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            # Without LLM, default to CREATE (conservative)
            return DedupDecision.CREATE, "LLM not available, defaulting to CREATE", []

        # Format existing memories for prompt
        existing_formatted = []
        for i, mem in enumerate(similar_memories[: self.MAX_PROMPT_SIMILAR_MEMORIES]):
            # Context.from_dict stores L0 summary on `mem.abstract`.
            # `_abstract_cache`/`meta["abstract"]` are optional and often empty.
            abstract = (
                getattr(mem, "abstract", "")
                or getattr(mem, "_abstract_cache", "")
                or (mem.meta or {}).get("abstract", "")
            )
            facet = self._extract_facet_key(abstract)
            score = mem.meta.get("_dedup_score")
            score_text = "n/a" if score is None else f"{float(score):.4f}"
            existing_formatted.append(
                f"{i + 1}. uri={mem.uri}\n   score={score_text}\n   facet={facet}\n   abstract={abstract}"
            )

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
            from openviking_cli.utils.llm import parse_json_from_response

            request_summary = {
                "candidate_abstract": candidate.abstract,
                "candidate_overview_len": len(candidate.overview or ""),
                "candidate_content_len": len(candidate.content or ""),
                "similar_count": len(similar_memories),
                "similar_items": [
                    {
                        "uri": mem.uri,
                        "abstract": getattr(mem, "abstract", "")
                        or getattr(mem, "_abstract_cache", "")
                        or (mem.meta or {}).get("abstract", ""),
                        "score": (mem.meta or {}).get("_dedup_score"),
                    }
                    for mem in similar_memories[: self.MAX_PROMPT_SIMILAR_MEMORIES]
                ],
            }
            logger.debug("Dedup LLM request summary: %s", request_summary)
            response = await vlm.get_completion_async(prompt)
            logger.debug("Dedup LLM raw response: %s", response)
            data = parse_json_from_response(response) or {}
            logger.debug("Dedup LLM parsed payload: %s", data)
            return self._parse_decision_payload(data, similar_memories, candidate)

        except Exception as e:
            logger.warning(f"LLM dedup decision failed: {e}")
            return DedupDecision.CREATE, f"LLM failed: {e}", []

    def _parse_decision_payload(
        self,
        data: dict,
        similar_memories: List[Context],
        candidate: Optional[CandidateMemory] = None,
    ) -> tuple[DedupDecision, str, List[ExistingMemoryAction]]:
        """Parse/normalize dedup payload from LLM."""
        decision_str = str(data.get("decision", "create")).lower().strip()
        reason = str(data.get("reason", "") or "")

        decision_map = {
            "skip": DedupDecision.SKIP,
            "create": DedupDecision.CREATE,
            "none": DedupDecision.NONE,
            # Backward compatibility: legacy candidate-level merge maps to none.
            "merge": DedupDecision.NONE,
        }
        decision = decision_map.get(decision_str, DedupDecision.CREATE)

        raw_actions = data.get("list", [])
        if not isinstance(raw_actions, list):
            raw_actions = []

        # Legacy response compatibility: {"decision":"merge"}.
        if decision_str == "merge" and not raw_actions and similar_memories:
            raw_actions = [
                {
                    "uri": similar_memories[0].uri,
                    "decide": "merge",
                    "reason": "Legacy candidate merge mapped to none",
                }
            ]
            if not reason:
                reason = "Legacy candidate merge mapped to none"

        action_map = {
            "merge": MemoryActionDecision.MERGE,
            "delete": MemoryActionDecision.DELETE,
        }
        similar_by_uri: Dict[str, Context] = {m.uri: m for m in similar_memories}
        actions: List[ExistingMemoryAction] = []
        seen: Dict[str, MemoryActionDecision] = {}

        for item in raw_actions:
            if not isinstance(item, dict):
                continue

            action_str = str(item.get("decide", "")).lower().strip()
            action = action_map.get(action_str)
            if not action:
                continue

            memory = None
            uri = item.get("uri")
            if isinstance(uri, str):
                memory = similar_by_uri.get(uri)

            # Tolerate index-based responses (1-based preferred, 0-based fallback).
            if memory is None:
                index = item.get("index")
                if isinstance(index, int):
                    if 1 <= index <= len(similar_memories):
                        memory = similar_memories[index - 1]
                    elif 0 <= index < len(similar_memories):
                        memory = similar_memories[index]

            if memory is None:
                continue

            previous = seen.get(memory.uri)
            if previous and previous != action:
                actions = [a for a in actions if a.memory.uri != memory.uri]
                seen.pop(memory.uri, None)
                logger.warning(f"Conflicting actions for memory {memory.uri}, dropping both")
                continue
            if previous == action:
                continue

            seen[memory.uri] = action
            actions.append(
                ExistingMemoryAction(
                    memory=memory,
                    decision=action,
                    reason=str(item.get("reason", "") or ""),
                )
            )

        # Rule: skip should never carry per-memory actions.
        if decision == DedupDecision.SKIP:
            return decision, reason, []

        has_merge_action = any(a.decision == MemoryActionDecision.MERGE for a in actions)

        # Rule: if any merge exists, ignore create and execute as none.
        if decision == DedupDecision.CREATE and has_merge_action:
            decision = DedupDecision.NONE
            reason = f"{reason} | normalized:create+merge->none".strip(" |")
            return decision, reason, actions

        # Rule: create can only carry delete actions (or empty list).
        if decision == DedupDecision.CREATE:
            actions = [a for a in actions if a.decision == MemoryActionDecision.DELETE]

        return decision, reason, actions

    @staticmethod
    def _extract_facet_key(text: str) -> str:
        """Extract normalized facet key from memory abstract (before separator)."""
        if not text:
            return ""

        normalized = " ".join(str(text).strip().split())
        # Prefer common separators used by extraction templates.
        for sep in ("：", ":", "-", "—"):
            if sep in normalized:
                left = normalized.split(sep, 1)[0].strip().lower()
                if left:
                    return left

        # Fallback: short leading phrase.
        m = re.match(r"^(.{1,24})\s", normalized.lower())
        if m:
            return m.group(1).strip()
        return normalized[:24].lower().strip()

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
