# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import subprocess
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.session.train.components.git_notes import GitNotesPipelineReporter

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHERS = (
    REPO_ROOT / "benchmark/tau2/train/restart_vikingbot_train_eval.sh",
    REPO_ROOT / "benchmark/alfworld/train/restart_alfworld_train_eval.sh",
    REPO_ROOT / "benchmark/smoke/train/restart_smoke_train_eval.sh",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
    ).strip()


def _make_git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.name", "OpenViking Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_git_notes_reporter_appends_redacted_stage_summaries(tmp_path: Path):
    repo, commit = _make_git_repo(tmp_path)
    reporter = GitNotesPipelineReporter(
        repo_root=repo,
        commit=commit,
        run_id="run-123",
        launch_command=(
            "bash benchmark/tau2/train/restart_vikingbot_train_eval.sh "
            "--auto-commit --api-key top-secret --token=also-secret --epochs 2"
        ),
        output_path="result/tau2/train/run_123/report.json",
        events_path="result/tau2/train/run_123/events.jsonl",
    )

    reporter.record_run_start(dataset="tau2", domain="airline")
    reporter.on_eval_report(
        label="baseline_test",
        report={
            "epoch": -1,
            "case_count": 16,
            "passed_count": 5,
            "accuracy": 0.3125,
            "cost_seconds": 184.25,
        },
        context=None,
    )
    reporter.on_train_report(
        report={
            "epoch": 0,
            "case_count": 16,
            "passed_count": 7,
            "accuracy": 0.4375,
            "cost_seconds": 327.5,
            "errors": ["one failed commit"],
        },
        context=None,
    )
    reporter.on_run_summary(
        title="batch train/eval",
        fields={"dataset": "tau2", "domain": "airline", "error_count": 2},
        baseline_eval={"accuracy": 0.3125},
        final_eval={
            "epoch": 1,
            "case_count": 16,
            "passed_count": 7,
            "accuracy": 0.4375,
        },
        accuracy_delta=0.125,
        output_path="result/tau2/train/run_123/report.json",
        rollouts_root="result/tau2/train/run_123/rollouts",
        rollouts_index_path="result/tau2/train/run_123/rollouts_index.json",
        latest_failed_rollout=None,
    )

    note = _git(repo, "notes", "show", commit)
    assert "OpenViking training run `run-123`" in note
    assert "--api-key '***'" in note
    assert "--token='***'" in note
    assert "top-secret" not in note
    assert "also-secret" not in note
    assert "### baseline_test" in note
    assert "passed: 5/16" in note
    assert "accuracy: 31.25%" in note
    assert "duration: 184.25s" in note
    assert "### train epoch 0" in note
    assert "errors: 1" in note
    assert "### run result" in note
    assert "epoch: 1" in note
    assert "passed: 7/16" in note
    assert "errors: 2" in note
    assert "events: `result/tau2/train/run_123/events.jsonl`" in note
    assert "accuracy delta: +12.50pp" in note
    assert "result/tau2/train/run_123/report.json" in note
    assert _git(repo, "rev-parse", "HEAD") == commit


def test_git_notes_reporter_serializes_concurrent_process_updates(tmp_path: Path):
    repo, commit = _make_git_repo(tmp_path)
    script = """
import sys
from pathlib import Path
from openviking.session.train.components.git_notes import GitNotesPipelineReporter

index = int(sys.argv[3])
reporter = GitNotesPipelineReporter(
    repo_root=Path(sys.argv[1]),
    commit=sys.argv[2],
    run_id=f"run-{index}",
    launch_command=f"bash train.sh --slot {index}",
    output_path=f"result/run_{index}/report.json",
    events_path=f"result/run_{index}/events.jsonl",
)
reporter.on_eval_report(
    label=f"final_test_slot_{index}",
    report={
        "epoch": 1,
        "case_count": 1,
        "passed_count": 1,
        "accuracy": 1.0,
        "cost_seconds": 1.0,
    },
    context=None,
)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(repo), commit, str(index)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(8)
    ]
    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr

    note = _git(repo, "notes", "show", commit)
    for index in range(8):
        assert f"### final_test_slot_{index}" in note


def test_git_notes_reporter_records_failure_without_leaking_message(tmp_path: Path):
    repo, commit = _make_git_repo(tmp_path)
    reporter = GitNotesPipelineReporter(
        repo_root=repo,
        commit=commit,
        run_id="run-failed",
        launch_command="bash train.sh --auto-commit",
        output_path="result/run_failed/report.json",
        events_path="result/run_failed/events.jsonl",
    )

    reporter.mark_stage("final_test", epoch=2)
    reporter.record_failure(RuntimeError("request included secret-value"))

    note = _git(repo, "notes", "show", commit)
    assert "### run failed" in note
    assert "stage: final_test" in note
    assert "epoch: 2" in note
    assert "passed: n/a" in note
    assert "accuracy: n/a" in note
    assert "errors: 1" in note
    assert "events: `result/run_failed/events.jsonl`" in note
    assert "error: RuntimeError" in note
    assert "secret-value" not in note


def test_git_notes_reporter_marks_train_epoch_at_epoch_start(tmp_path: Path):
    repo, commit = _make_git_repo(tmp_path)
    reporter = GitNotesPipelineReporter(
        repo_root=repo,
        commit=commit,
        run_id="run-epoch-failed",
        launch_command="bash train.sh --auto-commit",
        output_path="result/run_failed/report.json",
        events_path="result/run_failed/events.jsonl",
    )

    reporter.on_epoch_start(epoch=3, context=None)
    reporter.record_failure(RuntimeError("failed"))

    note = _git(repo, "notes", "show", commit)
    assert "stage: train epoch 3" in note
    assert "epoch: 3" in note


def test_git_notes_reporter_uses_cache_artifact_without_old_duration(tmp_path: Path):
    repo, commit = _make_git_repo(tmp_path)
    reporter = GitNotesPipelineReporter(
        repo_root=repo,
        commit=commit,
        run_id="run-cache",
        launch_command="bash train.sh --auto-commit",
        output_path="result/run_cache/report.json",
        events_path="result/run_cache/events.jsonl",
    )

    reporter.on_eval_report(
        label="baseline_test (cache hit)",
        report={
            "epoch": -1,
            "case_count": 4,
            "passed_count": 2,
            "accuracy": 0.5,
            "cost_seconds": None,
            "cache_hit": True,
            "result_path": "result/cache/baseline.json",
        },
        context=None,
    )

    note = _git(repo, "notes", "show", commit)
    assert "### baseline_test (cache hit)" in note
    assert "cache hit: true" in note
    assert "duration: n/a" in note
    assert "result: `result/cache/baseline.json`" in note


def test_git_notes_write_failure_warns_without_raising(tmp_path: Path, capsys):
    repo, _ = _make_git_repo(tmp_path)
    reporter = GitNotesPipelineReporter(
        repo_root=repo,
        commit="not-a-commit",
        run_id="run-warning",
        launch_command="bash train.sh --auto-commit",
        output_path="result/report.json",
        events_path="result/events.jsonl",
    )

    reporter.on_train_report(
        report={"epoch": 0, "case_count": 1, "passed_count": 1, "accuracy": 1.0},
        context=None,
    )

    assert "failed to append Git note" in capsys.readouterr().err


def test_batch_runner_builds_git_notes_lifecycle_hook(tmp_path: Path, monkeypatch):
    from openviking.session.train import batch_runner

    repo, commit = _make_git_repo(tmp_path)
    monkeypatch.setattr(batch_runner, "_repo_root", lambda: repo)
    config = batch_runner.BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        benchmark_service_url="http://127.0.0.1:1944",
        git_notes_commit=commit,
        git_notes_launch_command="bash train.sh --auto-commit",
    )

    reporter = batch_runner._git_notes_reporter(config)
    context = batch_runner._pipeline_context(
        epoch=0,
        training=True,
        additional_lifecycle_hooks=[reporter],
    )

    assert reporter is not None
    assert reporter in context.lifecycle_hooks
    assert reporter.commit == commit


@pytest.mark.asyncio
async def test_batch_cli_reads_git_notes_target_from_auto_commit_environment(monkeypatch):
    from openviking.session.train import batch_runner

    cli = import_module("openviking.session.train.run_batch_train_eval")

    captured = None

    async def capture_config(config):
        nonlocal captured
        captured = config
        return SimpleNamespace(train_epochs=[])

    monkeypatch.setattr(batch_runner, "run_batch_train_eval", capture_config)
    monkeypatch.setenv("OPENVIKING_TRAIN_GIT_NOTES_COMMIT", "abc123")
    monkeypatch.setenv(
        "OPENVIKING_TRAIN_LAUNCH_COMMAND",
        "bash benchmark/smoke/train/restart_smoke_train_eval.sh --auto-commit",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_batch_train_eval", "--dataset", "smoke", "--domain", "smoke"],
    )

    assert await cli.main_async() == 0
    assert captured is not None
    assert captured.git_notes_commit == "abc123"
    assert "restart_smoke_train_eval.sh" in captured.git_notes_launch_command


@pytest.mark.asyncio
async def test_batch_runner_records_start_and_failure_when_client_init_fails(
    tmp_path: Path,
    monkeypatch,
):
    from openviking.session.train import batch_runner

    repo, commit = _make_git_repo(tmp_path)

    class FailingClient:
        async def initialize(self):
            raise RuntimeError("client init failed")

        async def close(self):
            return None

    monkeypatch.setattr(batch_runner, "_repo_root", lambda: repo)
    monkeypatch.setattr(batch_runner, "_build_http_client", lambda config: FailingClient())
    config = batch_runner.BatchTrainEvalConfig(
        dataset="smoke",
        domain="smoke",
        benchmark_service_url="http://127.0.0.1:1944",
        git_notes_commit=commit,
        git_notes_launch_command="bash train.sh --auto-commit",
    )

    with pytest.raises(RuntimeError, match="client init failed"):
        await batch_runner.run_batch_train_eval(config)

    note = _git(repo, "notes", "show", commit)
    assert "OpenViking training run" in note
    assert "### run failed" in note


def test_auto_commit_helper_commits_and_exports_note_target(tmp_path: Path):
    repo, initial_commit = _make_git_repo(tmp_path)
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    helper = REPO_ROOT / "openviking/session/train/auto_commit.sh"
    command = f"""
set -euo pipefail
source {helper!s}
AUTO_COMMIT=true
OPENVIKING_TRAIN_LAUNCH_COMMAND='bash train.sh --auto-commit --epochs 2'
openviking_train_auto_commit {repo!s} 'smoke train eval'
printf '%s\n%s\n' "$OPENVIKING_TRAIN_GIT_NOTES_COMMIT" "$OPENVIKING_TRAIN_LAUNCH_COMMAND"
"""

    completed = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )

    current_commit, launch_command = completed.stdout.strip().splitlines()[-2:]
    assert current_commit != initial_commit
    assert current_commit == _git(repo, "rev-parse", "HEAD")
    assert launch_command == "bash train.sh --auto-commit --epochs 2"
    assert _git(repo, "status", "--porcelain") == ""


def test_auto_commit_helper_clears_inherited_note_target_when_disabled(tmp_path: Path):
    repo, _ = _make_git_repo(tmp_path)
    helper = REPO_ROOT / "openviking/session/train/auto_commit.sh"
    command = f"""
set -euo pipefail
source {helper!s}
AUTO_COMMIT=false
OPENVIKING_TRAIN_GIT_NOTES_COMMIT=stale
OPENVIKING_TRAIN_LAUNCH_COMMAND='stale command'
openviking_train_auto_commit {repo!s} 'smoke train eval'
printf '%s\n%s\n' "${{OPENVIKING_TRAIN_GIT_NOTES_COMMIT-unset}}" "${{OPENVIKING_TRAIN_LAUNCH_COMMAND-unset}}"
"""

    completed = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.splitlines() == ["unset", "unset"]


@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda path: path.parent.parent.name)
def test_train_launcher_help_documents_auto_commit(launcher: Path):
    completed = subprocess.run(
        ["bash", str(launcher), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--auto-commit" in completed.stdout
