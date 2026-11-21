# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SAG2 passage-term-passage retrieval using OV's canonical tenant scope."""

from __future__ import annotations

import asyncio
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

from openviking.storage.vectordb_adapters.sag2_lexical import (
    Sag2TenantScope,
    Term,
    match_query,
    name_phrases,
    normalized_alias,
    quote,
    sentences,
    tokenize,
)

_SAG2_OUTPUT_FIELDS = None  # Full records stay internal until OV result conversion.


def _content(candidate: dict[str, Any]) -> str:
    if candidate.get("level") is None or int(candidate["level"]) == 2:
        return str(candidate.get("content") or candidate.get("abstract") or "")
    return str(candidate.get("abstract") or "")


def interleave(lists: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for rank in range(limit):
        for candidates in lists:
            if rank >= len(candidates):
                continue
            candidate = candidates[rank]
            record_id = str(candidate["id"])
            if record_id in seen:
                continue
            seen.add(record_id)
            value = dict(candidate)
            value["_order"] = len(result)
            value["_first_rrf"] = 1.0 / (60 + len(result) + 1)
            result.append(value)
            if len(result) == limit:
                return result
    return result


def _rank(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda item: (
            -float(item.get("_score", 0.0)),
            -float(item.get("_first_rrf", 0.0)),
            str(item["id"]),
        ),
    )


async def _title_corpus(scope: Sag2TenantScope) -> tuple[dict[str, str], set[str]]:
    cached = getattr(scope, "_ov_title_corpus", None)
    if cached is not None:
        return cached
    aliases: dict[str, set[str]] = {}
    titles: set[str] = set()
    for row in await scope.titles():
        name = str(row.get("name") or "").strip()
        uri = str(row.get("uri") or "")
        if not name:
            continue
        canonical = " ".join(tokenize(name))
        if not canonical:
            continue
        titles.add(canonical)
        values = {name, uri.rstrip("/").rsplit("/", 1)[-1]}
        for value in values:
            alias = normalized_alias(value)
            if alias and alias != normalized_alias(name):
                aliases.setdefault(alias, set()).add(canonical)
    result = (
        {key: next(iter(values)) for key, values in aliases.items() if len(values) == 1},
        titles,
    )
    scope._ov_title_corpus = result
    return result


async def repair_query(scope: Sag2TenantScope, query: str, excluded_ids: Iterable[str] = ()) -> str:
    """Apply only unique, scope-local aliases when the original has no match."""
    original_expression = match_query(query)
    if original_expression and await scope.has_match(original_expression, excluded_ids):
        return query
    aliases, _ = await _title_corpus(scope)
    if not aliases:
        return query
    repaired = query
    replaced = False
    for alias, canonical in sorted(aliases.items(), key=lambda item: -len(item[0])):
        pattern = re.compile(rf"(?<![\w]){re.escape(alias)}(?![\w])", re.IGNORECASE)
        if pattern.search(repaired):
            repaired = pattern.sub(canonical, repaired)
            replaced = True
    if not replaced:
        return query
    repaired_expression = match_query(repaired)
    if not repaired_expression:
        return query
    if await scope.has_match(repaired_expression):
        return repaired
    return query


async def _resolve_terms(
    scope: Sag2TenantScope,
    terms: list[Term],
    seen: set[str],
    max_query_terms: int,
) -> list[Term]:
    resolved: list[Term] = []
    titles: set[str] = set()
    titles_loaded = False
    for item in terms[:max_query_terms]:
        direct_expression = match_query(item.term)
        if direct_expression and await scope.has_match(direct_expression, seen):
            resolved.append(item)
            continue
        if not titles_loaded:
            _, titles = await _title_corpus(scope)
            titles_loaded = True
        candidates = [item.term]
        if item.kind == "name_phrase":
            words = item.term.split()
            subphrases = [
                " ".join(words[start:end])
                for start in range(len(words))
                for end in range(start + 1, len(words) + 1)
                if " ".join(words[start:end]) in titles
            ]
            candidates.extend(sorted(set(subphrases), key=lambda value: (-len(value), value)))
        for value in candidates:
            repaired = await repair_query(scope, value, seen)
            expression = match_query(repaired)
            if expression and await scope.has_match(expression, seen):
                resolved.append(Term(repaired, item.score, item.kind))
                break
    return resolved


async def expansion_terms(
    scope: Sag2TenantScope,
    query: str,
    seeds: list[dict[str, Any]],
    limit: int,
) -> list[Term]:
    query_words = tokenize(query)
    query_set = set(query_words)
    max_score = max((float(seed.get("_score", 0.0)) for seed in seeds), default=0.0)
    passages = []
    vocabulary: set[str] = set(query_set)
    for seed in seeds:
        parts = []
        for sentence in sentences(_content(seed)):
            names = name_phrases(sentence)
            ungrouped = []
            end = 0
            for start, stop, _ in names:
                ungrouped.append(sentence[end:start])
                ungrouped.append(" ")
                end = stop
            ungrouped.append(sentence[end:])
            words = tokenize("".join(ungrouped))
            words.extend(term for _, _, term in names)
            vocabulary.update(words)
            parts.append((sentence, words, names))
        passages.append((seed, parts))
    total, frequencies = await scope.term_frequencies(vocabulary)

    def idf(term: str) -> float:
        df = frequencies.get(term, 0)
        return math.log(1.0 + (total - df + 0.5) / (df + 0.5))

    ranked: dict[str, Term] = {}
    for seed, parts in passages:
        weight = 0.5 + 0.5 * (float(seed.get("_score", 0.0)) / max_score if max_score > 0 else 0.0)
        counts: OrderedDict[str, int] = OrderedDict()
        contexts: dict[str, float] = {}
        name_terms: set[str] = set()
        for sentence, words, names in parts:
            unique: set[str] = set()
            context = 0.0
            for word in tokenize(sentence):
                if word not in unique and word in query_set:
                    context += idf(word)
                unique.add(word)
            for word in words:
                counts[word] = counts.get(word, 0) + 1
                contexts[word] = max(contexts.get(word, 0.0), context)
            name_terms.update(term for _, _, term in names)
        for term, tf in counts.items():
            if all(word in query_set for word in tokenize(term)) or frequencies.get(term, 0) == 0:
                continue
            score = weight * (1 + math.log(tf)) * idf(term) * (1 + contexts[term])
            previous = ranked.get(term)
            if previous is None or score > previous.score:
                ranked[term] = Term(term, score, "name_phrase" if term in name_terms else "word")
    return sorted(ranked.values(), key=lambda term: (-term.score, term.term))[:limit]


@dataclass
class RoundTrace:
    hop: int
    candidates: int
    terms: int
    judgments: int


async def _retrieve(
    scope: Sag2TenantScope,
    question: str,
    config: Any,
    scorer: Any,
    *,
    max_hops: int,
) -> tuple[list[dict[str, Any]], list[RoundTrace]]:
    repaired = await repair_query(scope, question)
    expression = match_query(repaired, config.max_query_terms)
    if not expression:
        return [], []
    retained: list[dict[str, Any]] = []
    frontier: list[dict[str, Any]] = []
    expanded: set[str] = set()
    seen: set[str] = set()
    carried: OrderedDict[str, dict[str, Any]] = OrderedDict()
    protected: OrderedDict[str, dict[str, Any]] = OrderedDict()
    latest: OrderedDict[str, dict[str, Any]] = OrderedDict()
    traces: list[RoundTrace] = []
    judgments = 0

    for hop in range(max_hops + 1):
        await scope.check_epoch()
        if hop > 0 and not frontier:
            break
        terms = (
            await _resolve_terms(
                scope,
                await expansion_terms(scope, question, frontier, config.expansion_term_limit),
                seen,
                config.max_query_terms,
            )
            if hop > 0
            else []
        )
        if hop > 0 and not terms:
            break
        if hop == 0:
            candidates = await scope.query(expression, limit=config.candidate_k)
            for order, candidate in enumerate(candidates[: config.candidate_k]):
                candidate["_order"] = order
                candidate["_first_rrf"] = 1.0 / (60 + order + 1)
                if len(protected) < config.protected_k:
                    protected[str(candidate["id"])] = candidate
        else:
            ranking = " OR ".join([*(quote(term.term) for term in terms), f"({expression})"])
            lists = []
            for term in terms:
                lists.append(
                    await scope.query(
                        ranking,
                        limit=config.candidate_k,
                        required_term=term.term,
                        excluded_ids=seen,
                    )
                )
            candidates = interleave(lists, config.candidate_k)
        if not candidates:
            break
        for candidate in frontier:
            carried[str(candidate["id"])] = candidate
        pool: OrderedDict[str, dict[str, Any]] = OrderedDict()
        if hop > 0:
            pool.update(protected)
            pool.update(carried)
        pool.update((str(candidate["id"]), candidate) for candidate in candidates)
        evidence: list[dict[str, Any]] = []
        if hop > 0:
            combined = OrderedDict(carried)
            combined.update(
                (str(candidate["id"]), candidate) for candidate in candidates[: config.seed_k]
            )
            evidence = list(combined.values())
        if hop == 2:
            count = len(pool)
            history = OrderedDict(
                (record_id, candidate)
                for record_id, candidate in pool.items()
                if record_id in latest
            )
            incoming = [
                candidate for candidate in pool.values() if str(candidate["id"]) not in latest
            ][:20]
            previous = sorted(
                latest.values(), key=lambda item: (-float(item.get("_score", 0.0)), str(item["id"]))
            )
            history.update((str(candidate["id"]), candidate) for candidate in previous)
            pool = OrderedDict()
            for candidate in [*list(history.values())[: max(0, count - len(incoming))], *incoming]:
                pool[str(candidate["id"])] = candidate
            evidence = [
                {**candidate, "previous_jev_score": candidate.get("_score", 0.0)}
                for candidate in retained[:10]
            ]
        if judgments + len(pool) > config.max_judgments:
            break
        traces.append(RoundTrace(hop, len(candidates), len(terms), len(pool)))
        scored = await scorer.score(question, list(pool.values()), evidence)
        retained = _rank(scored)
        judgments += len(pool)
        latest.update((str(candidate["id"]), candidate) for candidate in retained)
        await scope.check_epoch()
        seen.update(str(candidate["id"]) for candidate in retained)
        frontier = [candidate for candidate in retained if str(candidate["id"]) not in expanded][
            : config.seed_k
        ]
        expanded.update(str(candidate["id"]) for candidate in frontier)

    return retained[: config.candidate_k], traces


async def retrieve_sag2(
    scope: Sag2TenantScope,
    question: str,
    config: Any,
    scorer: Any,
    *,
    max_hops: int,
) -> tuple[list[dict[str, Any]], list[RoundTrace]]:
    return await asyncio.wait_for(
        _retrieve(scope, question, config, scorer, max_hops=max_hops),
        timeout=config.total_timeout_s,
    )


async def quick_sag2(scope: Sag2TenantScope, question: str, config: Any) -> list[dict[str, Any]]:
    async def run():
        repaired = await repair_query(scope, question)
        expression = match_query(repaired, config.max_query_terms)
        if not expression:
            return []
        return await scope.query(expression, limit=config.candidate_k)

    return await asyncio.wait_for(run(), timeout=config.total_timeout_s)
