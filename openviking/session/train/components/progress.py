# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Terminal progress helpers for train components."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

try:  # pragma: no cover - exercised through integration/TTY usage
    from rich.console import Console
    from rich.progress import Progress, ProgressColumn, Task, TextColumn
    from rich.table import Column
    from rich.text import Text
except Exception:  # pragma: no cover - fallback for minimal environments
    Console = None
    Progress = None
    ProgressColumn = object  # type: ignore[assignment,misc]
    Task = Any  # type: ignore[misc,assignment]
    TextColumn = None
    Column = None
    Text = None


class ThreeStateBarColumn(ProgressColumn):
    """Render successful/failed/running/pending work as a rich progress bar."""

    def __init__(self, bar_width: int = 42) -> None:
        table_column = Column(ratio=1, no_wrap=True) if Column is not None else None
        super().__init__(table_column=table_column)
        self.bar_width = bar_width

    def render(self, task: Task) -> Text:
        bar_width = self.bar_width or 40
        total = max(int(task.total or 0), 0)
        processed = max(int(task.completed or 0), 0)
        failed = max(int(task.fields.get("failed", 0) or 0), 0)
        succeeded = max(int(task.fields.get("succeeded", processed - failed) or 0), 0)
        running = max(int(task.fields.get("running", 0) or 0), 0)

        if total <= 0:
            return Text("─" * bar_width, style="bar.back")

        succeeded = min(succeeded, total)
        failed = min(failed, total - succeeded)
        running = min(running, total - succeeded - failed)
        succeeded_width = int(bar_width * succeeded / total)
        failed_width = int(bar_width * (succeeded + failed) / total) - succeeded_width
        running_width = (
            int(bar_width * (succeeded + failed + running) / total) - succeeded_width - failed_width
        )
        pending_width = bar_width - succeeded_width - failed_width - running_width

        bar = Text()
        if succeeded_width > 0:
            bar.append("█" * succeeded_width, style="green")
        if failed_width > 0:
            bar.append("█" * failed_width, style="red")
        if running_width > 0:
            bar.append("▓" * running_width, style="yellow")
        if pending_width > 0:
            bar.append("░" * pending_width, style="dim")
        return bar


class ProgressSummaryColumn(ProgressColumn):
    """Render processed count plus non-zero active/failure counts."""

    def render(self, task: Task) -> Text:
        failed = max(int(task.fields.get("failed", 0) or 0), 0)
        running = max(int(task.fields.get("running", 0) or 0), 0)

        summary = Text("(")
        summary.append(f"{int(task.completed or 0)}/{int(task.total or 0)}")
        if failed > 0:
            summary.append(", ")
            summary.append(f"{failed} failed", style="bold red")
        if running > 0:
            summary.append(", ")
            summary.append(f"{running} running", style="bold yellow")
        summary.append(")")
        return summary


@dataclass(slots=True)
class ProgressPrinter:
    """Render a four-state progress indicator for train components.

    The public API intentionally matches the previous lightweight printer so
    callers only need ``render()``, ``start_one()``, ``complete_one()`` and
    ``finish()``.  ``fail_one()`` records failed work in red. Rich rendering is
    used for interactive terminals; a compact text fallback is kept for minimal
    environments.
    """

    total: int
    label: str
    enabled: bool
    description: str = ""
    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    _finished: bool = False
    _use_rich: bool = field(init=False, default=False)
    _progress: Any = field(init=False, default=None)
    _task_id: Any = field(init=False, default=None)
    _started: bool = field(init=False, default=False)
    _description_printed: bool = field(init=False, default=False)
    _started_at: float | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.pending == 0 and self.running == 0 and self.completed == 0 and self.failed == 0:
            self.pending = max(0, self.total)
        self._use_rich = bool(
            self.enabled
            and self.total > 0
            and Progress is not None
            and Console is not None
            and TextColumn is not None
            and sys.stderr.isatty()
        )

    def render(self) -> None:
        if not self.enabled or self.total <= 0:
            return
        self._mark_started()
        self._print_description_once()
        if self._use_rich:
            self._ensure_rich_started()
            self._update_rich()
            return
        self._write_text()

    def start_one(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        if self.pending > 0:
            self.pending -= 1
        self.running += 1
        self._write()

    def complete_one(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        if self.running > 0:
            self.running -= 1
        elif self.pending > 0:
            self.pending -= 1
        self.completed = min(self.total, self.completed + 1)
        self._write()

    def fail_one(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        if self.running > 0:
            self.running -= 1
        elif self.pending > 0:
            self.pending -= 1
        self.failed = min(self.total - self.completed, self.failed + 1)
        self._write()

    def advance(self) -> None:
        """Compatibility helper for callers that only track completion."""
        self.complete_one()

    def finish(self) -> None:
        if not self.enabled or self.total <= 0 or self._finished:
            return
        self._finished = True
        if self._use_rich:
            self._update_rich()
            if self._progress is not None and self._started:
                self._progress.stop()
            return
        self._write_text(newline=True)

    def _write(self) -> None:
        self._mark_started()
        self._print_description_once()
        if self._use_rich:
            self._ensure_rich_started()
            self._update_rich()
            return
        self._write_text()

    def _mark_started(self) -> None:
        if self._started_at is None:
            self._started_at = time.monotonic()

    def _print_description_once(self) -> None:
        if self._description_printed or not self.description:
            return
        self._description_printed = True
        if self._use_rich:
            line = Text()
            line.append(format_label(self.label), style=label_style(self.label))
            line.append(f" {self.description}")
            Console(stderr=True, soft_wrap=False).print(line)
            return
        sys.stdout.write(f"[{self.label}] {self.description}\n")
        sys.stdout.flush()

    def _ensure_rich_started(self) -> None:
        if self._progress is not None:
            return
        self._progress = Progress(
            ThreeStateBarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            ProgressSummaryColumn(),
            console=Console(stderr=True, soft_wrap=False),
            transient=False,
        )
        self._task_id = self._progress.add_task(
            format_label(self.label),
            total=self.total,
            running=0,
            succeeded=0,
            failed=0,
        )
        self._progress.start()
        self._started = True

    def _update_rich(self) -> None:
        if self._progress is None:
            return
        self._progress.update(
            self._task_id,
            total=self.total,
            completed=self.completed + self.failed,
            succeeded=self.completed,
            failed=self.failed,
            running=self.running,
        )

    def _write_text(self, *, newline: bool = False) -> None:
        width = 24
        succeeded_width, failed_width, running_width, pending_width = _state_widths(
            pending=self.pending,
            running=self.running,
            completed=self.completed,
            failed=self.failed,
            total=self.total,
            width=width,
        )
        bar = "C" * succeeded_width + "F" * failed_width + "R" * running_width + "P" * pending_width
        processed = self.completed + self.failed
        percent = (processed / self.total) * 100.0
        suffix = "\n" if newline else ""
        sys.stdout.write(
            f"\r[{self.label}] [{bar}] {percent:6.2f}% "
            f"{_progress_summary(processed=processed, total=self.total, failed=self.failed, running=self.running)}"
            f"{suffix}"
        )
        sys.stdout.flush()


def _state_widths(
    *,
    pending: int,
    running: int,
    completed: int,
    failed: int,
    total: int,
    width: int,
) -> tuple[int, int, int, int]:
    values = [completed, failed, running, pending]
    if total <= 0 or width <= 0:
        return 0, 0, 0

    exact = [(value / total) * width if value > 0 else 0.0 for value in values]
    widths = [int(value) for value in exact]

    for idx, value in enumerate(values):
        if value > 0 and widths[idx] == 0:
            widths[idx] = 1

    while sum(widths) > width:
        candidates = [
            idx for idx, value in enumerate(values) if widths[idx] > (1 if value > 0 else 0)
        ]
        if not candidates:
            break
        idx = max(candidates, key=lambda item: widths[item])
        widths[idx] -= 1

    while sum(widths) < width:
        idx = max(range(len(values)), key=lambda item: exact[item] - int(exact[item]))
        widths[idx] += 1

    return widths[0], widths[1], widths[2], widths[3]


def _progress_summary(*, processed: int, total: int, failed: int, running: int) -> str:
    extras: list[str] = []
    if failed > 0:
        extras.append(f"{failed} failed")
    if running > 0:
        extras.append(f"{running} running")
    if extras:
        return f"({processed}/{total}, {', '.join(extras)})"
    return f"({processed}/{total})"


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def format_label(label: str) -> str:
    return f"[{label.upper()}]"


def label_style(label: str) -> str:
    if label.endswith("_start"):
        return "bold yellow"
    if "final" in label or label in {
        "train",
        "train_rollout",
        "test_rollout",
        "baseline_test_rollout",
    } or label.endswith("_rollout"):
        return "bold green"
    return "bold cyan"


async def run_with_progress(
    items: list[Any],
    *,
    coroutine_factory: Callable[[Any, int], Any],
    total: int | None = None,
    label: str,
    enabled: bool,
    description: str = "",
    concurrency: int,
) -> list[Any]:
    """Run ``coroutine_factory(item, index)`` for each item with a ProgressPrinter.

    Creates a semaphore, starts/fails/completes progress rows per item, and
    guarantees ``progress.finish()`` in a finally block. Returns the gathered
    results in input order.
    """
    import asyncio

    n = total if total is not None else len(items)
    progress = ProgressPrinter(total=n, label=label, enabled=enabled, description=description)
    progress.render()
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(item: Any, index: int) -> Any:
        async with semaphore:
            progress.start_one()
            try:
                result = await coroutine_factory(item, index)
            except Exception:
                progress.fail_one()
                raise
            progress.complete_one()
            return result

    try:
        return list(
            await asyncio.gather(*(_run_one(item, idx) for idx, item in enumerate(items)))
        )
    finally:
        progress.finish()
