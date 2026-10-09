# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Global vector retrieval with optional reranking of the recalled candidates.
"""

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from openviking.core.context import ContextLevel
from openviking.core.retrieval_targets import default_target_directories
from openviking.core.retrieval_types import SearchType
from openviking.models.embedder.base import (
    EmbedResult,
    embed_compat,
    embedder_supports_multimodal,
)
from openviking.models.rerank import RerankClient
from openviking.retrieve.retrieval_stats import get_stats_collector
from openviking.server.identity import RequestContext
from openviking.storage.abstract_overview import AbstractOverviewFormatError, body_for_preview
from openviking.storage.expr import FilterExpr
from openviking.storage.vikingdb_manager import VikingDBManager, VikingDBManagerProxy
from openviking.telemetry import get_current_telemetry
from openviking.utils.tags import normalize_search_tags
from openviking.utils.time_decay import parse_duration_ms
from openviking.utils.token_estimation import (
    estimate_text_tokens,
    truncate_text_to_token_budget,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve.types import (
    ContextType,
    MatchedContext,
    QueryResult,
    TypedQuery,
)
from openviking_cli.utils.config import RerankConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RetrieverMode(str):
    THINKING = "thinking"
    QUICK = "quick"


class HierarchicalRetriever:
    """Global retriever with dense and sparse vector support."""

    RERANK_CANDIDATE_MULTIPLIER = 2
    LEVEL_URI_SUFFIX = {0: ".abstract.md", 1: ".overview.md"}

    def __init__(
        self,
        storage: VikingDBManager,
        embedder: Optional[Any],
        rerank_config: Optional[RerankConfig] = None,
        rerank_profiles: Optional[Dict[str, RerankConfig]] = None,
        rerank_routing: Optional[Dict[str, str]] = None,
    ):
        """Initialize retriever with rerank_config and optional rerank_profiles.

        Args:
            storage: VikingVectorIndexBackend instance
            embedder: Embedder instance (supports dense/sparse/hybrid)
            rerank_config: Legacy single rerank configuration
            rerank_profiles: Optional dictionary of named rerank profiles (e.g. 'light', 'heavy')
            rerank_routing: Optional routing rules mapping operations to profile names
        """
        self.vector_store = storage
        self.embedder = embedder
        self.rerank_config = rerank_config
        self.rerank_profiles = rerank_profiles or {}
        self.rerank_routing = rerank_routing or {}
        self.rerank_max_input_tokens = rerank_config.max_input_tokens if rerank_config else 0

        # Use rerank threshold if available, otherwise use a default
        self.threshold = rerank_config.threshold if rerank_config else 0

        # Initialize profile-specific clients
        self._rerank_clients: Dict[str, Any] = {}
        for name, p_cfg in self.rerank_profiles.items():
            if p_cfg and p_cfg.is_available():
                client = RerankClient.from_config(p_cfg)
                if client:
                    self._rerank_clients[name] = client
                    provider = p_cfg._effective_provider()
                    logger.info(
                        f"[HierarchicalRetriever] Rerank profile '{name}' enabled "
                        f"(provider={provider}), threshold={p_cfg.threshold}"
                    )

        # Initialize legacy rerank client — all providers go through unified dispatch
        if rerank_config and rerank_config.is_available():
            self._rerank_client = RerankClient.from_config(rerank_config)
            provider = rerank_config._effective_provider()
            logger.info(
                f"[HierarchicalRetriever] Rerank enabled "
                f"(provider={provider}), threshold={self.threshold}"
            )
        else:
            self._rerank_client = None
            if not self._rerank_clients:
                logger.info(
                    f"[HierarchicalRetriever] Rerank not configured, using vector search "
                    f"only with threshold={self.threshold}"
                )

    def _resolve_lane(self, ctx: Optional[RequestContext]) -> Optional[str]:
        """Resolve rerank lane from RequestContext or routing rules."""
        if not self._rerank_clients:
            return None
        candidate_lane = getattr(ctx, "rerank_lane", None) if ctx else None
        if candidate_lane and candidate_lane in self._rerank_clients:
            return candidate_lane
        # Fallback to default routing rule or first available profile
        default_lane = self.rerank_routing.get("default")
        if default_lane and default_lane in self._rerank_clients:
            return default_lane
        if "light" in self._rerank_clients:
            return "light"
        return next(iter(self._rerank_clients.keys()), None)

    def _get_lane_resources(
        self, lane: Optional[str]
    ) -> Tuple[Optional[Any], Optional[RerankConfig], float, int]:
        """Return (client, config, threshold, max_input_tokens) for the resolved lane."""
        if lane and lane in self._rerank_clients:
            client = self._rerank_clients[lane]
            cfg = self.rerank_profiles[lane]
            return client, cfg, cfg.threshold, cfg.max_input_tokens
        if self._rerank_client:
            return (
                self._rerank_client,
                self.rerank_config,
                self.threshold,
                self.rerank_max_input_tokens,
            )
        if self._rerank_clients:
            fallback = self._resolve_lane(None)
            if fallback and fallback in self._rerank_clients:
                client = self._rerank_clients[fallback]
                cfg = self.rerank_profiles[fallback]
                return client, cfg, cfg.threshold, cfg.max_input_tokens
        return None, None, self.threshold, self.rerank_max_input_tokens

    async def retrieve(
        self,
        query: TypedQuery,
        ctx: RequestContext,
        limit: int = 5,
        mode: Optional[RetrieverMode] = None,
        score_threshold: Optional[float] = None,
        score_gte: bool = False,
        scope_dsl: Optional[FilterExpr | Dict[str, Any]] = None,
        level: Optional[List[int]] = None,
        events_time_decay_protection: Optional[str] = None,
        request_now: Optional[datetime] = None,
        search_type: SearchType = "semantic",
    ) -> QueryResult:
        """
        Run one global vector search, then optionally rerank its candidates.

        Args:
            ctx: Request context used for tenant and permission filtering
            mode: QUICK uses vector scores; THINKING optionally reranks the recalled
                candidates. None selects THINKING when a reranker is configured.
            score_threshold: Custom score threshold (overrides config)
            score_gte: True uses >=, False uses >
            scope_dsl: Additional scope constraints passed from public find/search filter
            level: Optional result level filter (0=L0, 1=L1, 2=L2)
        """
        t0 = time.monotonic()
        telemetry = get_current_telemetry()
        effective_lane = self._resolve_lane(ctx)
        client, _, lane_thresh, lane_max_tokens = self._get_lane_resources(effective_lane)
        effective_threshold = self._resolve_threshold(score_threshold, lane_thresh)
        image_query = query.image_query
        if mode is None:
            mode = RetrieverMode.THINKING if client else RetrieverMode.QUICK
        use_rerank = (
            (mode == RetrieverMode.THINKING or bool(effective_lane))
            and client is not None
            and not image_query
        )
        decay_kwargs = {}
        if events_time_decay_protection is not None:
            parse_duration_ms(
                events_time_decay_protection, parameter_name="events_time_decay_protection"
            )
            decay_kwargs = {
                "events_time_decay_protection": events_time_decay_protection,
                "request_now": request_now or datetime.now(timezone.utc),
            }
        if image_query and level is None:
            level = [2]

        # 创建 proxy 包装器，绑定当前 ctx
        vector_proxy = VikingDBManagerProxy(self.vector_store, ctx)

        target_dirs = [d for d in (query.target_directories or []) if d]

        if not await vector_proxy.collection_exists_bound():
            logger.warning(
                "[HierarchicalRetriever] Collection %s does not exist",
                vector_proxy.collection_name,
            )
            return QueryResult(
                query=query,
                matched_contexts=[],
                searched_directories=[],
            )

        # Generate query vectors once to avoid duplicate embedding calls
        query_vector = None
        sparse_query_vector = None
        if search_type == "semantic" and self.embedder:
            # Hot path: the capability comes from the in-memory account config and
            # cached embedder resource (no I/O, no model call, nothing borrowed).
            if image_query and not await embedder_supports_multimodal(self.embedder):
                raise InvalidArgumentError("Image search requires a multimodal embedding model.")
            with telemetry.measure("search.embed_query"):
                embedding_input = getattr(query, "embedding_input", None) or query.query
                result: EmbedResult = await embed_compat(
                    self.embedder,
                    embedding_input,
                    is_query=True,
                )
                query_vector = result.dense_vector
                sparse_query_vector = result.sparse_vector

        # Report the effective search scope in the query result.
        if target_dirs:
            root_uris = target_dirs
        else:
            root_uris = default_target_directories(ctx, context_type=query.context_type)

        context_type = query.context_type.value if query.context_type else None
        if image_query and context_type is None:
            context_type = ContextType.RESOURCE.value

        search_limit = limit * self.RERANK_CANDIDATE_MULTIPLIER if use_rerank else limit
        with telemetry.measure("search.vector_retrieval"):
            if search_type == "keywords":
                vector_results = await vector_proxy.search_by_keywords_in_tenant(
                    query=query.query,
                    context_type=context_type,
                    target_directories=target_dirs,
                    extra_filter=scope_dsl,
                    level=level,
                    limit=search_limit,
                )
            else:
                vector_results = await vector_proxy.search_in_tenant(
                    query_vector=query_vector,
                    sparse_query_vector=sparse_query_vector,
                    context_type=context_type,
                    target_directories=target_dirs,
                    extra_filter=scope_dsl,
                    level=level,
                    limit=search_limit,
                    **decay_kwargs,
                )
        telemetry.count("vector.searches", 1)
        telemetry.count("vector.scored", len(vector_results))
        telemetry.count("vector.scanned", len(vector_results))

        # Recall scores already include event decay from the vector engine.
        # Keep the highest-scored hit for each URI before model reranking.
        collected_by_uri: Dict[str, Dict[str, Any]] = {}
        for result in vector_results:
            uri = result.get("uri", "")
            if not uri:
                continue
            score = self._finite_score(result.get("_score", 0.0))
            previous = collected_by_uri.get(uri)
            if previous is None or score > previous["_score"]:
                collected_by_uri[uri] = {**result, "_score": score}

        candidates = sorted(
            collected_by_uri.values(), key=lambda candidate: candidate["_score"], reverse=True
        )
        scores = [candidate["_score"] for candidate in candidates]
        rerank_used = use_rerank and bool(candidates)
        if rerank_used:
            scores = await self._rerank_scores(
                query.query,
                [str(candidate.get("abstract", "")) for candidate in candidates],
                scores,
                client=client,
                max_input_tokens=lane_max_tokens,
                lane=effective_lane,
            )
            lane_label = effective_lane or "default"
            telemetry.set("rerank.lane", lane_label)
            telemetry.count(f"rerank.{lane_label}.calls", 1)
            telemetry.count(f"rerank.{lane_label}.docs", len(candidates))

        # A low vector score can still rerank highly, so filter only after reranking.
        candidates = [
            {**candidate, "_final_score": score}
            for candidate, score in zip(candidates, scores, strict=True)
            if self._passes_threshold(score, effective_threshold, score_gte)
        ]
        telemetry.count("vector.passed", len(candidates))
        matched = await self._convert_to_matched_contexts(candidates, ctx=ctx)
        final = matched[:limit]

        elapsed_ms = (time.monotonic() - t0) * 1000
        get_stats_collector().record_query(
            context_type=context_type or "unknown",
            result_count=len(final),
            scores=[m.score for m in final],
            latency_ms=elapsed_ms,
            rerank_used=rerank_used,
            lane=effective_lane,
        )

        return QueryResult(
            query=query,
            matched_contexts=final,
            searched_directories=root_uris,
        )

    def _resolve_threshold(
        self, threshold: Optional[float], lane_threshold: Optional[float] = None
    ) -> float:
        if threshold is not None:
            return threshold
        if lane_threshold is not None:
            return lane_threshold
        return self.threshold if self.threshold is not None else 0.0

    @staticmethod
    def _finite_score(value: Any, default: float = 0.0) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return default
        return score if math.isfinite(score) else default

    @staticmethod
    def _passes_threshold(score: float, threshold: float, score_gte: bool) -> bool:
        if score_gte:
            return score >= threshold
        return score > threshold

    async def _rerank_scores(
        self,
        query: str,
        documents: List[str],
        fallback_scores: List[float],
        client: Optional[Any] = None,
        max_input_tokens: Optional[int] = None,
        lane: Optional[str] = None,
    ) -> List[float]:
        """Return rerank scores or fall back to vector scores."""
        rerank_client = client or self._rerank_client
        if not rerank_client or not documents:
            return fallback_scores

        input_token_limit = (
            max_input_tokens if max_input_tokens is not None else self.rerank_max_input_tokens
        )
        rerank_query = query
        rerank_documents = [
            (index, document) for index, document in enumerate(documents) if document.strip()
        ]
        if not rerank_documents:
            return fallback_scores

        if input_token_limit > 0:
            max_query_tokens = input_token_limit * 3 // 4
            if estimate_text_tokens(query) > max_query_tokens:
                rerank_query = truncate_text_to_token_budget(query, max_query_tokens)
            document_tokens = input_token_limit - estimate_text_tokens(rerank_query)
            rerank_documents = [
                (index, truncate_text_to_token_budget(document, document_tokens))
                for index, document in rerank_documents
            ]

        lane_label = lane or "default"
        try:
            scores = await asyncio.to_thread(
                rerank_client.rerank_batch,
                rerank_query,
                [document for _, document in rerank_documents],
            )
        except Exception as e:
            logger.warning(
                "[HierarchicalRetriever] Rerank failed (lane=%s), fallback to vector scores: %s",
                lane_label,
                e,
            )
            return fallback_scores

        if not scores or len(scores) != len(rerank_documents):
            logger.warning(
                "[HierarchicalRetriever] Invalid rerank result (lane=%s), "
                "fallback to vector scores",
                lane_label,
            )
            return fallback_scores

        model_name = getattr(rerank_client, "model_name", "unknown")
        logger.info(
            "[HierarchicalRetriever] Rerank completed (lane=%s, model=%s): %d docs",
            lane_label,
            model_name,
            len(rerank_documents),
        )

        normalized_scores = list(fallback_scores)
        for score, (index, _) in zip(scores, rerank_documents, strict=True):
            normalized_scores[index] = self._finite_score(score, fallback_scores[index])
        return normalized_scores

    async def _convert_to_matched_contexts(
        self,
        candidates: List[Dict[str, Any]],
        ctx: RequestContext,
    ) -> List[MatchedContext]:
        """Convert candidates to contexts ordered by vector or rerank score."""
        results = []
        for c in candidates:
            final_score = self._finite_score(c.get("_final_score", c.get("_score", 0.0)))
            level = c.get("level", 2)
            display_uri = self._append_level_suffix(c.get("uri", ""), level)
            abstract = c.get("abstract", "")
            if level in {ContextLevel.ABSTRACT, ContextLevel.OVERVIEW}:
                # New records persist body-only rerank scalars, but imported or
                # legacy indexes may still contain the full OKF document. Keep
                # the public find/search preview contract body-only at its final
                # conversion boundary. L2 user Markdown is intentionally left
                # untouched, including ordinary YAML frontmatter.
                try:
                    abstract = body_for_preview(abstract)
                except AbstractOverviewFormatError as exc:
                    logger.warning("Malformed sidecar in retrieval result %s: %s", display_uri, exc)
                    abstract = ""

            results.append(
                MatchedContext(
                    uri=display_uri,
                    context_type=ContextType(c["context_type"])
                    if c.get("context_type")
                    else ContextType.RESOURCE,
                    level=level,
                    abstract=abstract,
                    category=c.get("category", ""),
                    score=final_score,
                    search_tags=normalize_search_tags(c.get("search_tags"), discard_invalid=True),
                    origin_score=c.get("_origin_score"),
                    time_score=c.get("_time_score"),
                )
            )

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    @classmethod
    def _append_level_suffix(cls, uri: str, level: int) -> str:
        """Return user-facing URI with L0/L1 suffix reconstructed by level."""
        suffix = cls.LEVEL_URI_SUFFIX.get(level)
        if not uri or not suffix:
            return uri
        if uri.endswith(f"/{suffix}"):
            return uri
        if uri.endswith("/.abstract.md") or uri.endswith("/.overview.md"):
            return uri
        if uri.endswith("/") and not uri.endswith("://"):
            uri = uri.rstrip("/")
        return f"{uri}/{suffix}"
