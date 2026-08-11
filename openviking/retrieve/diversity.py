# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stable diversity selection for relevance-ranked retrieval candidates."""

import asyncio
import hashlib
import math
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Sequence, Tuple
from urllib.parse import urlsplit

from openviking.core.namespace import classify_uri, uri_parts
from openviking.models.embedder.base import embed_compat
from openviking_cli.retrieve.diversity import DiversityOptions
from openviking_cli.retrieve.types import MatchedContext
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_LEVEL_SUFFIXES = (".abstract.md", ".overview.md")


@dataclass
class DiversitySelection:
    """Selected contexts plus request-level diversity counters."""

    contexts: List[MatchedContext]
    candidate_count: int
    suppressed_count: int
    fallback_used: bool = False


@dataclass
class _Candidate:
    context: MatchedContext
    original_index: int


def logical_resource_uri(uri: str) -> str:
    """Return the stable resource identity shared by L0, L1 and L2 forms."""
    for suffix in _LEVEL_SUFFIXES:
        if uri.endswith(suffix):
            return uri[: -len(suffix)]
    return uri


def diversity_group_key(uri: str, group_by: str) -> str:
    """Build a deterministic parent or source-root grouping key."""
    logical_uri = logical_resource_uri(uri).rstrip("/")
    if group_by == "parent":
        return logical_uri.rsplit("/", 1)[0] if "/" in logical_uri else logical_uri

    parsed = urlsplit(logical_uri)
    if parsed.scheme != "viking":
        path_parts = [part for part in parsed.path.split("/") if part]
        return path_parts[0] if path_parts else logical_uri

    parts = uri_parts(logical_uri)
    if not parts:
        return "viking://"
    classification = classify_uri(logical_uri)
    content_index = classification.content_index
    if content_index is None and parts[0] == "resources":
        content_index = 0
    source_end = min(len(parts), (content_index + 2) if content_index is not None else 2)
    return "viking://" + "/".join(parts[:source_end])


def _normalized_abstract_hash(abstract: str) -> str:
    normalized = re.sub(r"\s+", " ", abstract).strip().casefold()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _append_suppressed_uri(candidate: _Candidate, suppressed_uri: str) -> None:
    existing = candidate.context.deduplicated_from
    if suppressed_uri != candidate.context.uri and suppressed_uri not in existing:
        existing.append(suppressed_uri)


def _collapse_exact_candidates(candidates: Sequence[MatchedContext]) -> List[_Candidate]:
    ordered = sorted(enumerate(candidates), key=lambda item: (-item[1].score, item[0]))
    retained: List[_Candidate] = []
    retained_by_identity: Dict[Tuple[str, str], _Candidate] = {}
    for original_index, context in ordered:
        context_copy = replace(context, deduplicated_from=list(context.deduplicated_from))
        abstract_hash = _normalized_abstract_hash(context.abstract)
        identities = [("uri", logical_resource_uri(context.uri))]
        if abstract_hash:
            identities.append(("abstract", abstract_hash))
        duplicate = next(
            (
                retained_by_identity[identity]
                for identity in identities
                if identity in retained_by_identity
            ),
            None,
        )
        if duplicate is not None:
            _append_suppressed_uri(duplicate, context.uri)
            continue
        candidate = _Candidate(context=context_copy, original_index=original_index)
        retained.append(candidate)
        for identity in identities:
            retained_by_identity[identity] = candidate
    return retained


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


async def _embed_candidate_abstracts(
    candidates: Sequence[_Candidate], embedder: Any
) -> List[List[float]]:
    results = await asyncio.gather(
        *(embed_compat(embedder, candidate.context.abstract) for candidate in candidates)
    )
    vectors: List[List[float]] = []
    for result in results:
        if not result.dense_vector:
            raise ValueError("candidate embedding did not contain a dense vector")
        vectors.append(result.dense_vector)
    return vectors


def _select_by_relevance(
    candidates: Sequence[_Candidate], options: DiversityOptions, limit: int, apply_group_cap: bool
) -> List[_Candidate]:
    selected: List[_Candidate] = []
    group_counts: Dict[str, int] = {}
    for candidate in candidates:
        group_key = diversity_group_key(candidate.context.uri, options.group_by)
        if apply_group_cap and group_counts.get(group_key, 0) >= options.max_per_group:
            continue
        selected.append(candidate)
        group_counts[group_key] = group_counts.get(group_key, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def _select_mmr(
    candidates: Sequence[_Candidate],
    vectors: Sequence[Sequence[float]],
    options: DiversityOptions,
    limit: int,
) -> List[_Candidate]:
    selected_indices: List[int] = []
    remaining_indices = list(range(len(candidates)))
    group_counts: Dict[str, int] = {}
    apply_group_cap = options.strategy == "combined"

    while remaining_indices and len(selected_indices) < limit:
        best_index = None
        best_key = None
        for candidate_index in remaining_indices:
            candidate = candidates[candidate_index]
            group_key = diversity_group_key(candidate.context.uri, options.group_by)
            if apply_group_cap and group_counts.get(group_key, 0) >= options.max_per_group:
                continue
            maximum_similarity = max(
                (
                    _cosine_similarity(vectors[candidate_index], vectors[selected_index])
                    for selected_index in selected_indices
                ),
                default=0.0,
            )
            relevance_score = candidate.context.score
            mmr_score = (
                options.relevance_weight * relevance_score
                - (1.0 - options.relevance_weight) * maximum_similarity
            )
            ranking_key = (mmr_score, relevance_score, -candidate.original_index)
            if best_key is None or ranking_key > best_key:
                best_key = ranking_key
                best_index = candidate_index
        if best_index is None:
            break

        selected_candidate = candidates[best_index]
        selected_indices.append(best_index)
        remaining_indices.remove(best_index)
        selected_group = diversity_group_key(selected_candidate.context.uri, options.group_by)
        group_counts[selected_group] = group_counts.get(selected_group, 0) + 1

        near_duplicate_indices = [
            candidate_index
            for candidate_index in remaining_indices
            if _cosine_similarity(vectors[best_index], vectors[candidate_index])
            >= options.similarity_threshold
        ]
        for duplicate_index in near_duplicate_indices:
            _append_suppressed_uri(selected_candidate, candidates[duplicate_index].context.uri)
            remaining_indices.remove(duplicate_index)

    return [candidates[candidate_index] for candidate_index in selected_indices]


def _build_selection(
    candidates: Sequence[MatchedContext],
    selected: Sequence[_Candidate],
    fallback_used: bool = False,
) -> DiversitySelection:
    contexts = [candidate.context for candidate in selected]
    return DiversitySelection(
        contexts=contexts,
        candidate_count=len(candidates),
        suppressed_count=max(0, len(candidates) - len(contexts)),
        fallback_used=fallback_used,
    )


async def select_diverse_contexts(
    candidates: Sequence[MatchedContext],
    *,
    options: DiversityOptions,
    embedder: Any,
    limit: int,
) -> DiversitySelection:
    """Select a stable, diverse subset from relevance-ranked candidates."""
    if limit <= 0 or not candidates:
        return DiversitySelection([], len(candidates), 0)

    collapsed = _collapse_exact_candidates(candidates)
    if options.strategy == "group_limit":
        selected = _select_by_relevance(collapsed, options, limit, apply_group_cap=True)
        return _build_selection(candidates, selected)

    try:
        vectors = await _embed_candidate_abstracts(collapsed, embedder)
    except Exception as exc:
        logger.warning("Diversity embedding failed; using relevance fallback: %s", exc)
        selected = _select_by_relevance(
            collapsed,
            options,
            limit,
            apply_group_cap=options.strategy == "combined",
        )
        return _build_selection(candidates, selected, fallback_used=True)

    selected = _select_mmr(collapsed, vectors, options, limit)
    return _build_selection(candidates, selected)
