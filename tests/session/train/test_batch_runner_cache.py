# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from pathlib import Path

from openviking.session.train.batch_runner import (
    BatchTrainEvalConfig,
    _baseline_cache_key,
    _clean_result_dir,
    _load_baseline_cache,
    _result_base_dir,
    _write_baseline_cache,
)


def test_baseline_cache_key_depends_on_trials_eval_index_and_split():
    base = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_index=25,
        trials=8,
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert _baseline_cache_key(base) == _baseline_cache_key(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_index=25,
            trials=8,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )
    assert _baseline_cache_key(base) != _baseline_cache_key(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_index=25,
            trials=1,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )
    assert _baseline_cache_key(base) != _baseline_cache_key(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_index=10,
            trials=8,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )
    assert _baseline_cache_key(base) != _baseline_cache_key(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_split="train",
            eval_index=25,
            trials=8,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )


def test_baseline_cache_round_trips_report(tmp_path: Path):
    cache_path = tmp_path / "baseline.json"
    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_index=1,
        trials=1,
        benchmark_service_url="http://127.0.0.1:1944",
    )
    report = {
        "epoch": -1,
        "rollout_stage": "baseline_test_rollout",
        "case_count": 1,
        "accuracy": 1.0,
        "passed_count": 1,
        "average_reward": 1.0,
    }

    _write_baseline_cache(cache_path, report, config=config)
    loaded = _load_baseline_cache(cache_path)

    assert loaded is not None
    assert loaded["baseline_cache_hit"] is True
    assert loaded["baseline_cache_path"] == str(cache_path)
    assert loaded["accuracy"] == 1.0


def test_clean_result_preserves_baseline_cache(tmp_path: Path, monkeypatch):
    import openviking.session.train.batch_runner as batch_runner

    monkeypatch.setattr(batch_runner, "_repo_root", lambda: tmp_path)
    result_dir = tmp_path / "result" / "tau2" / "train"
    cache_file = result_dir / "cache" / "baseline" / "baseline.json"
    top_level_file = result_dir / "latest_rollouts"
    cache_file.parent.mkdir(parents=True)
    top_level_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{}", encoding="utf-8")
    top_level_file.write_text("{}", encoding="utf-8")

    _clean_result_dir(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )

    assert cache_file.exists()
    assert top_level_file.exists()


def test_clean_result_preserves_non_run_dirs(tmp_path: Path, monkeypatch):
    import openviking.session.train.batch_runner as batch_runner

    monkeypatch.setattr(batch_runner, "_repo_root", lambda: tmp_path)
    result_dir = tmp_path / "result" / "tau2" / "train"
    opt_file = result_dir / "opt" / "checkpoint.json"
    top_level_file = result_dir / "notes.json"
    opt_file.parent.mkdir(parents=True)
    opt_file.write_text("{}", encoding="utf-8")
    top_level_file.parent.mkdir(parents=True, exist_ok=True)
    top_level_file.write_text("{}", encoding="utf-8")

    _clean_result_dir(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )

    assert opt_file.exists()
    assert top_level_file.exists()


def test_clean_result_keeps_recent_run_dirs(tmp_path: Path, monkeypatch):
    import os

    import openviking.session.train.batch_runner as batch_runner

    monkeypatch.setattr(batch_runner, "_repo_root", lambda: tmp_path)
    result_dir = tmp_path / "result" / "tau2" / "train"
    cache_file = result_dir / "cache" / "baseline" / "baseline.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("{}", encoding="utf-8")

    legacy_run_dir = result_dir / "airline_20260101_000000"
    legacy_run_dir.mkdir(parents=True)
    (legacy_run_dir / "report.json").write_text("{}", encoding="utf-8")

    prefixed_non_run_dir = result_dir / "run_notes"
    prefixed_non_run_dir.mkdir(parents=True)
    (prefixed_non_run_dir / "note.txt").write_text("{}", encoding="utf-8")

    for index in range(7):
        run_dir = result_dir / f"run_airline_20260101_00000{index}"
        run_dir.mkdir(parents=True)
        (run_dir / "report.json").write_text("{}", encoding="utf-8")
        os.utime(run_dir, (1000 + index, 1000 + index))

    _clean_result_dir(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            benchmark_service_url="http://127.0.0.1:1944",
            keep_recent_results=5,
            run_timestamp="20260101_999999",
        )
    )

    remaining = sorted(path.name for path in result_dir.iterdir() if path.is_dir())

    assert "cache" in remaining
    assert "airline_20260101_000000" in remaining
    assert "run_notes" in remaining
    assert "run_airline_20260101_000000" not in remaining
    assert "run_airline_20260101_000001" not in remaining
    assert "run_airline_20260101_000002" in remaining
    assert "run_airline_20260101_000006" in remaining


def test_keep_recent_results_must_be_non_negative():
    import pytest

    with pytest.raises(ValueError, match="keep_recent_results must be >= 0"):
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            benchmark_service_url="http://127.0.0.1:1944",
            keep_recent_results=-1,
        )


def test_case_loader_uses_sample_index_filter():
    from openviking.session.train.batch_runner import _case_loader

    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        train_index=7,
        eval_index=3,
        benchmark_service_url="http://127.0.0.1:1944",
    )

    train_loader = _case_loader(config, split="train", sample_index=config.train_index)
    eval_loader = _case_loader(config, split="test", sample_index=config.eval_index)
    all_loader = _case_loader(config, split="train", sample_index=None)

    assert train_loader.limit is None
    assert eval_loader.limit is None
    assert train_loader.split == "train"
    assert eval_loader.split == "test"
    assert train_loader.filters == {"task_indices": [7]}
    assert eval_loader.filters == {"task_indices": [3]}
    assert all_loader.filters == {}


def test_sample_indices_are_zero_based_and_may_be_zero():
    BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        train_index=0,
        eval_index=0,
        benchmark_service_url="http://127.0.0.1:1944",
    )

    import pytest

    with pytest.raises(ValueError, match="train_index must be >= 0"):
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            train_index=-1,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    with pytest.raises(ValueError, match="eval_index must be >= 0"):
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_index=-1,
            benchmark_service_url="http://127.0.0.1:1944",
        )


def test_eval_split_normalization_and_validation():
    train_config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_split="TRAIN",
        benchmark_service_url="http://127.0.0.1:1944",
    )
    none_config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_split="none",
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert train_config.eval_split == "train"
    assert none_config.eval_split is None

    import pytest

    with pytest.raises(ValueError, match="eval_split must be train, test, or none"):
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_split="dev",
            benchmark_service_url="http://127.0.0.1:1944",
        )


def test_eval_loader_can_target_train_split():
    from openviking.session.train.batch_runner import _case_loader

    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_split="train",
        eval_index=14,
        benchmark_service_url="http://127.0.0.1:1944",
    )

    loader = _case_loader(config, split=config.eval_split, sample_index=config.eval_index)

    assert loader.split == "train"
    assert loader.filters == {"task_indices": [14]}


def test_result_dir_name_selects_result_subdirectory(tmp_path: Path, monkeypatch):
    import openviking.session.train.batch_runner as batch_runner

    monkeypatch.setattr(batch_runner, "_repo_root", lambda: tmp_path)
    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        benchmark_service_url="http://127.0.0.1:1944",
        result_dir_name="train_1",
    )

    assert _result_base_dir(config) == tmp_path / "result" / "tau2" / "train_1"


def test_result_dir_name_must_not_be_empty():
    import pytest

    with pytest.raises(ValueError, match="result_dir_name must not be empty"):
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            benchmark_service_url="http://127.0.0.1:1944",
            result_dir_name=" ",
        )
