# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Package search used exclusively by the Skills API."""

import math
import time
from typing import Optional

from openviking.models.embedder.base import embed_compat
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.retrieve.retrieval_stats import get_stats_collector
from openviking.retrieve.skill_results import SkillResultResolver, candidate_key, pagination_key
from openviking.server.identity import RequestContext
from openviking.storage.vikingdb_manager import VikingDBManagerProxy
from openviking.telemetry import get_current_telemetry
from openviking_cli.retrieve.types import QueryResult, TypedQuery


class SkillPackageRetriever(HierarchicalRetriever):
    """Reuse hit conversion and thresholds; keep package pagination out of general search."""

    async def retrieve_skills(
        self,
        query: TypedQuery,
        ctx: RequestContext,
        *,
        skill_resolver: SkillResultResolver,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        score_gte: bool = False,
        level: Optional[list[int]] = None,
        scope_dsl=None,
    ) -> QueryResult:
        started = time.monotonic()
        telemetry = get_current_telemetry()
        proxy = VikingDBManagerProxy(self.vector_store, ctx)
        target_dirs = query.target_directories or []
        if limit <= 0 or not await proxy.collection_exists_bound():
            return QueryResult(query=query, matched_contexts=[], searched_directories=target_dirs)

        threshold = self._resolve_threshold(score_threshold)
        query_vector = sparse_vector = None
        if self.embedder:
            with telemetry.measure("search.embed_query"):
                embedded = await embed_compat(self.embedder, query.query, is_query=True)
                query_vector, sparse_vector = embedded.dense_vector, embedded.sparse_vector

        page_size = max(limit, self.GLOBAL_SEARCH_TOPK)
        offset = 0
        seen = set()
        candidates = {}
        matches = []
        while True:
            with telemetry.measure("search.vector_retrieval"):
                page = await proxy.search_in_tenant(
                    query_vector=query_vector,
                    sparse_query_vector=sparse_vector,
                    context_type="skill",
                    target_directories=target_dirs,
                    extra_filter=scope_dsl,
                    level=level,
                    limit=page_size,
                    offset=offset,
                )
            telemetry.count("vector.searches", 1)
            telemetry.count("vector.scored", len(page))
            telemetry.count("vector.scanned", len(page))
            keys = {pagination_key(item) for item in page}
            if offset and keys and keys <= seen:
                raise RuntimeError(
                    "Skill search pagination did not advance; results are incomplete"
                )
            seen.update(keys)
            for item in page:
                if item.get("context_type") != "skill" or not item.get("uri"):
                    continue
                score = self._finite_score(item.get("_score", 0.0))
                if not self._passes_threshold(score, threshold, score_gte):
                    continue
                key = candidate_key(item)
                previous = candidates.get(key)
                if previous is None or score > previous["_final_score"]:
                    candidates[key] = {**item, "_score": score, "_final_score": score}
            converted = await self._convert_to_matched_contexts(
                list(candidates.values()), ctx=ctx, apply_hotness=False
            )
            matches = await skill_resolver.resolve(converted)
            if len(matches) >= limit or len(page) < page_size:
                break
            # Every page uses the same query vectors and vector-score ordering.
            boundary = self._finite_score(page[-1].get("_score"), default=math.nan)
            if math.isfinite(boundary) and not self._passes_threshold(
                boundary, threshold, score_gte
            ):
                break
            offset += len(page)

        final = matches[:limit]
        telemetry.set("vector.returned", len(final))
        get_stats_collector().record_query(
            context_type="skill",
            result_count=len(final),
            scores=[item.score for item in final],
            latency_ms=(time.monotonic() - started) * 1000,
            rerank_used=False,
        )
        return QueryResult(query=query, matched_contexts=final, searched_directories=target_dirs)
