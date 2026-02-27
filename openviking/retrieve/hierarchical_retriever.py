# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Hierarchical retriever for OpenViking.

Implements directory-based hierarchical retrieval with recursive search
and rerank-based relevance scoring.
"""

import heapq
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.memory_lifecycle import hotness_score
from openviking.server.identity import RequestContext, Role
from openviking.storage import VikingVectorIndexBackend
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.retrieve.types import (
    ContextType,
    MatchedContext,
    QueryResult,
    RelatedContext,
    TypedQuery,
)
from openviking_cli.utils.config import RerankConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RetrieverMode(str):
    THINKING = "thinking"
    QUICK = "quick"


class HierarchicalRetriever:
    """Hierarchical retriever with dense and sparse vector support."""

    MAX_CONVERGENCE_ROUNDS = 3  # Stop after multiple rounds with unchanged topk
    MAX_RELATIONS = 5  # Maximum relations per resource
    SCORE_PROPAGATION_ALPHA = 0.5  # Score propagation coefficient
    DIRECTORY_DOMINANCE_RATIO = 1.2  # Directory score must exceed max child score
    GLOBAL_SEARCH_TOPK = 3  # Global retrieval count
    HOTNESS_ALPHA = 0.2  # Weight for hotness score in final ranking (0 = disabled)

    def __init__(
        self,
        storage: VikingVectorIndexBackend,
        embedder: Optional[Any],
        rerank_config: Optional[RerankConfig] = None,
    ):
        """Initialize hierarchical retriever with rerank_config.

        Args:
            storage: VikingVectorIndexBackend instance
            embedder: Embedder instance (supports dense/sparse/hybrid)
            rerank_config: Rerank configuration (optional, will fallback to vector search only)
        """
        self.vector_store = storage
        self.embedder = embedder
        self.rerank_config = rerank_config

        # Use rerank threshold if available, otherwise use a default
        self.threshold = rerank_config.threshold if rerank_config else 0

        # Initialize rerank client only if config is available
        if rerank_config and rerank_config.is_available():
            # TODO: Support later - initialize RerankClient here
            self._rerank_client = None
            logger.info(
                f"[HierarchicalRetriever] Rerank config available, threshold={self.threshold}"
            )
        else:
            self._rerank_client = None
            logger.info(
                f"[HierarchicalRetriever] Rerank not configured, using vector search only with threshold={self.threshold}"
            )

    async def retrieve(
        self,
        query: TypedQuery,
        ctx: RequestContext,
        limit: int = 5,
        mode: RetrieverMode = RetrieverMode.THINKING,
        score_threshold: Optional[float] = None,
        score_gte: bool = False,
        scope_dsl: Optional[Dict[str, Any]] = None,
    ) -> QueryResult:
        """
        Execute hierarchical retrieval.

        Args:
            user: User ID (for permission filtering)
            score_threshold: Custom score threshold (overrides config)
            score_gte: True uses >=, False uses >
            grep_patterns: Keyword match pattern list
            scope_dsl: Additional scope constraints passed from public find/search filter
        """

        # Use custom threshold or default threshold
        effective_threshold = score_threshold if score_threshold is not None else self.threshold

        target_dirs = [d for d in (query.target_directories or []) if d]

        if not await self.vector_store.collection_exists_bound():
            logger.warning(
                "[RecursiveSearch] Collection %s does not exist",
                self.vector_store.collection_name,
            )
            return QueryResult(
                query=query,
                matched_contexts=[],
                searched_directories=[],
            )

        # Generate query vectors once to avoid duplicate embedding calls
        query_vector = None
        sparse_query_vector = None
        if self.embedder:
            result: EmbedResult = self.embedder.embed(query.query)
            query_vector = result.dense_vector
            sparse_query_vector = result.sparse_vector

        # Step 1: Determine starting directories based on target_directories or context_type
        if target_dirs:
            root_uris = target_dirs
        else:
            root_uris = self._get_root_uris_for_type(query.context_type, ctx=ctx)

        # Step 2: Global vector search to supplement starting points
        global_results = await self._global_vector_search(
            ctx=ctx,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            context_type=query.context_type.value if query.context_type else None,
            target_dirs=target_dirs,
            scope_dsl=scope_dsl,
            limit=self.GLOBAL_SEARCH_TOPK,
        )

        # Step 3: Merge starting points
        starting_points = self._merge_starting_points(query.query, root_uris, global_results)

        # Step 4: Recursive search
        candidates = await self._recursive_search(
            query=query.query,
            ctx=ctx,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            starting_points=starting_points,
            limit=limit,
            mode=mode,
            threshold=effective_threshold,
            score_gte=score_gte,
            context_type=query.context_type.value if query.context_type else None,
            target_dirs=target_dirs,
            scope_dsl=scope_dsl,
        )

        # Step 6: Convert results
        matched = await self._convert_to_matched_contexts(candidates, ctx=ctx)

        return QueryResult(
            query=query,
            matched_contexts=matched[:limit],
            searched_directories=root_uris,
        )

    async def _global_vector_search(
        self,
        ctx: RequestContext,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]],
        context_type: Optional[str],
        target_dirs: List[str],
        scope_dsl: Optional[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Global vector search to locate initial directories."""
        results = await self.vector_store.search_global_roots_in_tenant(
            ctx=ctx,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            context_type=context_type,
            target_directories=target_dirs,
            extra_filter=scope_dsl,
            limit=limit,
        )
        return results

    def _merge_starting_points(
        self,
        query: str,
        root_uris: List[str],
        global_results: List[Dict[str, Any]],
        mode: str = "thinking",
    ) -> List[Tuple[str, float]]:
        """Merge starting points.
        Returns:
            List of (uri, parent_score) tuples
        """
        points = []
        seen = set()

        # Results from global search
        docs = []
        if self._rerank_client and mode == RetrieverMode.THINKING:
            for r in global_results:
                # todo: multi-modal
                doc = r["abstract"]
                docs.append(doc)
            rerank_scores = self._rerank_client.rerank_batch(query, docs)
            for i, r in enumerate(global_results):
                points.append((r["uri"], rerank_scores[i]))
                seen.add(r["uri"])
        else:
            for r in global_results:
                points.append((r["uri"], r["_score"]))
                seen.add(r["uri"])

        # Root directories as starting points
        for uri in root_uris:
            if uri not in seen:
                points.append((uri, 0.0))
                seen.add(uri)

        return points

    async def _recursive_search(
        self,
        query: str,
        ctx: RequestContext,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]],
        starting_points: List[Tuple[str, float]],
        limit: int,
        mode: str,
        threshold: Optional[float] = None,
        score_gte: bool = False,
        context_type: Optional[str] = None,
        target_dirs: Optional[List[str]] = None,
        scope_dsl: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recursive search with directory priority return and score propagation.

        Args:
            threshold: Score threshold
            score_gte: True uses >=, False uses >
            grep_patterns: Keyword match patterns
            scope_dsl: Additional scope constraints from public find/search filter
        """
        # Use passed threshold or default threshold
        effective_threshold = threshold if threshold is not None else self.threshold

        def passes_threshold(score: float) -> bool:
            """Check if score passes threshold."""
            if score_gte:
                return score >= effective_threshold
            return score > effective_threshold

        sparse_query_vector = sparse_query_vector or None

        collected: List[Dict[str, Any]] = []  # Collected results (directories and leaves)
        dir_queue: List[tuple] = []  # Priority queue: (-score, uri)
        visited: set = set()
        prev_topk_uris: set = set()
        convergence_rounds = 0

        alpha = self.SCORE_PROPAGATION_ALPHA

        # Initialize: process starting points
        for uri, score in starting_points:
            heapq.heappush(dir_queue, (-score, uri))

        while dir_queue:
            temp_score, current_uri = heapq.heappop(dir_queue)
            current_score = -temp_score
            if current_uri in visited:
                continue
            visited.add(current_uri)
            logger.info(f"[RecursiveSearch] Entering URI: {current_uri}")

            pre_filter_limit = max(limit * 2, 20)

            results = await self.vector_store.search_children_in_tenant(
                ctx=ctx,
                parent_uri=current_uri,
                query_vector=query_vector,
                sparse_query_vector=sparse_query_vector,  # Pass sparse vector
                context_type=context_type,
                target_directories=target_dirs,
                extra_filter=scope_dsl,
                limit=pre_filter_limit,
            )

            if not results:
                continue

            query_scores = []
            if self._rerank_client and mode == RetrieverMode.THINKING:
                documents = []
                for r in results:
                    # todo: multi-modal
                    doc = r["abstract"]
                    documents.append(doc)

                rerank_scores = self._rerank_client.rerank_batch(query, documents)
                query_scores = rerank_scores
            else:
                for r in results:
                    query_scores.append(r.get("_score", 0))

            for r, score in zip(results, query_scores):
                uri = r.get("uri", "")
                final_score = (
                    alpha * score + (1 - alpha) * current_score if current_score else score
                )

                if not passes_threshold(final_score):
                    logger.debug(
                        f"[RecursiveSearch] URI {uri} score {final_score} did not pass threshold {effective_threshold}"
                    )
                    continue

                # Always collect results that pass threshold, even if already
                # visited as a directory starting point. The visited set only
                # prevents re-entering directories for child search.
                if not any(c.get("uri") == uri for c in collected):
                    r["_final_score"] = final_score
                    collected.append(r)
                    logger.debug(
                        f"[RecursiveSearch] Added URI: {uri} to candidates with score: {final_score}"
                    )

                if uri not in visited:
                    if r.get("level") == 2:
                        visited.add(uri)
                    else:
                        heapq.heappush(dir_queue, (-final_score, uri))

            # Convergence check
            current_topk = sorted(collected, key=lambda x: x.get("_final_score", 0), reverse=True)[
                :limit
            ]
            current_topk_uris = {c.get("uri", "") for c in current_topk}

            if current_topk_uris == prev_topk_uris and len(current_topk_uris) >= limit:
                convergence_rounds += 1

                if convergence_rounds >= self.MAX_CONVERGENCE_ROUNDS:
                    break
            else:
                convergence_rounds = 0
                prev_topk_uris = current_topk_uris

        collected.sort(key=lambda x: x.get("_final_score", 0), reverse=True)
        return collected[:limit]

    async def _convert_to_matched_contexts(
        self,
        candidates: List[Dict[str, Any]],
        ctx: RequestContext,
    ) -> List[MatchedContext]:
        """Convert candidate results to MatchedContext list.

        Blends semantic similarity with a hotness score derived from
        ``active_count`` and ``updated_at`` so that frequently-accessed,
        recently-updated contexts get a ranking boost.  The blend weight
        is controlled by ``HOTNESS_ALPHA`` (0 disables the boost).
        """
        results = []

        for c in candidates:
            # Read related contexts and get summaries
            relations = []
            if get_viking_fs():
                related_uris = await get_viking_fs().get_relations(c.get("uri", ""), ctx=ctx)
                if related_uris:
                    related_abstracts = await get_viking_fs().read_batch(
                        related_uris[: self.MAX_RELATIONS], level="l0", ctx=ctx
                    )
                    for uri in related_uris[: self.MAX_RELATIONS]:
                        abstract = related_abstracts.get(uri, "")
                        if abstract:
                            relations.append(RelatedContext(uri=uri, abstract=abstract))

            semantic_score = c.get("_final_score", c.get("_score", 0.0))

            # --- hotness boost ---
            updated_at_raw = c.get("updated_at")
            if isinstance(updated_at_raw, str):
                try:
                    updated_at_val = datetime.fromisoformat(updated_at_raw)
                except (ValueError, TypeError):
                    updated_at_val = None
            elif isinstance(updated_at_raw, datetime):
                updated_at_val = updated_at_raw
            else:
                updated_at_val = None

            h_score = hotness_score(
                active_count=c.get("active_count", 0),
                updated_at=updated_at_val,
            )

            alpha = self.HOTNESS_ALPHA
            final_score = (1 - alpha) * semantic_score + alpha * h_score

            results.append(
                MatchedContext(
                    uri=c.get("uri", ""),
                    context_type=ContextType(c["context_type"])
                    if c.get("context_type")
                    else ContextType.RESOURCE,
                    level=c.get("level", 2),
                    abstract=c.get("abstract", ""),
                    category=c.get("category", ""),
                    score=final_score,
                    relations=relations,
                )
            )

        # Re-sort by blended score so hotness boost can change ranking
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _get_root_uris_for_type(
        self, context_type: Optional[ContextType], ctx: Optional[RequestContext] = None
    ) -> List[str]:
        """Return starting directory URI list based on context_type and user context.

        When context_type is None, returns roots for all types.
        ROOT has no space, relies on global_vector_search without URI prefix filter.
        """
        if not ctx or ctx.role == Role.ROOT:
            return []

        user_space = ctx.user.user_space_name()
        agent_space = ctx.user.agent_space_name()
        if context_type is None:
            return [
                f"viking://user/{user_space}/memories",
                f"viking://agent/{agent_space}/memories",
                "viking://resources",
                f"viking://agent/{agent_space}/skills",
            ]
        elif context_type == ContextType.MEMORY:
            return [
                f"viking://user/{user_space}/memories",
                f"viking://agent/{agent_space}/memories",
            ]
        elif context_type == ContextType.RESOURCE:
            return ["viking://resources"]
        elif context_type == ContextType.SKILL:
            return [f"viking://agent/{agent_space}/skills"]
        return []
