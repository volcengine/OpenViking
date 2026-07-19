# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local Git note reporting for benchmark train/eval runs."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openviking.session.train.components.reporter import NoopPipelineLifecycleHook

_SECRET_OPTIONS = (
    "api-key",
    "access-token",
    "auth-token",
    "password",
    "secret",
    "token",
)
_SECRET_OPTION_PATTERN = re.compile(
    rf"(?P<prefix>--(?:{'|'.join(_SECRET_OPTIONS)})(?:=|\s+))"
    r"(?P<value>'[^']*'|\"[^\"]*\"|\S+)",
    flags=re.IGNORECASE,
)


def redact_launch_command(command: str) -> str:
    """Redact values for known secret-bearing command-line options."""

    return _SECRET_OPTION_PATTERN.sub(lambda match: f"{match.group('prefix')}'***'", command)


@dataclass(slots=True)
class GitNotesPipelineReporter(NoopPipelineLifecycleHook):
    """Append concise train/eval lifecycle summaries to a local Git note."""

    repo_root: Path
    commit: str
    run_id: str
    launch_command: str
    output_path: str
    events_path: str
    _started_monotonic: float = field(init=False, default_factory=time.monotonic)
    _current_stage: str = field(init=False, default="run")
    _current_epoch: Any = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.repo_root = self.repo_root.expanduser().resolve()
        self.commit = str(self.commit).strip()
        self.run_id = str(self.run_id).strip()
        self.launch_command = redact_launch_command(str(self.launch_command).strip())
        self.output_path = str(self.output_path)
        self.events_path = str(self.events_path)

    def record_run_start(self, *, dataset: str, domain: str) -> None:
        self._append_note(
            "\n".join(
                [
                    f"## OpenViking training run `{self.run_id}`",
                    f"- dataset: {dataset}",
                    f"- domain: {domain}",
                    f"- command: `{self.launch_command}`",
                    f"- result: `{self.output_path}`",
                    f"- events: `{self.events_path}`",
                    f"- started: {_timestamp()}",
                ]
            )
        )

    def mark_stage(self, stage: str, *, epoch: Any = None) -> None:
        self._current_stage = str(stage)
        self._current_epoch = epoch

    def on_epoch_start(self, *, epoch: int, context: Any) -> None:
        del context
        self.mark_stage(f"train epoch {epoch}", epoch=epoch)

    def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        stage = str(report.get("rollout_stage") or label)
        self._current_stage = stage
        self._current_epoch = report.get("epoch")
        self._append_note(self._stage_summary(stage, report))

    def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        epoch = report.get("epoch")
        self.mark_stage(f"train epoch {epoch}", epoch=epoch)
        self._append_note(self._stage_summary(f"train epoch {epoch}", report))
        if getattr(context, "eval_each_epoch_case_loader", None) is not None:
            metadata = dict(getattr(context, "execution_metadata", {}) or {})
            self.mark_stage(
                str(metadata.get("rollout_stage") or "epoch eval"),
                epoch=epoch,
            )

    def on_run_summary(
        self,
        *,
        title: str,
        fields: dict[str, Any],
        baseline_eval: dict[str, Any] | None = None,
        final_eval: dict[str, Any] | None = None,
        accuracy_delta: float | None = None,
        output_path: str | None = None,
        rollouts_root: str | None = None,
        rollouts_index_path: str | None = None,
        latest_failed_rollout: str | None = None,
    ) -> None:
        del title, baseline_eval, latest_failed_rollout
        report = dict(final_eval or {})
        report.update(
            {
                "cost_seconds": time.monotonic() - self._started_monotonic,
                "errors": int(fields.get("error_count") or 0),
                "result_path": output_path or self.output_path,
            }
        )
        extra_lines: list[str] = []
        if accuracy_delta is not None:
            extra_lines.append(f"- accuracy delta: {float(accuracy_delta) * 100:+.2f}pp")
        extra_lines.extend(
            [
                f"- rollouts: `{rollouts_root}`" if rollouts_root else "- rollouts: n/a",
                f"- rollouts index: `{rollouts_index_path}`"
                if rollouts_index_path
                else "- rollouts index: n/a",
            ]
        )
        self._current_stage = "run result"
        self._current_epoch = report.get("epoch")
        self._append_note(self._stage_summary("run result", report, extra_lines=extra_lines))

    def record_failure(self, error: BaseException) -> None:
        report = {
            "epoch": self._current_epoch,
            "cost_seconds": time.monotonic() - self._started_monotonic,
            "errors": 1,
        }
        self._append_note(
            self._stage_summary(
                "run failed",
                report,
                stage=self._current_stage,
                extra_lines=[f"- error: {type(error).__name__}"],
            )
        )

    def _stage_summary(
        self,
        title: str,
        report: dict[str, Any],
        *,
        stage: str | None = None,
        extra_lines: list[str] | None = None,
    ) -> str:
        case_count = report.get("case_count")
        passed_count = report.get("passed_count")
        errors = report.get("errors")
        error_count = len(errors) if isinstance(errors, list) else int(errors or 0)
        lines = [
            f"### {title}",
            f"- run: `{self.run_id}`",
            f"- stage: {stage or title}",
            f"- epoch: {_display(report.get('epoch'))}",
            f"- passed: {_passed(passed_count, case_count)}",
            f"- accuracy: {_percent(report.get('accuracy'))}",
            f"- duration: {_duration(report.get('cost_seconds'))}",
            f"- errors: {error_count}",
        ]
        if report.get("cache_hit"):
            lines.append("- cache hit: true")
        lines.extend(
            [
                f"- result: `{report.get('result_path') or self.output_path}`",
                f"- events: `{self.events_path}`",
                *(extra_lines or []),
                f"- updated: {_timestamp()}",
            ]
        )
        return "\n".join(lines)

    def _append_note(self, content: str) -> None:
        try:
            import fcntl

            lock_path = self._git_common_path("openviking-train-notes.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.repo_root),
                        "notes",
                        "append",
                        "-m",
                        content,
                        self.commit,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
        except (ImportError, OSError, subprocess.SubprocessError) as error:
            print(
                f"[batch-train-eval] failed to append Git note for "
                f"run {self.run_id}: {type(error).__name__}",
                file=sys.stderr,
                flush=True,
            )

    def _git_common_path(self, name: str) -> Path:
        value = subprocess.check_output(
            ["git", "-C", str(self.repo_root), "rev-parse", "--git-common-dir"],
            text=True,
        ).strip()
        path = Path(value)
        common_dir = path if path.is_absolute() else self.repo_root / path
        return common_dir / name


def _percent(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _passed(passed_count: Any, case_count: Any) -> str:
    if passed_count is None or case_count is None:
        return "n/a"
    return f"{int(passed_count)}/{int(case_count)}"


def _display(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _duration(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{max(0.0, float(value)):.2f}s"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
