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
OPTION_ONLY_NAMES = {
    "processing_mode",
    "telemetry",
}


@pytest.mark.parametrize("source_path", MIGRATED_BENCHMARKS)
def test_benchmark_sdk_calls_keep_advanced_fields_in_options(source_path: Path):
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in OPTIONS_METHODS:
            continue
        option_only_keywords = sorted(
            keyword.arg
            for keyword in node.keywords
            if keyword.arg is not None and keyword.arg in OPTION_ONLY_NAMES
        )
        if option_only_keywords:
            violations.append((node.lineno, node.func.attr, option_only_keywords))

    assert not violations, f"{source_path}: SDK fields must stay in options: {violations}"
