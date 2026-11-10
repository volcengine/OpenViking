# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Console reporting helpers for session training pipelines."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

try:  # pragma: no cover - cosmetic terminal rendering
    from rich.console import Console
    from rich.text import Text
except Exception:  # pragma: no cover - rich is optional
    Console = None
    Text = None

from openviking.session.train.components.progress import format_duration, format_label, label_style

HookResult = Awaitable[None] | None
ReportHookResult = Awaitable[dict[str, Any] | None] | dict[str, Any] | None
DecisionHookResult = Awaitable[Any] | Any | None


class PipelineLifecycleHook(Protocol):
    """Lifecycle hook extension point for train/eval pipelines."""

    def on_epoch_start(self, *, epoch: int, context: Any) -> HookResult: ...

    def on_train_rollout_end(
        self,
        *,
        epoch: int,
        rollouts: list[Any],
        snapshot_id: str,
        policy_set: Any,
        context: Any,
    ) -> ReportHookResult: ...

    def on_epoch_end(
        self,
        *,
        epoch_result: Any,
        policy_set: Any,
        context: Any,
    ) -> DecisionHookResult: ...

    def on_eval_end(
        self,
        *,
        evaluation_result: Any,
        policy_set: Any,
        context: Any,
    ) -> ReportHookResult: ...

    def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> HookResult: ...

    def on_train_rollout_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> HookResult: ...

    def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> HookResult: ...

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
    ) -> HookResult: ...


class NoopPipelineLifecycleHook:
    """Base class for lifecycle hooks that only need to override some events."""

    def on_epoch_start(self, *, epoch: int, context: Any) -> None:
        del epoch, context

    def on_train_rollout_end(
        self,
        *,
        epoch: int,
        rollouts: list[Any],
        snapshot_id: str,
        policy_set: Any,
        context: Any,
    ) -> None:
        del epoch, rollouts, snapshot_id, policy_set, context

    def on_epoch_end(
        self,
        *,
        epoch_result: Any,
        policy_set: Any,
        context: Any,
    ) -> None:
        del epoch_result, policy_set, context

    def on_eval_end(
        self,
        *,
        evaluation_result: Any,
        policy_set: Any,
        context: Any,
    ) -> None:
        del evaluation_result, policy_set, context

    def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del label, report, context

    def on_train_rollout_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del report, context

    def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del report, context

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
        del (
            title,
            fields,
            baseline_eval,
            final_eval,
            accuracy_delta,
            output_path,
            rollouts_root,
            rollouts_index_path,
            latest_failed_rollout,
        )


async def emit_run_summary(
    context: Any,
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
    """Emit a run-level summary event to lifecycle hooks on a pipeline context."""

    lifecycle_hooks = list(getattr(context, "lifecycle_hooks", []) or [])
    for hook in lifecycle_hooks:
        result = hook.on_run_summary(
            title=title,
            fields=fields,
            baseline_eval=baseline_eval,
            final_eval=final_eval,
            accuracy_delta=accuracy_delta,
            output_path=output_path,
            rollouts_root=rollouts_root,
            rollouts_index_path=rollouts_index_path,
            latest_failed_rollout=latest_failed_rollout,
        )
        if inspect.isawaitable(result):
            await result


@dataclass(slots=True)
class ConsolePipelineReporter(NoopPipelineLifecycleHook):
    """Default stdout lifecycle hook for batch train/eval runners."""

    use_rich: bool | None = None
    _epoch_summaries: dict[int, dict[str, Any]] = field(init=False, default_factory=dict)
    _printed_epoch_summaries: set[int] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        if self.use_rich is None:
            self.use_rich = Console is not None and Text is not None and sys.stdout.isatty()

    def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        label = str(report.get("rollout_stage") or label)
        split = report.get("split")
        trial_count = int(report.get("trial_count") or 1)
        if trial_count > 1:
            self._print_line(
                label,
                [
                    ("epoch", report["epoch"]),
                    *_split_field(split),
                    ("trials", trial_count, "cyan"),
                    ("cases_per_trial", report.get("case_count_per_trial") or "varies"),
                    (
                        "total_rollouts",
                        report.get("total_rollout_count", report["case_count"]),
                        "cyan",
                    ),
                    (
                        "accuracy",
                        fmt_percent(report.get("accuracy_mean")),
                        _accuracy_style(report.get("accuracy_mean")),
                    ),
                    ("", f"± {fmt_percentage_point_abs(report.get('accuracy_std'))}", "yellow"),
                    *_cost_field(report),
                ],
            )
            self._remember_eval_report(label, report)
            if _is_epoch_test_report(label, report):
                self._print_epoch_summary(int(report["epoch"]))
            self._print_stage_separator()
            return
        self._print_line(
            label,
            [
                ("epoch", report["epoch"]),
                *_split_field(split),
                ("cases", report["case_count"]),
                (
                    "accuracy",
                    fmt_percent(report["accuracy"]),
                    _accuracy_style(report.get("accuracy")),
                ),
                (
                    "passed",
                    f"{report['passed_count']}/{report['case_count']}",
                    _passed_style(report),
                ),
                *_cost_field(report),
            ],
        )
        self._remember_eval_report(label, report)
        if _is_epoch_test_report(label, report):
            self._print_epoch_summary(int(report["epoch"]))
        self._print_stage_separator()

    def on_epoch_start(self, *, epoch: int, context: Any) -> None:
        del context
        text = f" epoch {epoch} "
        width = 44
        left = max((width - len(text)) // 2, 1)
        right = max(width - len(text) - left, 1)
        line = f"{'=' * left}{text}{'=' * right}"
        if not self.use_rich:
            print(line)
            return
        Console().print(line, style="bold cyan")

    def on_train_rollout_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        self._remember_train_rollout_report(report)
        self._print_line(
            "train_rollout",
            [
                ("epoch", report["epoch"]),
                ("cases", report["case_count"]),
                *_cache_field(report),
                (
                    "accuracy",
                    fmt_percent(report["accuracy"]),
                    _accuracy_style(report.get("accuracy")),
                ),
                (
                    "passed",
                    f"{report['passed_count']}/{report['case_count']}",
                    _passed_style(report),
                ),
                *_cost_field(report),
            ],
        )
        self._print_stage_separator()

    def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        self._remember_train_report(report)
        error_count = len(report["errors"])
        self._print_line(
            "train",
            [
                ("epoch", report["epoch"]),
                ("commits", report["committed_rollout_count"], "cyan"),
                ("errors", error_count, "green" if error_count == 0 else "red bold"),
                *_cost_field(report),
            ],
        )
        if report.get("errors"):
            trace_ids = report.get("failed_commit_trace_ids") or []
            telemetry_ids = report.get("failed_commit_telemetry_ids") or []
            if trace_ids:
                print(f"[train] failed_commit_trace_ids={','.join(trace_ids)}")
            else:
                print("[train] failed_commit_trace_ids=<none>")
            if telemetry_ids:
                print(f"[train] failed_commit_telemetry_ids={','.join(telemetry_ids)}")
        if not _has_epoch_eval(context):
            self._print_epoch_summary(int(report["epoch"]))
        self._print_stage_separator()

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
        print(f"==== {title} ====")
        for key, value in fields.items():
            if value is not None:
                print(f"{key}: {value}")
        if baseline_eval:
            self._report_eval_line("baseline", baseline_eval)
        if final_eval:
            self._report_eval_line("final", final_eval)
        if accuracy_delta is not None:
            print(f"accuracy delta: {fmt_percentage_point(accuracy_delta)}")
        if output_path:
            print(f"report: {output_path}")
        if rollouts_root:
            print(f"rollouts: {rollouts_root}")
        if rollouts_index_path:
            print(f"rollouts_index: {rollouts_index_path}")
        if latest_failed_rollout:
            print(f"latest_failed_rollout: {latest_failed_rollout}")

    def _print_stage_separator(self) -> None:
        print("-" * 60)

    def _print_line(self, label: str, fields: list[tuple[Any, ...]]) -> None:
        if not self.use_rich:
            print(
                f"[{label}] "
                + " ".join(
                    _plain_field(item)
                    for item in fields
                )
            )
            return
        console = Console()
        line = Text()
        line.append(format_label(label), style=label_style(label))
        for item in fields:
            key = str(item[0])
            value = str(item[1])
            value_style = str(item[2]) if len(item) > 2 else "default"
            line.append(" ")
            if key:
                line.append(f"{key}=", style="dim")
            line.append(value, style=value_style)
        console.print(line)

    def _remember_train_rollout_report(self, report: dict[str, Any]) -> None:
        epoch = _report_epoch(report)
        if epoch is None:
            return
        self._epoch_summaries.setdefault(epoch, {})["train_rollout"] = dict(report)

    def _remember_train_report(self, report: dict[str, Any]) -> None:
        epoch = _report_epoch(report)
        if epoch is None:
            return
        self._epoch_summaries.setdefault(epoch, {})["train"] = dict(report)

    def _remember_eval_report(self, label: str, report: dict[str, Any]) -> None:
        epoch = _report_epoch(report)
        if epoch is None:
            return
        self._epoch_summaries.setdefault(epoch, {})["test"] = {
            **dict(report),
            "label": label,
        }

    def _print_epoch_summary(self, epoch: int) -> None:
        if epoch in self._printed_epoch_summaries:
            return
        summary = self._epoch_summaries.get(epoch) or {}
        train_data = _summary_train_report(summary)
        test_data = summary.get("test")
        if train_data is None and test_data is None:
            return
        self._printed_epoch_summaries.add(epoch)

        header = f" epoch {epoch} summary "
        width = max(44, len(header) + 8)
        left = max((width - len(header)) // 2, 1)
        right = max(width - len(header) - left, 1)
        border_top = f"{'=' * left}{header}{'=' * right}"
        border_bottom = "=" * len(border_top)
        self._print_summary_fragments([(border_top, "cyan bold")])
        if train_data is not None:
            self._print_summary_fragments(_train_summary_fragments(train_data))
        if test_data is not None:
            self._print_summary_fragments(_test_summary_fragments(test_data))
        self._print_summary_fragments([(border_bottom, "cyan bold")])

    def _print_summary_fragments(self, fragments: list[tuple[str, str]]) -> None:
        if not self.use_rich:
            print("".join(_style_plain(text, style) for text, style in fragments))
            return
        line = Text()
        for text, style in fragments:
            line.append(text, style=style or "default")
        Console().print(line)

    def _report_eval_line(self, label: str, data: dict[str, Any]) -> None:
        trial_count = int(data.get("trial_count") or 1)
        if trial_count > 1:
            print(
                f"{label} accuracy: {fmt_percent(data.get('accuracy_mean'))} ± "
                f"{fmt_percentage_point_abs(data.get('accuracy_std'))} "
                f"(trials={trial_count}, "
                f"cases_per_trial={data.get('case_count_per_trial') or 'varies'})"
            )
            return
        print(
            f"{label} accuracy: "
            f"{fmt_percent(data['accuracy'])} "
            f"({data['passed_count']}/{data['case_count']})"
        )


def _cost_field(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    cost_seconds = report.get("cost_seconds")
    if cost_seconds is None:
        return []
    return [("cost", format_duration(float(cost_seconds)), "magenta bold")]


def _cache_field(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    hit_count = int(report.get("cache_hit_count") or 0)
    if hit_count <= 0:
        return []
    total = int(report.get("case_count") or 0)
    if bool(report.get("from_cache")):
        value = "all" if total <= 0 else f"{hit_count}/{total}"
    else:
        value = f"partial({hit_count}/{total})" if total > 0 else str(hit_count)
    return [("from_cache", value, "cyan bold")]


def _split_field(split: Any) -> list[tuple[str, str, str]]:
    if split is None:
        return []
    return [("split", str(split), "cyan")]


def _plain_field(item: tuple[Any, ...]) -> str:
    text = f"{item[0]}={item[1]}" if item[0] else str(item[1])
    if len(item) <= 2 or item[0] != "accuracy":
        return text
    return f"accuracy={_style_plain(str(item[1]), str(item[2]))}"


def _style_plain(text: str, style: str) -> str:
    if not style:
        return text
    parts: list[str] = []
    style_tokens = set(style.split())
    if "bold" in style_tokens:
        parts.append("1")
    if "red" in style_tokens:
        parts.append("31")
    elif "green" in style_tokens:
        parts.append("32")
    elif "yellow" in style_tokens:
        parts.append("33")
    elif "cyan" in style_tokens:
        parts.append("36")
    elif "magenta" in style_tokens:
        parts.append("35")
    if not parts:
        return text
    return f"\033[{';'.join(parts)}m{text}\033[0m"


def _report_epoch(report: dict[str, Any]) -> int | None:
    try:
        return int(report["epoch"])
    except (KeyError, TypeError, ValueError):
        return None


def _has_epoch_eval(context: Any) -> bool:
    return getattr(context, "eval_each_epoch_case_loader", None) is not None


def _is_epoch_test_report(label: str, report: dict[str, Any]) -> bool:
    label_text = str(label)
    return (
        (
            label_text == "test_rollout"
            or label_text.startswith("epoch_")
            or label_text.startswith("eval_")
        )
        and label_text.endswith("_rollout")
        and report.get("epoch") is not None
        and int(report.get("epoch") or 0) >= 0
    )


def _summary_train_report(summary: dict[str, Any]) -> dict[str, Any] | None:
    train_rollout = summary.get("train_rollout")
    if isinstance(train_rollout, dict):
        return train_rollout
    train = summary.get("train")
    if isinstance(train, dict):
        nested = train.get("train_rollout")
        if isinstance(nested, dict):
            return nested
    return None


def _train_summary_fragments(data: dict[str, Any]) -> list[tuple[str, str]]:
    accuracy = data.get("accuracy")
    passed = data.get("passed_count")
    total = data.get("case_count")
    fragments = [
        ("TRAIN accuracy: ", "bold"),
        (fmt_percent(accuracy), _accuracy_style(accuracy)),
    ]
    if passed is not None and total is not None:
        fragments.extend([("  passed=", "default"), (f"{passed}/{total}", _passed_style(data))])
    return fragments


def _test_summary_fragments(data: dict[str, Any]) -> list[tuple[str, str]]:
    trial_count = int(data.get("trial_count") or 1)
    if trial_count > 1:
        accuracy = data.get("accuracy_mean")
        fragments = [
            ("EVAL  accuracy: ", "bold"),
            (fmt_percent(accuracy), _accuracy_style(accuracy)),
            (" ± ", "default"),
            (fmt_percentage_point_abs(data.get("accuracy_std")), "yellow"),
            ("  trials=", "default"),
            (str(trial_count), "cyan"),
        ]
        return fragments

    accuracy = data.get("accuracy")
    fragments = [
        ("EVAL  accuracy: ", "bold"),
        (fmt_percent(accuracy), _accuracy_style(accuracy)),
        ("  passed=", "default"),
        (f"{data.get('passed_count')}/{data.get('case_count')}", _passed_style(data)),
    ]
    return fragments


def fmt_score(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.6f}"


def fmt_percent(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def fmt_percentage_point(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:+.2f}pp"


def fmt_percentage_point_abs(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}pp"


def _accuracy_style(value: Any) -> str:
    if value is None:
        return "dim"
    score = float(value)
    if score >= 0.8:
        return "green bold"
    if score >= 0.5:
        return "yellow bold"
    return "red bold"


def _passed_style(data: dict[str, Any]) -> str:
    case_count = int(data.get("case_count") or 0)
    passed_count = int(data.get("passed_count") or 0)
    if case_count > 0 and passed_count == case_count:
        return "green bold"
    if passed_count == 0:
        return "red bold"
    return "yellow bold"
