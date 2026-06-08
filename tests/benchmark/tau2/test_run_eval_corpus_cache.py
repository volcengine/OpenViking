from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_run_eval():
    repo = Path(__file__).resolve().parents[3]
    scripts = repo / "benchmark" / "tau2" / "llm" / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(
        "tau2_run_eval_under_test",
        scripts / "run_eval.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corpus_session_commit_concurrency_prefers_openviking_config():
    run_eval = _load_run_eval()

    assert (
        run_eval._corpus_session_commit_concurrency(
            {
                "benchmark": {"corpus_session_commit_concurrency": 2},
                "openviking": {"corpus_session_commit_concurrency": 4},
            }
        )
        == 4
    )
    assert (
        run_eval._corpus_session_commit_concurrency(
            {
                "benchmark": {"corpus_session_commit_concurrency": 3},
                "openviking": {},
            }
        )
        == 3
    )


@pytest.mark.parametrize("bad_value", [0, -1, "not-an-int"])
def test_corpus_session_commit_concurrency_rejects_invalid_values(bad_value):
    run_eval = _load_run_eval()

    with pytest.raises(ValueError, match="corpus_session_commit_concurrency"):
        run_eval._corpus_session_commit_concurrency(
            {
                "benchmark": {"corpus_session_commit_concurrency": bad_value},
                "openviking": {},
            }
        )


def test_prepare_memory_corpus_reuses_cache_by_requested_commit_concurrency(tmp_path):
    run_eval = _load_run_eval()
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    manifest_path = corpus_dir / "corpus_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "train_transcript_format": "openviking_text",
                "train_include_system_prompt": False,
                "train_skip_failed_sessions": True,
                "train_tool_output_max_chars": 5000,
                "corpus_session_commit_concurrency": 8,
                "corpus_session_commit_worker_count": 2,
            }
        ),
        encoding="utf-8",
    )
    cell = {
        "domain": "retail",
        "strategy_id": "s1",
        "corpus_id": "c1",
        "corpus_key": "retail_c1",
        "corpus_dir": str(corpus_dir),
        "train_transcript_format": "openviking_text",
        "train_include_system_prompt": False,
        "train_skip_failed_sessions": True,
        "train_tool_output_max_chars": 5000,
        "corpus_session_commit_concurrency": 8,
    }

    row = run_eval._prepare_memory_corpus(cell, tmp_path, tmp_path / "out")

    assert row["reused"] is True
    assert row["corpus_session_commit_concurrency"] == 8
    assert row["corpus_session_commit_worker_count"] == 2

    mismatched = dict(cell, corpus_session_commit_concurrency=4)
    with pytest.raises(RuntimeError, match="corpus_session_commit_concurrency mismatch"):
        run_eval._prepare_memory_corpus(mismatched, tmp_path, tmp_path / "out2")
