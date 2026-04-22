# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory Deduplicator for OpenViking.

LLM-assisted deduplication with candidate-level skip/create/none decisions and
per-existing merge/delete actions.
"""

import asyncio
import copy
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from openviking.core.context import Context
from openviking.core.namespace import canonical_agent_root, canonical_user_root
from openviking.models.embedder.base import EmbedResult, embed_compat
from openviking.prompts import render_prompt
from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.telemetry import bind_telemetry_stage, get_current_telemetry
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
    query_vector: list[float] | None = None  # For batch-internal dedup tracking


class ClusterDecisionType(str, Enum):
    """Outcome of consolidating a cluster of existing memories."""

    KEEP_AND_MERGE = "keep_and_merge"
    KEEP_AND_DELETE = "keep_and_delete"
    ARCHIVE_ALL = "archive_all"
    KEEP_ALL = "keep_all"


@dataclass
class ClusterDecision:
    """LLM-decided ops over a cluster of existing similar memories.

    Distinct from DedupResult: there is no fresh candidate. All cluster
    members are already stored. Used by periodic consolidation (the
    janitor pass) to fold duplicates that escaped per-write dedup,
    resolve contradictions, or archive stale clusters.
    """

    decision: ClusterDecisionType
    cluster: List[Context]
    keeper_uri: str = ""
    merge_into: List[str] = None  # type: ignore[assignment]
    delete: List[str] = None  # type: ignore[assignment]
    archive: List[str] = None  # type: ignore[assignment]
    merged_content: str = ""
    merged_abstract: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.merge_into is None:
            self.merge_into = []
        if self.delete is None:
            self.delete = []
        if self.archive is None:
            self.archive = []


class MemoryDeduplicator:
    """Handles memory deduplication with LLM decision making."""

    SIMILARITY_THRESHOLD = 0.0  # Vector similarity threshold for pre-filtering
    MAX_PROMPT_SIMILAR_MEMORIES = 5  # Number of similar memories sent to LLM

    _USER_CATEGORIES = {"preferences", "entities", "events"}
    _AGENT_CATEGORIES = {"cases", "patterns", "tools", "skills"}

    @staticmethod
    def _category_uri_prefix(category: str, ctx: RequestContext) -> str:
        """Build category URI prefix with space segment."""
        if category in MemoryDeduplicator._USER_CATEGORIES:
            return f"{canonical_user_root(ctx)}/memories/{category}/"
        elif category in MemoryDeduplicator._AGENT_CATEGORIES:
            return f"{canonical_agent_root(ctx)}/memories/{category}/"
        return ""

    def __init__(
        self,
        vikingdb: VikingDBManager,
    ):
        """Initialize deduplicator."""
        self.vikingdb = vikingdb
        config = get_openviking_config()
        self.embedder = config.embedding.get_embedder()

    def _is_shutdown_in_progress(self) -> bool:
        """Whether dedup is running during storage shutdown."""
        return bool(getattr(self.vikingdb, "is_closing", False))

    async def deduplicate(
        self,
        candidate: CandidateMemory,
        ctx: RequestContext,
        *,
        batch_memories: list[tuple[list[float], Context]] | None = None,
        strict_errors: bool = False,
    ) -> DedupResult:
        """Decide how to handle a candidate memory."""
        # Step 1: Vector pre-filtering - find similar memories in same category
        similar_memories, query_vector = await self._find_similar_memories(
            candidate,
            ctx=ctx,
            batch_memories=batch_memories,
            strict_errors=strict_errors,
        )

        if not similar_memories:
            # No similar memories, create directly
            return DedupResult(
                decision=DedupDecision.CREATE,
                candidate=candidate,
                similar_memories=[],
                actions=[],
                reason="No similar memories found",
                query_vector=query_vector,
            )

        # Step 2: LLM decision
        decision, reason, actions = await self._llm_decision(candidate, similar_memories)

        return DedupResult(
            decision=decision,
            candidate=candidate,
            similar_memories=similar_memories,
            actions=None if decision == DedupDecision.SKIP else actions,
            reason=reason,
            query_vector=query_vector,
        )

    async def _find_similar_memories(
        self,
        candidate: CandidateMemory,
        ctx: RequestContext,
        *,
        batch_memories: list[tuple[list[float], Context]] | None = None,
        strict_errors: bool = False,
    ) -> tuple[list[Context], list[float]]:
        """Find similar existing memories using vector search.

        Returns (similar_memories, query_vector). query_vector is the candidate's
        embedding, returned so the caller can store it for batch-internal tracking.
        """
        telemetry = get_current_telemetry()
        query_vector: list[float] = []  # Initialize early for safe returns

        if self.vikingdb is None:
            if strict_errors:
                raise RuntimeError("Memory dedup requires VikingDBManager")
            return [], query_vector

        if not self.embedder:
            if strict_errors:
                raise RuntimeError("Memory dedup requires an embedder")
            return [], query_vector

        # Generate embedding for candidate
        query_text = f"{candidate.abstract} {candidate.content}"
        embed_result: EmbedResult = await embed_compat(self.embedder, query_text, is_query=True)
        query_vector = embed_result.dense_vector

        category_uri_prefix = self._category_uri_prefix(candidate.category.value, ctx)
        logger.debug(
            "Dedup prefilter candidate category=%s owner_space=%s uri_prefix=%s",
            candidate.category.value,
            None,
            category_uri_prefix,
        )

        try:
            # Search with memory-scope filter.
            results = await self.vikingdb.search_similar_memories(
                owner_space=None,
                category_uri_prefix=category_uri_prefix,
                query_vector=query_vector,
                limit=5,
                ctx=ctx,
            )
            telemetry.count("vector.searches", 1)
            telemetry.count("vector.scored", len(results))
            telemetry.count("vector.scanned", len(results))

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
                    telemetry.count("vector.passed", 1)
                    # Reconstruct Context object
                    context = Context.from_dict(result)
                    if context:
                        # Keep retrieval score for later destructive-action guardrails.
                        context.meta = {**(context.meta or {}), "_dedup_score": score}
                        similar.append(context)
            logger.debug("Dedup similar memories after threshold=%d", len(similar))

            # Include batch-internal memories that are similar (#687).
            # Shallow-copy to avoid mutating the original's meta while
            # preserving all fields (account_id, owner_space, etc.) needed
            # downstream if the LLM decides to MERGE into this memory.
            if batch_memories:
                seen_uris = {c.uri for c in similar}
                for batch_vec, batch_ctx in batch_memories:
                    if batch_ctx.uri in seen_uris:
                        continue
                    score = self._cosine_similarity(query_vector, batch_vec)
                    if score >= self.SIMILARITY_THRESHOLD:
                        ctx_copy = copy.copy(batch_ctx)
                        ctx_copy.meta = {**(batch_ctx.meta or {}), "_dedup_score": score}
                        similar.append(ctx_copy)

            return similar, query_vector

        except asyncio.CancelledError as e:
            if not self._is_shutdown_in_progress():
                raise
            logger.warning(f"Vector search cancelled during dedup prefilter: {e}")
            return [], query_vector
        except Exception as e:
            if strict_errors:
                raise RuntimeError(f"Memory dedup vector search failed: {e}") from e
            logger.warning(f"Vector search failed: {e}")
            return [], query_vector

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
            with bind_telemetry_stage("memory_extract"):
                response = await vlm.get_completion_async(prompt)
            logger.debug("Dedup LLM raw response: %s", response)
            data = parse_json_from_response(response) or {}
            logger.debug("Dedup LLM parsed payload: %s", data)
            return self._parse_decision_payload(data, similar_memories, candidate)

        except asyncio.CancelledError as e:
            if not self._is_shutdown_in_progress():
                raise
            logger.warning(f"LLM dedup decision cancelled: {e}")
            return DedupDecision.CREATE, f"LLM cancelled: {e}", []
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

        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        mag_a = sum(a * a for a in vec_a) ** 0.5
        mag_b = sum(b * b for b in vec_b) ** 0.5

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot / (mag_a * mag_b)

    async def consolidate_cluster(
        self,
        cluster: List[Context],
        scope_uri: str,
        scope_overview: str = "",
        cluster_contents: Optional[Dict[str, str]] = None,
    ) -> ClusterDecision:
        """Decide ops over a cluster of existing similar memories.

        Distinct from deduplicate(). deduplicate() takes one
        CandidateMemory plus N similar existing memories and decides
        skip/create/none plus per-existing merge/delete -- the write-path
        dedup pipeline. consolidate_cluster() takes N existing memories
        with no fresh candidate and decides which to keep, which to fold
        into the keeper, which to delete (only when fully invalidated),
        and which to archive. Used by the periodic consolidator over
        clusters that escaped per-write dedup.

        Reuses the VLM access pattern from _llm_decision but renders a
        different prompt template (compression.cluster_consolidate).

        Context.abstract is on the object; full content lives in the
        underlying file. Callers must pre-fetch content via
        viking_fs.read(uri) and pass via cluster_contents (uri -> body).
        Missing entries are sent as the abstract only.

        Args:
            cluster: Existing memories that belong to one cluster.
            scope_uri: URI of the scope being consolidated (for prompt context).
            scope_overview: Current .overview.md text or '' / '(none)'.
            cluster_contents: Optional uri -> body dict from viking_fs.read.

        Returns:
            ClusterDecision with the cluster and the LLM-chosen ops.
            Returns KEEP_ALL when LLM is unavailable or the cluster has
            fewer than 2 members (defensive no-op).
        """
        if len(cluster) < 2:
            return ClusterDecision(
                decision=ClusterDecisionType.KEEP_ALL,
                cluster=cluster,
                reason="Cluster has fewer than 2 members; no consolidation needed.",
            )

        vlm = get_openviking_config().vlm
        if not vlm or not vlm.is_available():
            return ClusterDecision(
                decision=ClusterDecisionType.KEEP_ALL,
                cluster=cluster,
                reason="LLM not available; defaulting to keep_all (conservative).",
            )

        contents = cluster_contents or {}
        formatted_members: List[str] = []
        for i, mem in enumerate(cluster):
            abstract = (
                getattr(mem, "abstract", "")
                or getattr(mem, "_abstract_cache", "")
                or (mem.meta or {}).get("abstract", "")
            )
            updated = getattr(mem, "updated_at", None)
            updated_text = updated.isoformat() if updated is not None else "n/a"
            active = getattr(mem, "active_count", 0) or 0
            body = contents.get(mem.uri, "")
            body_preview = body[:1000] + ("...[truncated]" if len(body) > 1000 else "")
            formatted_members.append(
                f"{i + 1}. uri={mem.uri}\n"
                f"   abstract={abstract}\n"
                f"   updated_at={updated_text}\n"
                f"   active_count={active}\n"
                f"   content={body_preview if body_preview else '(content not pre-fetched)'}"
            )

        prompt = render_prompt(
            "compression.cluster_consolidate",
            {
                "scope_uri": scope_uri,
                "scope_overview": scope_overview or "(none)",
                "cluster_members": "\n\n".join(formatted_members),
            },
        )

        try:
            from openviking_cli.utils.llm import parse_json_from_response

            with bind_telemetry_stage("memory_consolidate"):
                response = await vlm.get_completion_async(prompt)
            data = parse_json_from_response(response) or {}
            return self._parse_cluster_decision(data, cluster)
        except asyncio.CancelledError as e:
            if not self._is_shutdown_in_progress():
                raise
            logger.warning(f"Cluster consolidation LLM cancelled: {e}")
            return ClusterDecision(
                decision=ClusterDecisionType.KEEP_ALL,
                cluster=cluster,
                reason=f"LLM cancelled: {e}",
            )
        except Exception as e:
            logger.warning(f"Cluster consolidation LLM failed: {e}")
            return ClusterDecision(
                decision=ClusterDecisionType.KEEP_ALL,
                cluster=cluster,
                reason=f"LLM failed: {e}",
            )

    @staticmethod
    def _parse_cluster_decision(
        data: dict,
        cluster: List[Context],
    ) -> ClusterDecision:
        """Normalize LLM payload into a ClusterDecision.

        Defensive: unknown decision strings collapse to KEEP_ALL. URIs
        that are not members of the cluster are dropped from action
        lists. keeper_uri must be a cluster member or it falls back to
        the first member.
        """
        cluster_uris = {m.uri for m in cluster}
        decision_str = str(data.get("decision", "keep_all")).lower().strip()

        decision_map = {
            "keep_and_merge": ClusterDecisionType.KEEP_AND_MERGE,
            "keep_and_delete": ClusterDecisionType.KEEP_AND_DELETE,
            "archive_all": ClusterDecisionType.ARCHIVE_ALL,
            "keep_all": ClusterDecisionType.KEEP_ALL,
        }
        decision = decision_map.get(decision_str, ClusterDecisionType.KEEP_ALL)

        def _filter_uris(field: str) -> List[str]:
            raw = data.get(field, []) or []
            if not isinstance(raw, list):
                return []
            return [u for u in raw if isinstance(u, str) and u in cluster_uris]

        keeper_uri = str(data.get("keeper_uri", "") or "").strip()
        if keeper_uri and keeper_uri not in cluster_uris:
            keeper_uri = ""

        merge_into = _filter_uris("merge_into")
        delete = _filter_uris("delete")
        archive = _filter_uris("archive")

        if decision == ClusterDecisionType.ARCHIVE_ALL:
            keeper_uri = ""
            archive = list(cluster_uris)
            merge_into = []
            delete = []
        elif decision in (
            ClusterDecisionType.KEEP_AND_MERGE,
            ClusterDecisionType.KEEP_AND_DELETE,
        ):
            if not keeper_uri:
                keeper_uri = cluster[0].uri
            merge_into = [u for u in merge_into if u != keeper_uri]
            delete = [u for u in delete if u != keeper_uri]
        elif decision == ClusterDecisionType.KEEP_ALL:
            keeper_uri = ""
            merge_into = []
            delete = []
            archive = []

        return ClusterDecision(
            decision=decision,
            cluster=cluster,
            keeper_uri=keeper_uri,
            merge_into=merge_into,
            delete=delete,
            archive=archive,
            merged_content=str(data.get("merged_content", "") or ""),
            merged_abstract=str(data.get("merged_abstract", "") or ""),
            reason=str(data.get("reason", "") or ""),
        )
