# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Hierarchical retriever for OpenViking.

Implements directory-based hierarchical retrieval with recursive search
and rerank-based relevance scoring.
"""

import asyncio
import heapq
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from openviking.core.retrieval_targets import default_target_directories
from openviking.models.embedder.base import EmbedResult, embed_compat
from openviking.models.rerank import RerankClient
from openviking.retrieve.memory_lifecycle import hotness_score
from openviking.retrieve.retrieval_stats import get_stats_collector
from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager, VikingDBManagerProxy
from openviking.storage.expr import FilterExpr
from openviking.telemetry import get_current_telemetry
from openviking.utils.time_utils import parse_iso_datetime
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
from openviking_cli.utils.config import RerankConfig, RetrievalConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_INTERNAL_SESSION_LOG_FILENAMES = frozenset({"messages.jsonl"})
_INTERNAL_SESSION_SIDECAR_FILENAMES = frozenset({".abstract.md", ".overview.md"})
_SESSION_LOG_FILTER_MAX_SCAN_PAGES = 8
_SESSION_URI_MAX_LENGTH = 4096
_SESSION_URI_MAX_DECODE_PASSES = 16


@dataclass(frozen=True)
class _SessionLogClassification:
    is_internal: bool = False
    incomplete: bool = False

    @property
    def should_exclude(self) -> bool:
        return self.is_internal or self.incomplete


@dataclass
class _SessionLogReplacementBudget:
    remaining: int

    def claim(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _new_session_log_replacement_budget() -> _SessionLogReplacementBudget:
    # The first page preserves the pre-filter search call. Only replacement
    # pages consume the shared request budget.
    return _SessionLogReplacementBudget(remaining=max(0, _SESSION_LOG_FILTER_MAX_SCAN_PAGES - 1))


class RetrieverMode(str):
    THINKING = "thinking"
    QUICK = "quick"


def _session_root_depth(parts: List[str], user_id: Optional[str]) -> Optional[int]:
    if len(parts) >= 2 and parts[0] == "session":
        return 2
    if not parts or parts[0] != "user":
        return None

    is_short = len(parts) >= 3 and parts[1] == "sessions"
    is_canonical = len(parts) >= 4 and parts[2] == "sessions"
    if is_short and is_canonical:
        normalized_user_id = user_id.casefold() if isinstance(user_id, str) else None
        # A user literally named "sessions" makes the short and canonical
        # grammars overlap. Retrieval has the request identity, so use it.
        # Without identity, prefer the canonical shape used by stored records.
        return 4 if normalized_user_id in (None, parts[1]) else 3
    if is_canonical:
        return 4
    if is_short:
        return 3
    return None


def _session_uri_tail(uri: str, user_id: Optional[str] = None) -> tuple[Optional[List[str]], bool]:
    """Return the tail below one authoritative session root and parse status."""
    scheme = "viking://"
    if len(uri) < len(scheme) or uri[: len(scheme)].casefold() != scheme:
        return None, False

    # Find URI delimiters only inside the bounded path prefix. Percent-encoded
    # '?' and '#' remain path data after decoding.
    scan_end = min(len(uri), _SESSION_URI_MAX_LENGTH)
    delimiter_positions = [
        position
        for position in (
            uri.find("?", len(scheme), scan_end),
            uri.find("#", len(scheme), scan_end),
        )
        if position >= 0
    ]
    path_end = min(delimiter_positions) if delimiter_positions else scan_end
    path_complete = bool(delimiter_positions) or len(uri) <= _SESSION_URI_MAX_LENGTH
    path = uri[len(scheme) : path_end].rstrip("/")

    for _ in range(_SESSION_URI_MAX_DECODE_PASSES):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    else:
        path_complete = False

    # Split decoded path data directly. Passing it back through a URI parser
    # would reinterpret encoded '?' or '#' bytes as query/fragment delimiters.
    parts = [part for part in path.strip("/").casefold().split("/") if part]
    root_depth = _session_root_depth(parts, user_id)
    return (parts[root_depth:] if root_depth is not None else None), not path_complete


def _classify_session_log_uri(uri: str, user_id: Optional[str] = None) -> _SessionLogClassification:
    tail, incomplete = _session_uri_tail(uri, user_id)
    if incomplete:
        # A recognizable session root fails closed. Other overlong/overencoded
        # URIs are indeterminate and are excluded by should_exclude as well.
        return _SessionLogClassification(
            is_internal=tail is not None,
            incomplete=True,
        )
    return _SessionLogClassification(
        is_internal=bool(tail is not None and _is_internal_session_log_tail(tail))
    )


def _is_internal_session_log_uri(uri: str, user_id: Optional[str] = None) -> bool:
    """Return whether a URI names a session transcript or its generated sidecar."""
    return _classify_session_log_uri(uri, user_id).is_internal


def _is_internal_session_log_tail(tail: List[str]) -> bool:
    if tail in (["messages.jsonl"], [".abstract.md"], [".overview.md"]):
        return True
    if (
        len(tail) == 2
        and tail[0] in _INTERNAL_SESSION_LOG_FILENAMES
        and tail[1] in _INTERNAL_SESSION_SIDECAR_FILENAMES
    ):
        return True
    if len(tail) >= 3 and tail[0] == "history" and tail[1].startswith("archive_"):
        archive_tail = tail[2:]
        return archive_tail in (
            ["messages.jsonl"],
            [".abstract.md"],
            [".overview.md"],
            ["messages.jsonl", ".abstract.md"],
            ["messages.jsonl", ".overview.md"],
        )
    return False


def _classify_session_log_result(
    result: Dict[str, Any], user_id: Optional[str] = None
) -> _SessionLogClassification:
    """Also catch L0/L1 sidecars represented as a base URI plus a level."""
    uri = str(result.get("uri", ""))
    classification = _classify_session_log_uri(uri, user_id)
    if classification.should_exclude:
        return classification
    try:
        level = int(result.get("level", 2))
    except (TypeError, ValueError):
        return classification
    if level not in (0, 1):
        return classification
    tail, _ = _session_uri_tail(uri, user_id)
    return _SessionLogClassification(
        is_internal=bool(
            tail == []
            or (
                tail is not None
                and len(tail) == 2
                and tail[0] == "history"
                and tail[1].startswith("archive_")
            )
        )
    )


def _is_internal_session_log_result(result: Dict[str, Any], user_id: Optional[str] = None) -> bool:
    return _classify_session_log_result(result, user_id).is_internal


async def _search_in_tenant_excluding_session_logs(
    vector_proxy: VikingDBManagerProxy,
    *,
    desired_limit: int,
    page_limit: int,
    query_vector: Optional[List[float]],
    sparse_query_vector: Optional[Dict[str, float]],
    context_type: Optional[str],
    target_directories: List[str],
    extra_filter: Optional[FilterExpr | Dict[str, Any]],
    level: Optional[List[int]],
    session_user_id: Optional[str] = None,
    replacement_page_budget: Optional[_SessionLogReplacementBudget] = None,
) -> tuple[List[Dict[str, Any]], int, int, bool]:
    results: List[Dict[str, Any]] = []
    searches = 0
    scanned = 0
    offset = 0
    truncated = False
    budget = replacement_page_budget or _new_session_log_replacement_budget()
    while True:
        page = await vector_proxy.search_in_tenant(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            context_type=context_type,
            target_directories=target_directories,
            extra_filter=extra_filter,
            level=level,
            limit=page_limit,
            offset=offset,
        )
        searches += 1
        scanned += len(page)
        normalization_incomplete = False
        for result in page:
            classification = _classify_session_log_result(result, session_user_id)
            normalization_incomplete = normalization_incomplete or classification.incomplete
            if not classification.should_exclude:
                results.append(result)
        if normalization_incomplete:
            truncated = True
            logger.warning("Session-log filtering excluded an incompletely normalized global URI")
            get_current_telemetry().count("vector.session_log_filter_truncated", 1)
        if len(results) >= desired_limit or len(page) < page_limit:
            break
        if not budget.claim():
            truncated = True
            logger.warning(
                "Session-log filtering exhausted the request-wide replacement-page "
                "budget during global search before finding %d eligible results",
                desired_limit,
            )
            get_current_telemetry().count("vector.session_log_filter_truncated", 1)
            break
        offset += page_limit
    return results[:desired_limit], searches, scanned, truncated


async def _search_children_excluding_session_logs(
    vector_proxy: VikingDBManagerProxy,
    *,
    parent_uri: str,
    desired_limit: int,
    page_limit: int,
    query_vector: Optional[List[float]],
    sparse_query_vector: Optional[Dict[str, float]],
    context_type: Optional[str],
    target_directories: Optional[List[str]],
    extra_filter: Optional[FilterExpr | Dict[str, Any]],
    session_user_id: Optional[str] = None,
    replacement_page_budget: Optional[_SessionLogReplacementBudget] = None,
) -> tuple[List[Dict[str, Any]], int, int, bool]:
    results: List[Dict[str, Any]] = []
    searches = 0
    scanned = 0
    offset = 0
    truncated = False
    budget = replacement_page_budget or _new_session_log_replacement_budget()
    while True:
        page = await vector_proxy.search_children_in_tenant(
            parent_uri=parent_uri,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            context_type=context_type,
            target_directories=target_directories,
            extra_filter=extra_filter,
            limit=page_limit,
            offset=offset,
        )
        searches += 1
        scanned += len(page)
        normalization_incomplete = False
        for result in page:
            classification = _classify_session_log_result(result, session_user_id)
            normalization_incomplete = normalization_incomplete or classification.incomplete
            if not classification.should_exclude:
                results.append(result)
        if normalization_incomplete:
            truncated = True
            logger.warning(
                "Session-log filtering excluded an incompletely normalized child URI under %s",
                parent_uri,
            )
            get_current_telemetry().count("vector.session_log_filter_truncated", 1)
        if len(results) >= desired_limit or len(page) < page_limit:
            break
        if not budget.claim():
            truncated = True
            logger.warning(
                "Session-log filtering exhausted the request-wide replacement-page "
                "budget under %s before finding %d eligible results",
                parent_uri,
                desired_limit,
            )
            get_current_telemetry().count("vector.session_log_filter_truncated", 1)
            break
        offset += page_limit
    return results[:desired_limit], searches, scanned, truncated


class HierarchicalRetriever:
    """Hierarchical retriever with dense and sparse vector support."""

    MAX_CONVERGENCE_ROUNDS = 3  # Stop after multiple rounds with unchanged topk
    MAX_RELATIONS = 5  # Maximum relations per resource
    DIRECTORY_DOMINANCE_RATIO = 1.2  # Directory score must exceed max child score
    GLOBAL_SEARCH_TOPK = 10  # Global retrieval count (more candidates = better rerank precision)
    MAX_PARALLEL_CHILD_SEARCHES = 4  # Limit per-request fan-out against remote vector stores
    LEVEL_URI_SUFFIX = {0: ".abstract.md", 1: ".overview.md"}

    def __init__(
        self,
        storage: VikingDBManager,
        embedder: Optional[Any],
        rerank_config: Optional[RerankConfig] = None,
        retrieval_config: Optional[RetrievalConfig] = None,
    ):
        """Initialize hierarchical retriever with rerank_config.

        Args:
            storage: VikingVectorIndexBackend instance
            embedder: Embedder instance (supports dense/sparse/hybrid)
            rerank_config: Rerank configuration (optional, will fallback to vector search only)
            retrieval_config: Retrieval ranking configuration.
        """
        self.vector_store = storage
        self.embedder = embedder
        self.rerank_config = rerank_config
        self.rerank_max_input_tokens = rerank_config.max_input_tokens if rerank_config else 0
        self.retrieval_config = retrieval_config or RetrievalConfig()
        self.hotness_alpha = self.retrieval_config.hotness_alpha
        self.score_propagation_alpha = self.retrieval_config.score_propagation_alpha

        # Use rerank threshold if available, otherwise use a default
        self.threshold = rerank_config.threshold if rerank_config else 0

        # Initialize rerank client — all providers go through unified dispatch
        if rerank_config and rerank_config.is_available():
            self._rerank_client = RerankClient.from_config(rerank_config)
            provider = rerank_config._effective_provider()
            logger.info(
                f"[HierarchicalRetriever] Rerank enabled (provider={provider}), threshold={self.threshold}"
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
        mode: Optional[RetrieverMode] = None,
        score_threshold: Optional[float] = None,
        score_gte: bool = False,
        scope_dsl: Optional[FilterExpr | Dict[str, Any]] = None,
        level: Optional[List[int]] = None,
    ) -> QueryResult:
        """
        Execute hierarchical retrieval.

        Args:
            ctx: Request context used for tenant and permission filtering
            score_threshold: Custom score threshold (overrides config)
            score_gte: True uses >=, False uses >
            scope_dsl: Additional scope constraints passed from public find/search filter
            level: Optional result level filter (0=L0, 1=L1, 2=L2)
        """
        t0 = time.monotonic()
        telemetry = get_current_telemetry()
        effective_threshold = self._resolve_threshold(score_threshold)
        image_query = bool(getattr(query, "image_query", False))
        if mode is None:
            mode = RetrieverMode.QUICK if not self._rerank_client else RetrieverMode.THINKING
        if image_query:
            mode = RetrieverMode.QUICK
            if level is None:
                level = [2]

        # 创建 proxy 包装器，绑定当前 ctx
        vector_proxy = VikingDBManagerProxy(self.vector_store, ctx)

        target_dirs = [d for d in (query.target_directories or []) if d]

        if not await vector_proxy.collection_exists_bound():
            logger.warning(
                "[RecursiveSearch] Collection %s does not exist",
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
        if self.embedder:
            if image_query and not getattr(self.embedder, "supports_multimodal", False):
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

        # Step 1: Determine starting directories based on explicit target dirs.
        if target_dirs:
            root_uris = target_dirs
        else:
            root_uris = default_target_directories(ctx, context_type=query.context_type)

        context_type = query.context_type.value if query.context_type else None
        if image_query and context_type is None:
            context_type = ContextType.RESOURCE.value

        session_user_id = getattr(getattr(ctx, "user", None), "user_id", None)
        replacement_page_budget = _new_session_log_replacement_budget()
        retrieval_truncated = False
        if mode == RetrieverMode.QUICK:
            search_limit = (
                max(limit * 5, 50) if image_query else max(limit, self.GLOBAL_SEARCH_TOPK)
            )
            with telemetry.measure("search.vector_retrieval"):
                (
                    quick_results,
                    search_count,
                    scanned_count,
                    retrieval_truncated,
                ) = await _search_in_tenant_excluding_session_logs(
                    vector_proxy,
                    desired_limit=search_limit,
                    page_limit=search_limit,
                    query_vector=query_vector,
                    sparse_query_vector=sparse_query_vector,
                    context_type=context_type,
                    target_directories=target_dirs,
                    extra_filter=scope_dsl,
                    level=level,
                    session_user_id=session_user_id,
                    replacement_page_budget=replacement_page_budget,
                )
            telemetry.count("vector.searches", search_count)
            telemetry.count("vector.scored", scanned_count)
            telemetry.count("vector.scanned", scanned_count)

            collected_by_uri: Dict[str, Dict[str, Any]] = {}
            for result in quick_results:
                uri = result.get("uri", "")
                if not uri:
                    continue

                score = self._finite_score(result.get("_score", 0.0))
                if not self._passes_threshold(score, effective_threshold, score_gte):
                    continue

                candidate = dict(result)
                candidate["_score"] = score
                candidate["_final_score"] = score

                previous = collected_by_uri.get(uri)
                if previous is None or score > previous.get("_final_score", 0.0):
                    collected_by_uri[uri] = candidate

            candidates = sorted(
                collected_by_uri.values(),
                key=lambda x: x.get("_final_score", 0.0),
                reverse=True,
            )
            apply_hotness = False
            rerank_used = False
        else:
            # Step 2: Global vector search to supplement starting points
            global_search_limit = max(limit, self.GLOBAL_SEARCH_TOPK)
            with telemetry.measure("search.vector_retrieval"):
                (
                    global_results,
                    search_count,
                    scanned_count,
                    global_truncated,
                ) = await _search_in_tenant_excluding_session_logs(
                    vector_proxy,
                    desired_limit=global_search_limit,
                    page_limit=global_search_limit,
                    query_vector=query_vector,
                    sparse_query_vector=sparse_query_vector,
                    context_type=context_type,
                    target_directories=target_dirs,
                    extra_filter=scope_dsl,
                    level=[0, 1],
                    session_user_id=session_user_id,
                    replacement_page_budget=replacement_page_budget,
                )
            telemetry.count("vector.searches", search_count)
            telemetry.count("vector.scored", scanned_count)
            telemetry.count("vector.scanned", scanned_count)

            # Debug: Print all URIs in global_results
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"[retrieve] target_dirs: {target_dirs}")
                logger.debug(f"[retrieve] root_uris: {root_uris}")
                logger.debug(f"[retrieve] scope_dsl: {scope_dsl}")
                logger.debug(
                    f"[retrieve] Step 2 completed, global_results contains {len(global_results)} items:"
                )
                for i, r in enumerate(global_results):
                    uri = r.get("uri", "UNKNOWN_URI")
                    score = r.get("_score", 0.0)
                    result_level = r.get("level", "UNKNOWN_LEVEL")
                    account_id = r.get("account_id", "UNKNOWN_ACCOUNT_ID")
                    logger.debug(
                        f"  [{i}] URI: {uri}, score: {score:.4f}, level: {result_level}, account_id: {account_id}"
                    )

            # Step 3: Pick recursive entry points from directory hits and explicit roots.
            directory_scores = [self._finite_score(r.get("_score", 0.0)) for r in global_results]
            if self._rerank_client and mode == RetrieverMode.THINKING:
                directory_scores = await self._rerank_scores(
                    query.query,
                    [str(r.get("abstract", "")) for r in global_results],
                    directory_scores,
                )

            starting_points = []
            seen_starting_uris = set()
            for result, score in zip(global_results, directory_scores, strict=True):
                uri = result.get("uri", "")
                if not uri or uri in seen_starting_uris:
                    continue
                starting_points.append((uri, score))
                seen_starting_uris.add(uri)

            for uri in root_uris:
                if uri not in seen_starting_uris:
                    starting_points.append((uri, 0.0))
                    seen_starting_uris.add(uri)

            # Add directory hits to the result pool only when explicitly requested.
            initial_candidates = []
            if level is not None:
                for result, score in zip(global_results, directory_scores, strict=True):
                    if result.get("level", 2) not in level:
                        continue
                    candidate = dict(result)
                    candidate["_score"] = score
                    initial_candidates.append(candidate)

            # Step 4: Recursive search
            with telemetry.measure("search.vector_retrieval"):
                recursive_truncation: Dict[str, bool] = {}
                candidates = await self._recursive_search(
                    vector_proxy=vector_proxy,
                    query=query.query,
                    query_vector=query_vector,
                    sparse_query_vector=sparse_query_vector,
                    starting_points=starting_points,
                    limit=limit,
                    mode=mode,
                    threshold=effective_threshold,
                    score_gte=score_gte,
                    context_type=context_type,
                    target_dirs=target_dirs,
                    scope_dsl=scope_dsl,
                    initial_candidates=initial_candidates,
                    level=level,
                    truncation_state=recursive_truncation,
                    session_user_id=session_user_id,
                    replacement_page_budget=replacement_page_budget,
                )
            retrieval_truncated = global_truncated or recursive_truncation.get("truncated", False)
            apply_hotness = True
            rerank_used = self._rerank_client is not None and mode == RetrieverMode.THINKING

        # Step 6: Convert results
        matched = await self._convert_to_matched_contexts(
            candidates,
            ctx=ctx,
            apply_hotness=apply_hotness,
        )
        final = matched[:limit]

        elapsed_ms = (time.monotonic() - t0) * 1000
        get_stats_collector().record_query(
            context_type=context_type or "unknown",
            result_count=len(final),
            scores=[m.score for m in final],
            latency_ms=elapsed_ms,
            rerank_used=rerank_used,
        )

        return QueryResult(
            query=query,
            matched_contexts=final,
            searched_directories=root_uris,
            truncated=retrieval_truncated,
        )

    def _resolve_threshold(self, threshold: Optional[float]) -> float:
        resolved = threshold if threshold is not None else self.threshold
        return resolved if resolved is not None else 0.0

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
    ) -> List[float]:
        """Return rerank scores or fall back to vector scores."""
        if not self._rerank_client or not documents:
            return fallback_scores

        rerank_query = query
        rerank_documents = [
            (index, document) for index, document in enumerate(documents) if document.strip()
        ]
        if not rerank_documents:
            return fallback_scores

        if self.rerank_max_input_tokens > 0:
            max_query_tokens = self.rerank_max_input_tokens * 3 // 4
            if estimate_text_tokens(query) > max_query_tokens:
                rerank_query = truncate_text_to_token_budget(query, max_query_tokens)
            document_tokens = self.rerank_max_input_tokens - estimate_text_tokens(rerank_query)
            rerank_documents = [
                (index, truncate_text_to_token_budget(document, document_tokens))
                for index, document in rerank_documents
            ]

        try:
            scores = await asyncio.to_thread(
                self._rerank_client.rerank_batch,
                rerank_query,
                [document for _, document in rerank_documents],
            )
        except Exception as e:
            logger.warning(
                "[HierarchicalRetriever] Rerank failed, fallback to vector scores: %s", e
            )
            return fallback_scores

        if not scores or len(scores) != len(rerank_documents):
            logger.warning(
                "[HierarchicalRetriever] Invalid rerank result, fallback to vector scores"
            )
            return fallback_scores

        normalized_scores = list(fallback_scores)
        for score, (index, _) in zip(scores, rerank_documents, strict=True):
            normalized_scores[index] = self._finite_score(score, fallback_scores[index])
        return normalized_scores

    async def _recursive_search(
        self,
        vector_proxy: VikingDBManagerProxy,
        query: str,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]],
        starting_points: List[Tuple[str, float]],
        limit: int,
        mode: str,
        threshold: Optional[float] = None,
        score_gte: bool = False,
        context_type: Optional[str] = None,
        target_dirs: Optional[List[str]] = None,
        scope_dsl: Optional[FilterExpr | Dict[str, Any]] = None,
        initial_candidates: Optional[List[Dict[str, Any]]] = None,
        level: Optional[List[int]] = None,
        truncation_state: Optional[Dict[str, bool]] = None,
        session_user_id: Optional[str] = None,
        replacement_page_budget: Optional[_SessionLogReplacementBudget] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recursive search with directory priority return and score propagation.

        Args:
            threshold: Score threshold
            score_gte: True uses >=, False uses >
            grep_patterns: Keyword match patterns
            scope_dsl: Additional scope constraints from public find/search filter
        """
        effective_threshold = self._resolve_threshold(threshold)

        sparse_query_vector = sparse_query_vector or None

        collected_by_uri: Dict[str, Dict[str, Any]] = {}
        dir_queue: List[tuple] = []  # Priority queue: (-score, uri)
        visited: set = set()
        prev_topk_uris: set = set()
        prev_pool_size = 0
        convergence_rounds = 0
        stagnant_rounds = 0
        replacement_page_budget = replacement_page_budget or _new_session_log_replacement_budget()

        # Add initial candidates that match the requested level.
        if initial_candidates:
            for r in initial_candidates:
                uri = r.get("uri", "")
                classification = _classify_session_log_result(r, session_user_id)
                if classification.incomplete and truncation_state is not None:
                    truncation_state["truncated"] = True
                if not uri or classification.should_exclude:
                    continue
                if level is None or r.get("level", 2) in level:
                    score = self._finite_score(r.get("_score", 0.0))
                    if not self._passes_threshold(score, effective_threshold, score_gte):
                        logger.debug(
                            f"[RecursiveSearch] Initial candidate URI {uri} score {score:.4f} did not pass threshold {effective_threshold}"
                        )
                        continue
                    r["_final_score"] = score
                    collected_by_uri[uri] = r
                    logger.debug(
                        f"[RecursiveSearch] Added initial candidate: {uri} (score: {score:.4f})"
                    )

        alpha = self.score_propagation_alpha

        # Initialize: process starting points
        for uri, score in starting_points:
            classification = _classify_session_log_uri(uri, session_user_id)
            if classification.incomplete and truncation_state is not None:
                truncation_state["truncated"] = True
            if classification.should_exclude:
                continue
            heapq.heappush(dir_queue, (-score, uri))

        child_search_limit = max(limit * 2, 20)

        async def search_children(
            current_uri: str,
        ) -> tuple[List[Dict[str, Any]], int, int, bool]:
            return await _search_children_excluding_session_logs(
                vector_proxy,
                parent_uri=current_uri,
                desired_limit=child_search_limit,
                page_limit=child_search_limit,
                query_vector=query_vector,
                sparse_query_vector=sparse_query_vector,
                context_type=context_type,
                target_directories=target_dirs,
                extra_filter=scope_dsl,
                session_user_id=session_user_id,
                replacement_page_budget=replacement_page_budget,
            )

        parallelism = max(1, self.MAX_PARALLEL_CHILD_SEARCHES)

        while dir_queue:
            batch: List[Tuple[str, float]] = []
            while dir_queue and len(batch) < parallelism:
                temp_score, current_uri = heapq.heappop(dir_queue)
                current_score = -temp_score
                if current_uri in visited:
                    continue
                visited.add(current_uri)
                logger.info(f"[RecursiveSearch] Entering URI: {current_uri}")
                batch.append((current_uri, current_score))

            if not batch:
                continue

            batch_results = await asyncio.gather(
                *(search_children(current_uri) for current_uri, _ in batch)
            )

            telemetry = get_current_telemetry()
            for (_, current_score), (
                results,
                search_count,
                scanned_count,
                search_truncated,
            ) in zip(batch, batch_results, strict=True):
                if search_truncated and truncation_state is not None:
                    truncation_state["truncated"] = True
                telemetry.count("vector.searches", search_count)
                telemetry.count("vector.scored", scanned_count)
                telemetry.count("vector.scanned", scanned_count)

                if not results:
                    continue

                query_scores = [self._finite_score(r.get("_score", 0.0)) for r in results]
                if self._rerank_client and mode == RetrieverMode.THINKING:
                    documents = [str(r.get("abstract", "")) for r in results]
                    query_scores = await self._rerank_scores(query, documents, query_scores)

                for r, score in zip(results, query_scores, strict=True):
                    uri = r.get("uri", "")
                    classification = _classify_session_log_result(r, session_user_id)
                    if classification.incomplete and truncation_state is not None:
                        truncation_state["truncated"] = True
                    if not uri or classification.should_exclude:
                        continue
                    final_score = (
                        alpha * score + (1 - alpha) * current_score if current_score else score
                    )

                    if not self._passes_threshold(final_score, effective_threshold, score_gte):
                        logger.debug(
                            f"[RecursiveSearch] URI {uri} score {final_score} did not pass threshold {effective_threshold}"
                        )
                        continue

                    telemetry.count("vector.passed", 1)
                    if level is None or r.get("level", 2) in level:
                        # Deduplicate by URI and keep the highest-scored candidate.
                        previous = collected_by_uri.get(uri)
                        if previous is None or final_score > previous.get("_final_score", 0):
                            r["_final_score"] = final_score
                            collected_by_uri[uri] = r
                            logger.debug(
                                "[RecursiveSearch] Updated URI: %s candidate score to %.4f",
                                uri,
                                final_score,
                            )

                    # Only recurse into directories (L0/L1). L2 files are terminal hits.
                    if uri not in visited and r.get("level", 2) != 2:
                        heapq.heappush(dir_queue, (-final_score, uri))

            # Convergence check after each parallel expansion round.
            current_topk = sorted(
                collected_by_uri.values(),
                key=lambda x: x.get("_final_score", 0),
                reverse=True,
            )[:limit]
            current_topk_uris = {c.get("uri", "") for c in current_topk}
            current_pool_size = len(collected_by_uri)

            if current_topk_uris == prev_topk_uris and len(current_topk_uris) >= limit:
                convergence_rounds += 1

                if convergence_rounds >= self.MAX_CONVERGENCE_ROUNDS:
                    break
            elif current_pool_size == prev_pool_size:
                stagnant_rounds += 1

                if stagnant_rounds >= self.MAX_CONVERGENCE_ROUNDS:
                    break
            else:
                convergence_rounds = 0
                stagnant_rounds = 0
                prev_topk_uris = current_topk_uris
                prev_pool_size = current_pool_size

        collected = sorted(
            collected_by_uri.values(),
            key=lambda x: x.get("_final_score", 0),
            reverse=True,
        )
        return collected[:limit]

    async def _convert_to_matched_contexts(
        self,
        candidates: List[Dict[str, Any]],
        ctx: RequestContext,
        apply_hotness: bool = True,
    ) -> List[MatchedContext]:
        """Convert candidate results to MatchedContext list.

        Blends semantic similarity with a hotness score derived from
        ``active_count`` and ``updated_at`` when configured. The blend weight
        is controlled by ``retrieval.hotness_alpha`` (0 disables the boost).
        """
        results = []
        for c in candidates:
            relations = []

            # Fix: clamp inf/nan scores from vector search (#inf-score)
            semantic_score = self._finite_score(c.get("_final_score", c.get("_score", 0.0)))

            alpha = self.hotness_alpha
            if apply_hotness and alpha > 0:
                updated_at_raw = c.get("updated_at")
                if isinstance(updated_at_raw, str):
                    try:
                        updated_at_val = parse_iso_datetime(updated_at_raw)
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
                final_score = (1 - alpha) * semantic_score + alpha * h_score
            else:
                final_score = semantic_score
            if not math.isfinite(final_score):
                final_score = 0.0
            level = c.get("level", 2)
            display_uri = self._append_level_suffix(c.get("uri", ""), level)

            results.append(
                MatchedContext(
                    uri=display_uri,
                    context_type=ContextType(c["context_type"])
                    if c.get("context_type")
                    else ContextType.RESOURCE,
                    level=level,
                    abstract=c.get("abstract", ""),
                    category=c.get("category", ""),
                    score=final_score,
                    relations=relations,
                )
            )

        # Re-sort by blended score so hotness boost can change ranking
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
