# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped native memory links with one-hop candidate expansion."""

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openviking.core.retrieval_targets import default_target_directories
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.models.embedder.base import embed_compat
from openviking.retrieve.retrieval_stats import get_stats_collector
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import StoredLink
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.expr import And, FilterExpr, In, RawDSL
from openviking.storage.vikingdb_manager import VikingDBManagerProxy
from openviking.telemetry import get_current_telemetry
from openviking.utils.time_decay import parse_duration_ms
from openviking.utils.token_estimation import estimate_text_tokens, truncate_text_to_token_budget
from openviking_cli.exceptions import InvalidURIError
from openviking_cli.retrieve.types import MatchedContext, QueryResult
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class MemoryLinks:
    """Read link metadata and resolve targets through the ordinary search scope."""

    LOOKUP_BATCH_SIZE = 200

    def __init__(
        self,
        fs: Any,
        proxy: VikingDBManagerProxy,
        ctx: RequestContext,
        target_directories: List[str],
        scope_dsl: Optional[FilterExpr | Dict[str, Any]],
    ):
        self.fs = fs
        self.proxy = proxy
        self.ctx = ctx
        self.target_directories = target_directories
        self.scope_dsl = scope_dsl
        self._semaphore = asyncio.Semaphore(10)
        self._metadata: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._readable: set[str] = set()

    async def _read(self, uri: str) -> Dict[str, List[Dict[str, Any]]]:
        if uri in self._metadata:
            return self._metadata[uri]
        metadata: Dict[str, List[Dict[str, Any]]] = {"links": [], "backlinks": []}
        try:
            async with self._semaphore:
                raw = await self.fs.read_file(uri, ctx=self.ctx)
            memory = MemoryFileUtils.read(raw, uri=uri)
            self._readable.add(uri)
            for key in metadata:
                for value in getattr(memory, key):
                    try:
                        link = StoredLink.model_validate(value)
                        source = validate_request_viking_uri(link.from_uri, self.ctx)
                        target = validate_request_viking_uri(link.to_uri, self.ctx)
                    except (TypeError, ValueError, InvalidURIError):
                        continue
                    if source == target:
                        continue
                    if (key == "links" and source != uri) or (key == "backlinks" and target != uri):
                        continue
                    link.weight = (
                        min(1.0, max(0.0, link.weight)) if math.isfinite(link.weight) else 0.5
                    )
                    metadata[key].append(
                        {**link.model_dump(), "from_uri": source, "to_uri": target}
                    )
        except Exception as exc:
            # Stale/deleted/unreadable files must not break ordinary search.
            logger.debug("Cannot read memory links for %s: %s", uri, type(exc).__name__)
        self._metadata[uri] = metadata
        return metadata

    @staticmethod
    def _other(uri: str, link: Dict[str, Any]) -> str:
        return link["to_uri"] if link["from_uri"] == uri else link["from_uri"]

    async def _resolve(self, uris: List[str]) -> Dict[str, Dict[str, Any]]:
        if not uris:
            return {}
        # Reuse exactly the tenant/ACL/target/filter machinery used by vector recall.
        # An untrusted URI in a memory file is never itself an access grant.
        records = {}
        for start in range(0, len(uris), self.LOOKUP_BATCH_SIZE):
            batch = uris[start : start + self.LOOKUP_BATCH_SIZE]
            conditions: List[FilterExpr] = [In("uri", batch)]
            if self.scope_dsl:
                conditions.append(
                    RawDSL(self.scope_dsl) if isinstance(self.scope_dsl, dict) else self.scope_dsl
                )
            offset = 0
            while True:
                rows = await self.proxy.filter_in_tenant(
                    context_type="memory",
                    target_directories=self.target_directories,
                    extra_filter=And(conditions),
                    level=[2],
                    limit=len(batch),
                    offset=offset,
                )
                records.update({row["uri"]: row for row in rows if row.get("uri") in batch})
                # Legacy duplicate index rows must not crowd out other targets.
                if len(rows) < len(batch) or all(uri in records for uri in batch):
                    break
                offset += len(rows)
        await asyncio.gather(*(self._read(uri) for uri in records))
        return {uri: row for uri, row in records.items() if uri in self._readable}

    async def _load(self, uris: List[str]):
        metadata = await asyncio.gather(*(self._read(uri) for uri in uris))
        targets = list(
            dict.fromkeys(
                self._other(uri, link)
                for uri, fields in zip(uris, metadata, strict=True)
                for links in fields.values()
                for link in links
            )
        )
        records = await self._resolve(targets)
        return metadata, records

    async def expand(self, candidates: List[Dict[str, Any]]):
        """Add every eligible L2 memory referenced by the initial seeds, once."""
        seeds = [
            row["uri"]
            for row in candidates
            if row.get("context_type") == "memory" and row.get("level") == 2
        ]
        _, records = await self._load(seeds)
        seen = {row["uri"] for row in candidates}
        additions = [
            {**records[uri], "_score": 0.0}
            for uri in sorted(records)
            if uri not in seen and str(records[uri].get("abstract", "")).strip()
        ]
        return candidates + additions

    async def attach(self, matches: List[MatchedContext]) -> None:
        """Expose only links whose opposite endpoint passes the current query scope."""
        memories = [
            match for match in matches if match.context_type.value == "memory" and match.level == 2
        ]
        metadata, records = await self._load([match.uri for match in memories])
        for match, fields in zip(memories, metadata, strict=True):
            for key, links in fields.items():
                setattr(
                    match,
                    key,
                    [link for link in links if self._other(match.uri, link) in records],
                )


async def _rerank_all(retriever, query: str, candidates: List[Dict[str, Any]]):
    """Score every candidate in batches; a failed batch invalidates all scores."""
    config = retriever.rerank_config
    documents = [
        (index, str(row.get("abstract", "")))
        for index, row in enumerate(candidates)
        if str(row.get("abstract", "")).strip()
    ]
    scores = [row["_score"] for row in candidates]
    if not documents:
        return scores
    if config.max_input_tokens > 0:
        query = truncate_text_to_token_budget(query, config.max_input_tokens * 3 // 4)
        budget = config.max_input_tokens - estimate_text_tokens(query)
        documents = [
            (index, truncate_text_to_token_budget(text, budget)) for index, text in documents
        ]
    batch_size = config.batch_size
    if config._effective_provider() == "vikingdb":
        batch_size = min(batch_size, 100)
    try:
        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            values = await asyncio.to_thread(
                retriever._rerank_client.rerank_batch, query, [text for _, text in batch]
            )
            if values is None or len(values) != len(batch):
                raise ValueError("Invalid rerank batch result")
            for (index, _), value in zip(batch, values, strict=True):
                score = float(value)
                if not math.isfinite(score):
                    raise ValueError("Non-finite rerank score")
                scores[index] = score
    except Exception as exc:
        logger.warning("Search link rerank failed; restoring original candidates: %s", exc)
        return None
    return scores


async def search_with_memory_links(
    retriever,
    fs,
    query,
    ctx: RequestContext,
    *,
    limit: int = 10,
    score_threshold: Optional[float] = None,
    scope_dsl=None,
    level=None,
    events_time_decay_protection: Optional[str] = None,
    request_now=None,
    search_type="semantic",
) -> QueryResult:
    """Search-only pipeline: recall -> one-hop links -> batched rerank -> top-k.

    Keep the common retriever unchanged: its ordinary retrieve() would rerank
    and truncate before this Search extension has added the linked candidates.
    Reuse its configured model, score handling and result conversion, but issue
    the scoped recall here so it is scored and reported exactly once.
    """
    options = {
        "limit": limit,
        "score_threshold": score_threshold,
        "scope_dsl": scope_dsl,
        "level": level,
        "events_time_decay_protection": events_time_decay_protection,
        "request_now": request_now,
        "search_type": search_type,
    }
    if retriever._rerank_client is None or query.image_query:
        return await retriever.retrieve(query, ctx, **options)

    started = time.monotonic()
    telemetry = get_current_telemetry()
    proxy = VikingDBManagerProxy(retriever.vector_store, ctx)
    if not await proxy.collection_exists_bound():
        return QueryResult(query=query, matched_contexts=[], searched_directories=[])
    targets = [uri for uri in (query.target_directories or []) if uri]
    context_type = query.context_type.value if query.context_type else None
    recall_options = {
        "context_type": context_type,
        "target_directories": targets,
        "extra_filter": scope_dsl,
        "level": level,
        "limit": limit * retriever.RERANK_CANDIDATE_MULTIPLIER,
    }
    dense, sparse = None, None
    if search_type == "semantic" and retriever.embedder:
        with telemetry.measure("search.embed_query"):
            embedding = await embed_compat(
                retriever.embedder,
                getattr(query, "embedding_input", None) or query.query,
                is_query=True,
            )
            dense, sparse = embedding.dense_vector, embedding.sparse_vector
    with telemetry.measure("search.vector_retrieval"):
        if search_type == "keywords":
            rows = await proxy.search_by_keywords_in_tenant(query=query.query, **recall_options)
        else:
            if events_time_decay_protection is not None:
                parse_duration_ms(
                    events_time_decay_protection, parameter_name="events_time_decay_protection"
                )
                recall_options.update(
                    events_time_decay_protection=events_time_decay_protection,
                    request_now=request_now or datetime.now(timezone.utc),
                )
            rows = await proxy.search_in_tenant(
                query_vector=dense,
                sparse_query_vector=sparse,
                **recall_options,
            )
    telemetry.count("vector.searches", 1)
    telemetry.count("vector.scored", len(rows))
    telemetry.count("vector.scanned", len(rows))
    unique = {}
    for row in rows:
        uri = row.get("uri")
        score = retriever._finite_score(row.get("_score", 0))
        if uri and (uri not in unique or score > unique[uri]["_score"]):
            unique[uri] = {**row, "_score": score}
    original = sorted(unique.values(), key=lambda row: row["_score"], reverse=True)
    links = MemoryLinks(fs, proxy, ctx, targets, scope_dsl)
    with telemetry.measure("search.link_expansion"):
        candidates = await links.expand(original)
    scores = await _rerank_all(retriever, query.query, candidates)
    fallback = scores is None
    if fallback:
        candidates, scores = original, [row["_score"] for row in original]
    threshold = retriever._resolve_threshold(score_threshold)
    filtered = [
        {**row, "_final_score": score}
        for row, score in zip(candidates, scores, strict=True)
        if retriever._passes_threshold(score, threshold, False)
    ]
    telemetry.count("vector.passed", len(filtered))
    matches = await retriever._convert_to_matched_contexts(filtered, ctx=ctx)
    final = matches[:limit]
    await links.attach(final)
    get_stats_collector().record_query(
        context_type=context_type or "unknown",
        result_count=len(final),
        scores=[match.score for match in final],
        latency_ms=(time.monotonic() - started) * 1000,
        rerank_used=bool(candidates) and not fallback,
        rerank_fallback=fallback,
    )
    return QueryResult(
        query=query,
        matched_contexts=final,
        searched_directories=targets
        or default_target_directories(ctx, context_type=query.context_type),
    )
