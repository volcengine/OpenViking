"""Tests for LoCoMo benchmark progress utilities."""

from rich.console import Console

from benchmark.locomo.vikingbot.progress_utils import (
    AsyncProgressTracker,
    ThreadSafeProgressTracker,
    make_three_state_progress,
)


def test_locomo_progress_tracker_records_failed_jobs():
    console = Console(force_terminal=False, width=100)
    for tracker_type in (ThreadSafeProgressTracker, AsyncProgressTracker):
        progress, task_id = make_three_state_progress(description="Test", transient=True)
        tracker = tracker_type(progress, task_id, total=2)

        tracker.job_started()
        tracker.job_finished()
        with console.capture() as first:
            console.print(*progress.get_renderables())
        assert "(1/2)" in first.get()
        assert "failed" not in first.get()

        tracker.job_started()
        tracker.job_finished(failed=True)
        with console.capture() as final:
            console.print(*progress.get_renderables())
        assert "(2/2, 1 failed)" in final.get()
