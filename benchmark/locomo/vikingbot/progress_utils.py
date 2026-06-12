"""Shared progress bar utilities for LoCoMo benchmark scripts.

Provides a three-state progress bar (done / running / pending) built on
top of ``rich.progress``, plus helpers for both threaded and asyncio
scenarios.
"""

from __future__ import annotations

import sys
from typing import Optional

from rich.console import Console
from rich.progress import (
    Progress,
    ProgressColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Column
from rich.text import Text

# ---------------------------------------------------------------------------
# Three-state bar column
# ---------------------------------------------------------------------------


class ThreeStateBarColumn(ProgressColumn):
    """A progress bar that renders three states: done / running / pending.

    - **done**     = solid filled (``bar.complete`` style)
    - **running**  = shaded / mid-colour (``bar.finished`` style, repurposed)
    - **pending**  = hollow / background (``bar.pulse`` or ``bar.back`` style)

    The number of in-flight items is read from
    ``task.fields.get("running", 0)``.  The total bar width maps to
    ``task.total``; the solid portion is ``task.completed``; the shaded
    portion extends from there by ``running``; the rest is pending.
    """

    def __init__(
        self,
        bar_width: int = 42,
        style: str = "bar.complete",
        running_style: str = "bar.finished",
        complete_style: Optional[str] = None,
        finished_style: Optional[str] = None,
        pulse_style: str = "bar.pulse",
        table_column: Optional[Column] = None,
    ) -> None:
        super().__init__(table_column=table_column or Column(ratio=1, no_wrap=True))
        self.bar_width = bar_width
        self.style = style
        # "complete" = done portion, "finished" = running portion.
        # We accept both naming conventions so callers can override freely.
        self.complete_style = complete_style or style
        self.finished_style = finished_style or running_style
        self.pulse_style = pulse_style

    def render(self, task: Task) -> Text:
        """Render the three-state bar."""
        bar_width = self.bar_width or 40

        total = max(task.total or 0, 0)
        done = max(task.completed or 0, 0)
        running = max(int(task.fields.get("running", 0) or 0), 0)

        if total <= 0:
            # Degenerate case: show empty bar
            return Text("─" * bar_width, style="bar.back")

        # Clamp so we don't exceed 100% visually
        if done > total:
            done = total
        if done + running > total:
            running = total - done

        done_width = int(bar_width * done / total)
        running_width = int(bar_width * (done + running) / total) - done_width
        pending_width = bar_width - done_width - running_width

        bar = Text()
        if done_width > 0:
            bar.append("█" * done_width, style=self.complete_style)
        if running_width > 0:
            bar.append("▓" * running_width, style=self.finished_style)
        if pending_width > 0:
            bar.append("░" * pending_width, style="bar.back")

        return bar


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_three_state_progress(
    description: str = "Progress",
    console: Optional[Console] = None,
    transient: bool = False,
) -> tuple[Progress, TaskID]:
    """Create a :class:`Progress` instance with a three-state bar.

    Returns the ``(progress, task_id)`` pair.  The task starts with
    ``completed=0``, ``total=0``, ``running=0``; callers should call
    ``progress.update(task_id, total=N)`` to set the total and
    ``progress.update(task_id, advance=1)`` / modify ``running`` via
    ``fields`` as work proceeds.
    """
    console = console or Console(stderr=True, soft_wrap=False)
    progress = Progress(
        ThreeStateBarColumn(),
        TextColumn(
            "[progress.percentage]{task.percentage:>3.0f}%"
            " ({task.completed}/{task.total}, "
            "[bold yellow]{task.fields[running]} running[/])"
        ),
        TimeElapsedColumn(),
        console=console,
        transient=transient,
    )
    task_id = progress.add_task(description, total=0, running=0)
    return progress, task_id


def should_show_progress(no_progress_flag: bool) -> bool:
    """Decide whether to render a progress bar.

    Disabled when the user explicitly passed ``--no-progress`` or when
    stderr is not a TTY (e.g. redirected to a file / CI logs).
    """
    if no_progress_flag:
        return False
    return sys.stderr.isatty()


# ---------------------------------------------------------------------------
# Thread-safe counter (for run_eval.py's ThreadPoolExecutor)
# ---------------------------------------------------------------------------


class ThreadSafeProgressTracker:
    """Thin wrapper around a rich Progress that keeps ``running`` count
    correct when tasks start/finish from multiple threads.

    Usage::

        tracker = ThreadSafeProgressTracker(progress, task_id, total=N)
        # For each job:
        tracker.job_started()
        try:
            ... do work ...
        finally:
            tracker.job_finished()
    """

    def __init__(self, progress: Progress, task_id: TaskID, total: int) -> None:
        import threading

        self._progress = progress
        self._task_id = task_id
        self._lock = threading.Lock()
        self._running = 0
        self._done = 0
        self._total = total
        self._progress.update(task_id, total=total, completed=0, running=0)

    def job_started(self) -> None:
        """Call when a worker thread picks up a job."""
        with self._lock:
            self._running += 1
            self._progress.update(
                self._task_id,
                running=self._running,
            )

    def job_finished(self) -> None:
        """Call when a worker thread finishes a job (success or error)."""
        with self._lock:
            self._running = max(0, self._running - 1)
            self._done += 1
            self._progress.update(
                self._task_id,
                completed=self._done,
                running=self._running,
            )

    @property
    def done(self) -> int:
        with self._lock:
            return self._done

    @property
    def running(self) -> int:
        with self._lock:
            return self._running


# ---------------------------------------------------------------------------
# Async-safe counter (for judge.py's asyncio semaphore pattern)
# ---------------------------------------------------------------------------


class AsyncProgressTracker:
    """Same idea as :class:`ThreadSafeProgressTracker` but for asyncio.

    Since asyncio tasks all run on the same event loop, we don't strictly
    need a lock; we keep one anyway for clarity and in case someone later
    mixes threads in.
    """

    def __init__(self, progress: Progress, task_id: TaskID, total: int) -> None:
        self._progress = progress
        self._task_id = task_id
        self._running = 0
        self._done = 0
        self._total = total
        self._progress.update(task_id, total=total, completed=0, running=0)

    def job_started(self) -> None:
        self._running += 1
        self._progress.update(self._task_id, running=self._running)

    def job_finished(self) -> None:
        self._running = max(0, self._running - 1)
        self._done += 1
        self._progress.update(
            self._task_id,
            completed=self._done,
            running=self._running,
        )

    @property
    def done(self) -> int:
        return self._done

    @property
    def running(self) -> int:
        return self._running
