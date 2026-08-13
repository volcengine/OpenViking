from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MIGRATED_BENCHMARKS = [
    REPOSITORY_ROOT / "benchmark/RAG/src/core/vector_store.py",
    REPOSITORY_ROOT / "benchmark/retrieval/grep/vikingdb_bm25/performance/step1_add_resource.py",
    REPOSITORY_ROOT / "benchmark/retrieval/grep/vikingdb_bm25/effectiveness/step1_add_resource.py",
]
OPTIONS_METHODS = {"add_resource", "find"}
LEGACY_OPTION_NAMES = {
    "parent",
    "processing_mode",
    "reason",
    "target_uri",
    "telemetry",
    "wait",
}


@pytest.mark.parametrize("source_path", MIGRATED_BENCHMARKS)
def test_benchmark_sdk_calls_use_options_dict(source_path: Path):
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in OPTIONS_METHODS:
            continue
        legacy_keywords = sorted(
            keyword.arg
            for keyword in node.keywords
            if keyword.arg is not None and keyword.arg in LEGACY_OPTION_NAMES
        )
        if legacy_keywords:
            violations.append((node.lineno, node.func.attr, legacy_keywords))

    assert not violations, f"{source_path}: legacy SDK option keywords: {violations}"
