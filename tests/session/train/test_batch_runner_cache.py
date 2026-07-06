# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from pathlib import Path

from openviking.session.train.batch_runner import (
    BatchTrainEvalConfig,
    CachedEpochZeroTrainRolloutExecutor,
    _baseline_cache_key,
    _clean_result_dir,
    _load_baseline_cache,
    _print_baseline_cache_hit,
    _result_base_dir,
    _write_baseline_cache,
)


def _case():
    from openviking.session.train.domain import Case, Rubric, RubricCriterion

    return Case(
        name="case-1",
        task_signature="booking_duplicate",
        input={"user_request": "cancel duplicate booking"},
        rubric=Rubric(
            name="booking_rubric",
            description="Cancel only the verified duplicate booking.",
            criteria=[
                RubricCriterion(
                    name="verify_duplicate",
                    description="Verify duplicate status first.",
                    required=True,
                    weight=1.0,
                )
            ],
        ),
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


def test_train_split_baseline_cache_key_uses_effective_eval_index():
    implicit = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_split="train",
        train_index=[5, 6],
        trials=8,
        benchmark_service_url="http://127.0.0.1:1944",
    )
    explicit = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_split="train",
        train_index=[5, 6],
        eval_index=[5, 6],
        trials=8,
        benchmark_service_url="http://127.0.0.1:1944",
    )
    all_train = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_split="train",
        trials=8,
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert _baseline_cache_key(implicit) == _baseline_cache_key(explicit)
    assert _baseline_cache_key(implicit) != _baseline_cache_key(all_train)


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
    assert config.train_index == [7]
    assert config.eval_index == [3]
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


def test_print_baseline_cache_hit_formats_as_stage_label(capsys, tmp_path: Path):
    _print_baseline_cache_hit(
        {
            "rollout_stage": "baseline_test_rollout",
            "trial_count": 8,
            "accuracy_mean": 0.55,
            "accuracy_std": 0.05,
            "case_count_per_trial": 20,
        },
        tmp_path / "airline_test_index-all_trials-8_523a9bffb6c24543.json",
    )

    output = capsys.readouterr().out
    assert "[BASELINE_TEST_ROLLOUT]" in output
    assert "baseline_cache_hit=1" in output
    assert "accuracy=" in output
    assert "55.00%" in output
    assert "± " in output
    assert "5.00pp" in output
    assert "trials=8 cases_per_trial=20" in output
    assert "(from cache: airline_test_index-all_trials-8_523a9bffb6c24543.json)" in output


def test_train_rollout_cache_key_depends_on_train_trials_and_index():
    from openviking.session.train.batch_runner import _train_rollout_cache_key_prefix

    base = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        train_index=25,
        train_trials=1,
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert _train_rollout_cache_key_prefix(base) == _train_rollout_cache_key_prefix(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            train_index=25,
            train_trials=1,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )
    assert _train_rollout_cache_key_prefix(base) != _train_rollout_cache_key_prefix(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            train_index=25,
            train_trials=2,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )
    assert _train_rollout_cache_key_prefix(base) != _train_rollout_cache_key_prefix(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            train_index=10,
            train_trials=1,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )


async def test_cached_epoch_zero_train_rollout_executor_reuses_epoch_zero_cache(tmp_path: Path):
    from openviking.message import Message, TextPart
    from openviking.session.train.context import ExecutionContext
    from openviking.session.train.domain import Rollout

    calls = []
    completed = []

    class Delegate:
        async def execute(self, cases, policy_set, context):
            calls.append((list(cases), dict(context.metadata)))
            return [
                Rollout(
                    case=case,
                    messages=[Message(id=f"m-{case.name}", role="user", parts=[TextPart("hello")])],
                    policy_snapshot_id=context.policy_snapshot_id,
                    metadata={"source": "delegate"},
                )
                for case in cases
            ]

    executor = CachedEpochZeroTrainRolloutExecutor(
        delegate=Delegate(),
        cache_dir=tmp_path / "cache",
        cache_key_prefix="unit-prefix",
    )
    executor.on_rollout_complete = lambda **kwargs: completed.append(kwargs)
    case = _case()
    context = ExecutionContext(
        policy_snapshot_id="snapshot-1", metadata={"training": True, "epoch": 0}
    )

    first = await executor.execute([case], None, context)
    cached_context = ExecutionContext(
        policy_snapshot_id="snapshot-2", metadata={"training": True, "epoch": 0}
    )
    second = await executor.execute([case], None, cached_context)

    assert len(calls) == 1
    assert len(completed) == 2
    assert first[0].messages[0].content == "hello"
    assert second[0].messages[0].content == "hello"
    assert second[0].policy_snapshot_id == "snapshot-2"
    assert second[0].metadata["source"] == "delegate"
    assert second[0].metadata["train_rollout_cache_hit"] is True
    assert str(tmp_path / "cache") in second[0].metadata["train_rollout_cache_path"]


def test_train_rollout_report_marks_full_and_partial_cache_hits():
    from openviking.message import Message, TextPart
    from openviking.session.train.components.report_builder import PipelineReportBuilder
    from openviking.session.train.domain import CriterionResult, Rollout, RubricEvaluation

    case = _case()
    cached = Rollout(
        case=case,
        messages=[Message(id="m-cached", role="user", parts=[TextPart("cached")])],
        policy_snapshot_id="snapshot-1",
        metadata={"train_rollout_cache_hit": True},
    )
    fresh = Rollout(
        case=case,
        messages=[Message(id="m-fresh", role="user", parts=[TextPart("fresh")])],
        policy_snapshot_id="snapshot-1",
    )
    evaluation = RubricEvaluation(
        passed=True,
        score=1.0,
        criterion_results=[
            CriterionResult(
                criterion_name="verify_duplicate",
                passed=True,
                score=1.0,
                feedback=[],
                evidence=[],
            )
        ],
        feedback=[],
    )
    cached.evaluation = evaluation
    fresh.evaluation = evaluation
    builder = PipelineReportBuilder()

    full = builder.train_rollout_report(epoch=0, rollouts=[cached], snapshot_id="snapshot-1")
    partial = builder.train_rollout_report(
        epoch=0, rollouts=[cached, fresh], snapshot_id="snapshot-1"
    )

    assert full["cache_hit_count"] == 1
    assert full["cache_miss_count"] == 0
    assert full["from_cache"] is True
    assert partial["cache_hit_count"] == 1
    assert partial["cache_miss_count"] == 1
    assert partial["from_cache"] is False


async def test_cached_epoch_zero_train_rollout_executor_does_not_reuse_later_epochs(tmp_path: Path):
    from openviking.message import Message, TextPart
    from openviking.session.train.context import ExecutionContext
    from openviking.session.train.domain import Rollout

    calls = []

    class Delegate:
        async def execute(self, cases, policy_set, context):
            calls.append(dict(context.metadata))
            return [
                Rollout(
                    case=case,
                    messages=[
                        Message(
                            id=f"m-{len(calls)}", role="user", parts=[TextPart(str(len(calls)))]
                        )
                    ],
                    policy_snapshot_id=context.policy_snapshot_id,
                )
                for case in cases
            ]

    executor = CachedEpochZeroTrainRolloutExecutor(
        delegate=Delegate(),
        cache_dir=tmp_path / "cache",
        cache_key_prefix="unit-prefix",
    )
    case = _case()
    context = ExecutionContext(
        policy_snapshot_id="snapshot-1", metadata={"training": True, "epoch": 1}
    )

    first = await executor.execute([case], None, context)
    second = await executor.execute([case], None, context)

    assert len(calls) == 2
    assert first[0].messages[0].content == "1"
    assert second[0].messages[0].content == "2"
