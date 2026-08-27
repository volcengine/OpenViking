# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Keyword/dense score fusion for find/search.

The local FTS5 sidecar provides search-time BM25 recall for exact tokens (code
names, acronyms, tickers, version strings) that dense retrieval handles poorly.
This module merges dense ``MatchedContext`` results with keyword candidates
using Reciprocal Rank Fusion (robust, no score calibration) or a weighted blend
of normalized scores.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from openviking_cli.retrieve.types import MatchedContext

ReadAbstract = Callable[[str], Awaitable[str]]


class HybridKeywordRecaller:
    """Fuse keyword-sidecar recall into a dense retrieval result list."""

    def __init__(
        self,
        keyword_fs: Any,
        hybrid_config: Any,
        keyword_config: Optional[Any] = None,
    ):
        self._keyword_fs = keyword_fs
        self._config = hybrid_config
        self._keyword_config = keyword_config

    def enabled(self, ctx: Any = None) -> bool:
        if self._keyword_fs is None or self._config is None:
            return False
        if not getattr(self._config, "enabled", False):
            return False
        account_id = getattr(ctx, "account_id", None) or "default"
        try:
            return self._keyword_fs.is_ready(account_id)
        except Exception:
            return False

    async def enhance(
        self,
        query: str,
        dense: Sequence[MatchedContext],
        scope_uris: Sequence[str],
        ctx: Any,
        limit: int,
        exclude_uri: str = "",
        read_abstract: Optional[ReadAbstract] = None,
    ) -> List[MatchedContext]:
        """Merge keyword candidates into ``dense`` and return the fused top ``limit``."""
        if not self.enabled(ctx) or not query:
            return list(dense)
        candidates = await self._recall(query, scope_uris, exclude_uri, ctx, limit)
        if not candidates:
            return list(dense)
        fused = self._fuse(dense, candidates, limit)
        # Enrich keyword-only hits with best-effort abstract text.
        dense_uris = {m.uri for m in dense}
        for mc in fused:
            if mc.uri in dense_uris or not mc.abstract:
                if read_abstract is not None and not mc.abstract:
                    try:
                        mc.abstract = await read_abstract(mc.uri)
                    except Exception:
                        pass
        return fused

    async def _recall(
        self,
        query: str,
        scope_uris: Sequence[str],
        exclude_uri: str,
        ctx: Any,
        limit: int,
    ) -> List[Tuple[str, float]]:
        account_id = getattr(ctx, "account_id", None) or "default"
        collected: Dict[str, float] = {}
        scopes = list(scope_uris) or [""]
        for scope in scopes:
            try:
                hits = self._keyword_fs.lookup(
                    account_id=account_id,
                    query=query,
                    scope_uri=scope,
                    exclude_uri=exclude_uri,
                    limit=max(limit * 3, 30),
                )
            except Exception:
                continue
            for uri, score in hits:
                # Keep the best (lowest) bm25 score for a URI across scopes.
                if uri not in collected or score < collected[uri]:
                    collected[uri] = score
        ranked = sorted(collected.items(), key=lambda x: x[1])
        return ranked[: max(limit * 3, 30)]

    def _fuse(
        self,
        dense: Sequence[MatchedContext],
        candidates: Sequence[Tuple[str, float]],
        limit: int,
    ) -> List[MatchedContext]:
        fusion = getattr(self._config, "fusion", "rrf")
        if fusion == "weighted":
            return self._fuse_weighted(dense, candidates, limit)
        return self._fuse_rrf(dense, candidates, limit)

    def _fuse_rrf(
        self,
        dense: Sequence[MatchedContext],
        candidates: Sequence[Tuple[str, float]],
        limit: int,
    ) -> List[MatchedContext]:
        k = float(getattr(self._config, "rrf_k", 60.0) or 60.0)
        dense_rank = {m.uri: i for i, m in enumerate(dense)}
        kw_rank = {uri: i for i, (uri, _s) in enumerate(candidates)}
        scores: Dict[str, float] = {}
        for uri in dense_rank:
            scores[uri] = 1.0 / (k + dense_rank[uri] + 1)
        for uri in kw_rank:
            scores[uri] = scores.get(uri, 0.0) + 1.0 / (k + kw_rank[uri] + 1)

        by_uri = {m.uri: m for m in dense}
        ordered_uris = sorted(scores.keys(), key=lambda u: (-scores[u], dense_rank.get(u, 10**9)))
        out: List[MatchedContext] = []
        for uri in ordered_uris:
            mc = by_uri.get(uri)
            if mc is None:
                mc = self._make_keyword_context(uri, candidates)
            out.append(replace(mc, score=scores[uri]))
            if len(out) >= limit:
                break
        # Always keep the dense ordering for ties handled above; append leftover
        # dense results only if there is still headroom.
        if len(out) < limit:
            seen = {m.uri for m in out}
            for m in dense:
                if m.uri not in seen:
                    out.append(m)
                    seen.add(m.uri)
                    if len(out) >= limit:
                        break
        return out

    def _fuse_weighted(
        self,
        dense: Sequence[MatchedContext],
        candidates: Sequence[Tuple[str, float]],
        limit: int,
    ) -> List[MatchedContext]:
        w = float(getattr(self._config, "keyword_weight", 0.3) or 0.3)
        raw_scores = {uri: score for uri, score in candidates}
        if raw_scores:
            lo = min(raw_scores.values())
            hi = max(raw_scores.values())
        else:
            lo = hi = 0.0

        def norm(uri: str) -> float:
            if hi > lo:
                return (raw_scores[uri] - lo) / (hi - lo)
            return 0.5

        by_uri = {m.uri: m for m in dense}
        scores: Dict[str, float] = {}
        for m in dense:
            kw = norm(m.uri) if m.uri in raw_scores else 0.0
            scores[m.uri] = (1 - w) * m.score + w * kw
        for uri in raw_scores:
            if uri not in scores:
                scores[uri] = w * norm(uri)

        ordered = sorted(scores.keys(), key=lambda u: -scores[u])
        out: List[MatchedContext] = []
        for uri in ordered:
            mc = by_uri.get(uri)
            if mc is None:
                mc = self._make_keyword_context(uri, candidates)
            out.append(replace(mc, score=scores[uri]))
            if len(out) >= limit:
                break
        return out

    def _make_keyword_context(self, uri: str, candidates: Sequence[Tuple[str, float]]) -> MatchedContext:
        from openviking.core.context import ContextType
        from openviking.core.namespace import context_type_for_uri

        score = 0.0
        for u, s in candidates:
            if u == uri:
                score = s
                break
        ctype = context_type_for_uri(uri)
        try:
            context_type = ContextType(ctype)
        except ValueError:
            context_type = ContextType.RESOURCE
        return MatchedContext(
            uri=uri,
            context_type=context_type,
            level=2,
            abstract="",
            category="",
            score=score,
            match_reason="keyword",
        )
