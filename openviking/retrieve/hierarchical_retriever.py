# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Hierarchical retriever for OpenViking.

Implements directory-based hierarchical retrieval with recursive search
and rerank-based relevance scoring.
"""

import heapq
from typing import Any, Dict, List, Optional, Tuple

from openviking.models.embedder.base import EmbedResult
from openviking.storage import VikingDBInterface
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

    def __init__(
        self,
        storage: VikingDBInterface,
        embedder: Optional[Any],
        rerank_config: Optional[RerankConfig] = None,
    ):
        """Initialize hierarchical retriever with rerank_config.

        Args:
            storage: VikingDBInterface instance
            embedder: Embedder instance (supports dense/sparse/hybrid)
            rerank_config: Rerank configuration (optional, will fallback to vector search only)
        """
        self.storage = storage
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
        limit: int = 5,
        mode: RetrieverMode = RetrieverMode.THINKING,
        score_threshold: Optional[float] = None,
        score_gte: bool = False,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> QueryResult:
        """
        Execute hierarchical retrieval.

        Args:
            user: User ID (for permission filtering)
            score_threshold: Custom score threshold (overrides config)
            score_gte: True uses >=, False uses >
            grep_patterns: Keyword match pattern list
            metadata_filter: Additional metadata filter conditions
        """

        # Use custom threshold or default threshold
        effective_threshold = score_threshold if score_threshold is not None else self.threshold

        collection = self._type_to_collection(query.context_type)

        target_dirs = [d for d in (query.target_directories or []) if d]

        # Create context_type filter
        type_filter = {"op": "must", "field": "context_type", "conds": [query.context_type.value]}

        # Merge all filters
        filters_to_merge = [type_filter]
        if target_dirs:
            target_filter = {
                "op": "or",
                "conds": [
                    {"op": "prefix", "field": "uri", "prefix": target_dir}
                    for target_dir in target_dirs
                ],
            }
            filters_to_merge.append(target_filter)
        if metadata_filter:
            filters_to_merge.append(metadata_filter)

        final_metadata_filter = {"op": "and", "conds": filters_to_merge}

        if not await self.storage.collection_exists(collection):
            logger.warning(f"[RecursiveSearch] Collection {collection} does not exist")
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
            root_uris = self._get_root_uris_for_type(query.context_type)

        # Step 2: Global vector search to supplement starting points
        global_results = await self._global_vector_search(
            collection=collection,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            limit=self.GLOBAL_SEARCH_TOPK,
            filter=final_metadata_filter,
        )

        # Step 3: Merge starting points
        starting_points = self._merge_starting_points(query.query, root_uris, global_results)

        # Step 4: Recursive search
        candidates = await self._recursive_search(
            query=query.query,
            collection=collection,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            starting_points=starting_points,
            limit=limit,
            mode=mode,
            threshold=effective_threshold,
            score_gte=score_gte,
            metadata_filter=final_metadata_filter,
        )

        # Step 6: Convert results
        matched = await self._convert_to_matched_contexts(candidates, query.context_type)

        return QueryResult(
            query=query,
            matched_contexts=matched[:limit],
            searched_directories=root_uris,
        )

    async def _global_vector_search(
        self,
        collection: str,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]],
        limit: int,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Global vector search to locate initial directories."""
        if not query_vector:
            return []
        sparse_query_vector = sparse_query_vector or {}

        global_filter = {
            "op": "and",
            "conds": [filter, {"op": "must", "field": "is_leaf", "conds": [False]}],
        }
        results = await self.storage.search(
            collection=collection,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=global_filter,
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
        collection: str,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]],
        starting_points: List[Tuple[str, float]],
        limit: int,
        mode: str,
        threshold: Optional[float] = None,
        score_gte: bool = False,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recursive search with directory priority return and score propagation.

        Args:
            threshold: Score threshold
            score_gte: True uses >=, False uses >
            grep_patterns: Keyword match patterns
            metadata_filter: Additional metadata filter conditions
        """
        # Use passed threshold or default threshold
        effective_threshold = threshold if threshold is not None else self.threshold

        def passes_threshold(score: float) -> bool:
            """Check if score passes threshold."""
            if score_gte:
                return score >= effective_threshold
            return score > effective_threshold

        def merge_filter(base_filter: Dict, extra_filter: Optional[Dict]) -> Dict:
            """Merge filter conditions."""
            if not extra_filter:
                return base_filter
            return {"op": "and", "conds": [base_filter, extra_filter]}

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

            results = await self.storage.search(
                collection=collection,
                query_vector=query_vector,
                sparse_query_vector=sparse_query_vector,  # Pass sparse vector
                filter=merge_filter(
                    {"op": "must", "field": "parent_uri", "conds": [current_uri]}, metadata_filter
                ),
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
                    if r.get("is_leaf"):
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
        context_type: ContextType,
    ) -> List[MatchedContext]:
        """Convert candidate results to MatchedContext list."""
        results = []

        for c in candidates:
            # Read related contexts and get summaries
            relations = []
            if get_viking_fs():
                related_uris = await get_viking_fs().get_relations(c.get("uri", ""))
                if related_uris:
                    related_abstracts = await get_viking_fs().read_batch(
                        related_uris[: self.MAX_RELATIONS], level="l0"
                    )
                    for uri in related_uris[: self.MAX_RELATIONS]:
                        abstract = related_abstracts.get(uri, "")
                        if abstract:
                            relations.append(RelatedContext(uri=uri, abstract=abstract))

            results.append(
                MatchedContext(
                    uri=c.get("uri", ""),
                    context_type=context_type,
                    is_leaf=c.get("is_leaf", False),
                    abstract=c.get("abstract", ""),
                    category=c.get("category", ""),
                    score=c.get("_final_score", c.get("_score", 0.0)),
                    relations=relations,
                )
            )

        return results

    def _get_root_uris_for_type(self, context_type: ContextType) -> List[str]:
        """Return starting directory URI list based on context_type."""
        if context_type == ContextType.MEMORY:
            return ["viking://user/memories", "viking://agent/memories"]
        elif context_type == ContextType.RESOURCE:
            return ["viking://resources"]
        elif context_type == ContextType.SKILL:
            return ["viking://agent/skills"]
        return []

    def _type_to_collection(self, context_type: ContextType) -> str:
        """
        Convert context type to collection name.
        """
        return "context"
