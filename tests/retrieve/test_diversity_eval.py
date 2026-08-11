# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

from benchmark.retrieval.diversity_eval import evaluate_cases, load_cases


def test_evaluate_cases_reports_required_metrics_and_improves_duplicates():
    fixture = Path("benchmark/retrieval/fixtures/diversity_cases.jsonl")
    report = evaluate_cases(load_cases(fixture))
    assert set(report) == {"baseline", "diversity"}
    assert set(report["baseline"]) == {
        "recall_at_k",
        "ndcg_at_k",
        "duplicate_rate_at_k",
        "unique_source_rate_at_k",
        "p95_latency_ms",
    }
    assert report["diversity"]["duplicate_rate_at_k"] == 0.0
    assert report["diversity"]["recall_at_k"] >= report["baseline"]["recall_at_k"]
