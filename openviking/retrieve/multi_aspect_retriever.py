# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Multi-aspect retriever for OpenClaw recall.

Embeds a user query with N different instruction prompts to capture different
semantic perspectives (semantic similarity, entity matching, temporal events,
procedural knowledge, etc.), then batch-searches all N vectors simultaneously
using search_batch() — which dispatches to AMX INT8 tile computation on
supported hardware.

Architecture::

    User query: "How does the auth module work?"
         │
    ┌────┴──────────────────────────────────────────┐
    │  Multi-prompt Embedding (N aspects)           │
    │                                               │
    │  "Find semantically similar: ..."  → v_sem    │
    │  "Find entities related to: ..."   → v_ent    │
    │  "Find procedures about: ..."      → v_proc   │
    │  "Find events related to: ..."     → v_temp   │
    └────┬──────────────────────────────────────────┘
         │ N vectors
         ▼
    ┌────────────────────────────────────────────────┐
    │  search_batch([v_sem, v_ent, v_proc, v_temp])  │
    │  → AMX INT8 tiles process N queries in 1 pass  │
    └────┬──────────────────────────────────────────┘
         │ N result sets
         ▼
    ┌────────────────────────────────────────────────┐
    │  Reciprocal Rank Fusion (RRF)                  │
    │  → Diverse, multi-perspective ranked results   │
    └────────────────────────────────────────────────┘

Usage::

    from openviking.retrieve.multi_aspect_retriever import MultiAspectRetriever

    retriever = MultiAspectRetriever(embedder=my_embedder)
    results = retriever.retrieve(query_text, engine=idx, topk=10)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from openviking.models.embedder.base import EmbedderBase, EmbedResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default aspect definitions for OpenClaw memory recall
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AspectPrompt:
    """An instruction prefix that steers the embedder toward a specific
    semantic perspective."""
    name: str
    instruction: str


#: Built-in aspects tuned for OpenClaw's memory/resource/skill recall.
#: These mirror the facets a user implicitly cares about when querying
#: a personal knowledge base.
DEFAULT_ASPECTS: Tuple[AspectPrompt, ...] = (
    AspectPrompt("semantic",   "Find memories semantically similar to: "),
    AspectPrompt("entity",     "Find memories mentioning entities in: "),
    AspectPrompt("temporal",   "Find memories about events related to: "),
    AspectPrompt("procedural", "Find memories about procedures for: "),
)


# ---------------------------------------------------------------------------
# Multi-aspect embedding helper
# ---------------------------------------------------------------------------

@dataclass
class MultiAspectEmbedResult:
    """Result of embedding a single text with N aspect instructions."""
    text: str
    aspects: List[AspectPrompt]
    vectors: List[List[float]]          # one dense vector per aspect
    embed_time_us: float = 0.0          # total embedding wall-clock µs

    @property
    def n_aspects(self) -> int:
        return len(self.vectors)


def embed_multi_aspect(
    embedder: EmbedderBase,
    text: str,
    aspects: Sequence[AspectPrompt] = DEFAULT_ASPECTS,
) -> MultiAspectEmbedResult:
    """Embed *text* once per aspect instruction.

    Each aspect prepends its instruction to the raw text before calling
    ``embedder.embed()``.  This is the standard approach for
    instruction-following embedding models (E5-instruct, BGE-en-ICL, …).

    Returns a :class:`MultiAspectEmbedResult` containing all N vectors.
    """
    vectors: List[List[float]] = []
    t0 = time.perf_counter()
    for asp in aspects:
        prefixed = asp.instruction + text
        result: EmbedResult = embedder.embed(prefixed, is_query=True)
        if result.dense_vector is not None:
            vectors.append(result.dense_vector)
        else:
            raise RuntimeError(
                f"Embedder returned no dense vector for aspect '{asp.name}'"
            )
    elapsed_us = (time.perf_counter() - t0) * 1e6
    return MultiAspectEmbedResult(
        text=text,
        aspects=list(aspects),
        vectors=vectors,
        embed_time_us=elapsed_us,
    )


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion (RRF)
# ---------------------------------------------------------------------------

@dataclass
class FusedResult:
    """A single item after RRF fusion across multiple aspect result sets."""
    label: int
    rrf_score: float
    contributing_aspects: List[str]     # which aspects contributed this label
    per_aspect_rank: Dict[str, int]     # aspect_name → 0-based rank (if present)


def reciprocal_rank_fusion(
    aspect_names: List[str],
    label_lists: List[List[int]],
    score_lists: List[List[float]],
    topk: int = 10,
    k: int = 60,
) -> List[FusedResult]:
    """Fuse N ranked result lists using Reciprocal Rank Fusion.

    RRF score for document d:  ``sum_over_aspects( 1 / (k + rank_i(d)) )``

    Args:
        aspect_names: name of each aspect (length N)
        label_lists: per-aspect label arrays (length N)
        score_lists: per-aspect score arrays (length N, unused by RRF but
            available for tie-breaking)
        topk: how many fused results to return
        k: RRF constant (default 60, as in the original paper)

    Returns:
        Top-k :class:`FusedResult` sorted by RRF score descending.
    """
    rrf_scores: Dict[int, float] = {}
    contributors: Dict[int, List[str]] = {}
    ranks: Dict[int, Dict[str, int]] = {}

    for asp_name, labels in zip(aspect_names, label_lists):
        for rank, label in enumerate(labels):
            rrf_scores[label] = rrf_scores.get(label, 0.0) + 1.0 / (k + rank + 1)
            contributors.setdefault(label, []).append(asp_name)
            ranks.setdefault(label, {})[asp_name] = rank

    sorted_items = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

    results = []
    for label, score in sorted_items[:topk]:
        results.append(FusedResult(
            label=label,
            rrf_score=score,
            contributing_aspects=contributors[label],
            per_aspect_rank=ranks[label],
        ))
    return results


# ---------------------------------------------------------------------------
# Main retriever class
# ---------------------------------------------------------------------------

class MultiAspectRetriever:
    """Retriever that embeds a query from multiple semantic perspectives
    and batch-searches all vectors in one engine call.

    This is designed to plug into the OpenClaw recall path as an alternative
    to (or enhancement of) :class:`HierarchicalRetriever`.  While the
    hierarchical retriever searches with one query vector across a directory
    tree, the multi-aspect retriever searches with N vectors across a flat
    scope — ideal for brute-force indexes where AMX batch acceleration
    provides significant speedup.

    Example::

        from openviking.retrieve.multi_aspect_retriever import (
            MultiAspectRetriever, DEFAULT_ASPECTS,
        )
        import openviking.storage.vectordb.engine as engine

        idx = engine.IndexEngine(config_json)
        # ... add data ...

        retriever = MultiAspectRetriever(embedder=my_embedder)

        # Serial mode (N × search)
        results = retriever.retrieve(
            "How does auth work?", engine=idx, topk=10, mode="serial",
        )

        # Batch mode (1 × search_batch, AMX accelerated)
        results = retriever.retrieve(
            "How does auth work?", engine=idx, topk=10, mode="batch",
        )
    """

    def __init__(
        self,
        embedder: EmbedderBase,
        aspects: Sequence[AspectPrompt] = DEFAULT_ASPECTS,
        rrf_k: int = 60,
    ):
        self.embedder = embedder
        self.aspects = list(aspects)
        self.rrf_k = rrf_k

    # -- public API ---------------------------------------------------------

    def retrieve(
        self,
        query: str,
        engine,                         # engine.IndexEngine
        topk: int = 10,
        dsl: str = "{}",
        mode: str = "batch",
    ) -> RetrieveResult:
        """Run multi-aspect retrieval.

        Args:
            query: raw query text
            engine: an ``IndexEngine`` instance with ``search()`` and
                ``search_batch()`` methods
            topk: per-aspect top-k (RRF will re-rank the union)
            dsl: DSL filter string (applied identically to all aspects)
            mode: ``"batch"`` (1 × search_batch) or ``"serial"``
                (N × search)

        Returns:
            A :class:`RetrieveResult` with fused results and timing.
        """
        # Step 1: Multi-aspect embedding
        multi = embed_multi_aspect(self.embedder, query, self.aspects)

        # Step 2: Vector search (batch or serial)
        import openviking.storage.vectordb.engine as eng

        search_results = []
        t0 = time.perf_counter()

        if mode == "batch":
            reqs = []
            for vec in multi.vectors:
                sq = eng.SearchRequest()
                sq.query = vec
                sq.topk = topk
                sq.dsl = dsl
                reqs.append(sq)
            search_results = engine.search_batch(reqs)
        else:
            for vec in multi.vectors:
                sq = eng.SearchRequest()
                sq.query = vec
                sq.topk = topk
                sq.dsl = dsl
                search_results.append(engine.search(sq))

        search_time_us = (time.perf_counter() - t0) * 1e6

        # Step 3: RRF fusion
        t1 = time.perf_counter()
        aspect_names = [a.name for a in self.aspects]
        label_lists = [r.labels for r in search_results]
        score_lists = [r.scores for r in search_results]
        fused = reciprocal_rank_fusion(
            aspect_names, label_lists, score_lists,
            topk=topk, k=self.rrf_k,
        )
        fusion_time_us = (time.perf_counter() - t1) * 1e6

        # Diversity metric: unique labels / (N × topk)
        all_labels = set()
        for labels in label_lists:
            all_labels.update(labels)
        total_possible = len(self.aspects) * topk
        diversity = len(all_labels) / total_possible if total_possible else 0.0

        return RetrieveResult(
            query=query,
            mode=mode,
            n_aspects=len(self.aspects),
            fused_results=fused,
            per_aspect_results=search_results,
            embed_time_us=multi.embed_time_us,
            search_time_us=search_time_us,
            fusion_time_us=fusion_time_us,
            diversity=diversity,
        )


@dataclass
class RetrieveResult:
    """Complete result of a multi-aspect retrieval."""
    query: str
    mode: str
    n_aspects: int
    fused_results: List[FusedResult]
    per_aspect_results: list           # List[SearchResult]
    embed_time_us: float
    search_time_us: float
    fusion_time_us: float
    diversity: float

    @property
    def total_time_us(self) -> float:
        return self.embed_time_us + self.search_time_us + self.fusion_time_us
