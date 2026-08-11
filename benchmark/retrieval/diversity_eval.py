# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Offline quality and latency evaluation for diversity-aware retrieval."""

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

# Initialize the service package before the eager retrieval package exports are
# loaded; this avoids an existing queue/service circular-import edge in a
# standalone interpreter while keeping the evaluator on the production selector.
import openviking.service  # noqa: F401
from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.diversity import select_diverse_contexts
from openviking_cli.retrieve.diversity import DiversityOptions
from openviking_cli.retrieve.types import ContextType, MatchedContext


class FixtureEmbedder:
    """Return dense vectors stored in the deterministic fixture."""

    def __init__(self, vectors: Dict[str, List[float]]):
        self.vectors = vectors

    def prepare_embedding_input(self, content: str) -> str:
        return content

    async def embed_async(self, content: str, *, is_query: bool = False) -> EmbedResult:
        return EmbedResult(dense_vector=self.vectors[content])


def _percentile_95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1]


def _metrics(cases: Sequence[Dict[str, Any]], selections: Sequence[List[str]]) -> Dict[str, float]:
    recalls: List[float] = []
    ndcgs: List[float] = []
    duplicate_rates: List[float] = []
    unique_source_rates: List[float] = []
    for case, selected_uris in zip(cases, selections, strict=True):
        relevant = set(case["relevant_uris"])
        by_uri = {candidate["uri"]: candidate for candidate in case["candidates"]}
        recalls.append(len(relevant.intersection(selected_uris)) / max(1, len(relevant)))
        gains = [1.0 if uri in relevant else 0.0 for uri in selected_uris]
        dcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))
        ideal = sum(
            1.0 / math.log2(rank + 2) for rank in range(min(len(relevant), len(selected_uris)))
        )
        ndcgs.append(dcg / ideal if ideal else 1.0)
        duplicate_keys = [by_uri[uri]["duplicate_key"] for uri in selected_uris]
        duplicate_rates.append(
            (len(duplicate_keys) - len(set(duplicate_keys))) / max(1, len(duplicate_keys))
        )
        sources = [by_uri[uri]["source"] for uri in selected_uris]
        unique_source_rates.append(len(set(sources)) / max(1, len(sources)))
    return {
        "recall_at_k": statistics.fmean(recalls),
        "ndcg_at_k": statistics.fmean(ndcgs),
        "duplicate_rate_at_k": statistics.fmean(duplicate_rates),
        "unique_source_rate_at_k": statistics.fmean(unique_source_rates),
    }


async def _evaluate_cases(cases: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    baseline_selections: List[List[str]] = []
    diversity_selections: List[List[str]] = []
    baseline_latencies: List[float] = []
    diversity_latencies: List[float] = []
    for case in cases:
        candidates = [
            MatchedContext(
                uri=item["uri"],
                context_type=ContextType.RESOURCE,
                abstract=item["abstract"],
                score=item["score"],
            )
            for item in case["candidates"]
        ]
        started = time.perf_counter()
        baseline = candidates[: case["k"]]
        baseline_latencies.append((time.perf_counter() - started) * 1000)
        baseline_selections.append([item.uri for item in baseline])
        vectors = {item["abstract"]: item["vector"] for item in case["candidates"]}
        started = time.perf_counter()
        selection = await select_diverse_contexts(
            candidates,
            options=DiversityOptions(
                strategy="combined",
                relevance_weight=0.7,
                max_per_group=1,
                similarity_threshold=0.98,
            ),
            embedder=FixtureEmbedder(vectors),
            limit=case["k"],
        )
        diversity_latencies.append((time.perf_counter() - started) * 1000)
        diversity_selections.append([item.uri for item in selection.contexts])
    baseline_metrics = _metrics(cases, baseline_selections)
    diversity_metrics = _metrics(cases, diversity_selections)
    baseline_metrics["p95_latency_ms"] = _percentile_95(baseline_latencies)
    diversity_metrics["p95_latency_ms"] = _percentile_95(diversity_latencies)
    return {"baseline": baseline_metrics, "diversity": diversity_metrics}


def evaluate_cases(cases: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Evaluate fixed cases without network or external model calls."""
    return asyncio.run(_evaluate_cases(cases))


def load_cases(path: Path) -> List[Dict[str, Any]]:
    """Load non-empty JSONL records from a fixture path."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate_cases(load_cases(args.input))
    print(json.dumps(report, indent=2, sort_keys=True))
    baseline = report["baseline"]
    diversity = report["diversity"]
    duplicate_improvement = 1.0 - (
        diversity["duplicate_rate_at_k"] / baseline["duplicate_rate_at_k"]
        if baseline["duplicate_rate_at_k"]
        else 0.0
    )
    gates_pass = all(
        [
            duplicate_improvement >= 0.30,
            diversity["unique_source_rate_at_k"] >= baseline["unique_source_rate_at_k"],
            diversity["recall_at_k"] >= baseline["recall_at_k"] - 0.02,
            diversity["ndcg_at_k"] >= baseline["ndcg_at_k"] - 0.02,
            diversity["p95_latency_ms"] - baseline["p95_latency_ms"] <= 25.0,
        ]
    )
    return 0 if gates_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
