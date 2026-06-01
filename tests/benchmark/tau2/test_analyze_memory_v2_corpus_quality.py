from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_quality_module():
    repo = Path(__file__).resolve().parents[3]
    script = repo / "benchmark" / "tau2" / "llm" / "scripts" / "analyze_memory_v2_corpus_quality.py"
    sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location("tau2_corpus_quality_under_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_root_uri_uses_memories_path_segment():
    module = _load_quality_module()

    assert (
        module._memory_root_uri("viking://agent/memories-agent/memories/experiences")
        == "viking://agent/memories-agent/memories"
    )


def test_run_plan_manifests_falls_back_to_corpus_dir(tmp_path):
    module = _load_quality_module()
    manifest = tmp_path / "corpus" / "corpus_manifest.json"
    manifest.parent.mkdir()
    manifest.write_text("{}", encoding="utf-8")
    run_plan = tmp_path / "run_plan.json"
    run_plan.write_text(
        json.dumps({"cells": [{"corpus_dir": str(manifest.parent)}]}),
        encoding="utf-8",
    )

    assert module._run_plan_manifests(run_plan) == [manifest]


def test_existing_manifests_fails_fast_on_missing(tmp_path):
    module = _load_quality_module()
    existing = tmp_path / "existing.json"
    missing = tmp_path / "missing.json"
    existing.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="missing corpus_manifest"):
        module._existing_manifests([existing, missing], allow_missing=False)

    assert module._existing_manifests([existing, missing], allow_missing=True) == [existing]


def test_summarize_health_uses_graph_health_issue_fields(tmp_path):
    module = _load_quality_module()
    manifest = tmp_path / "corpus_manifest.json"
    manifest.write_text(
        json.dumps({"domain": "retail", "committed_session_count": 2}),
        encoding="utf-8",
    )

    row = module._summarize_health(
        manifest,
        {
            "healthy": False,
            "memory_type_counts": {"experiences": 3, "trajectories": 4},
            "parse_error_count": 1,
            "owner_mismatch_count": 2,
            "broken_endpoint_count": 3,
            "missing_backlink_count": 4,
            "missing_forward_link_count": 5,
            "experience_quality": {
                "source_links_per_experience": {"avg": 2.0, "linkless": 0},
                "duplicate_exact_source_set_count": 1,
            },
        },
    )

    assert row["issue_total"] == 15
    assert row["issue_breakdown"]["owner_mismatch_count"] == 2
    assert row["exp_per_session"] == 1.5
