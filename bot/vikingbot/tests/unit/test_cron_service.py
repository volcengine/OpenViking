"""Tests for CronService timer re-arm behavior around executing ticks."""

import asyncio
import contextlib

from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore


async def test_arm_timer_does_not_cancel_running_tick(tmp_path):
    """While a tick is executing jobs, _arm_timer must not cancel/replace the timer."""
    service = CronService(store_path=tmp_path / "cron.json")
    service._running = True

    async def _long_sleep():
        await asyncio.sleep(3600)

    original_task = asyncio.create_task(_long_sleep())
    service._timer_task = original_task
    service._executing = True  # simulate a tick in progress

    service._arm_timer()

    # Yield control so any (buggy) cancellation would take effect.
    await asyncio.sleep(0)

    assert service._timer_task is original_task
    assert not original_task.cancelled()

    original_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await original_task


async def test_arm_timer_reschedules_when_not_executing(tmp_path):
    """Sanity check: when not executing, _arm_timer replaces the timer task."""
    service = CronService(store_path=tmp_path / "cron.json")
    service._running = True

    async def _long_sleep():
        await asyncio.sleep(3600)

    stale_task = asyncio.create_task(_long_sleep())
    service._timer_task = stale_task
    service._executing = False

    service._arm_timer()
    await asyncio.sleep(0)

    # No jobs scheduled -> _get_next_wake_ms returns None -> no new task armed,
    # but the stale task must have been cancelled (not left running).
    assert stale_task.cancelled()

    for task in {stale_task, service._timer_task}:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def test_cancelled_tick_does_not_rearm(tmp_path):
    """A tick cancelled mid-execution (stop or loop shutdown) must not arm a new timer."""
    service = CronService(store_path=tmp_path / "cron.json")
    service._running = True
    service._store = CronStore(
        jobs=[
            CronJob(
                id="job1",
                name="job1",
                enabled=True,
                schedule=CronSchedule(kind="every", every_ms=60_000),
                payload=CronPayload(message="hi"),
                state=CronJobState(next_run_at_ms=1),
            )
        ]
    )

    job_started = asyncio.Event()

    async def on_job(job):
        job_started.set()
        await asyncio.sleep(3600)

    service.on_job = on_job

    tick_task = asyncio.create_task(service._on_timer())
    service._timer_task = tick_task

    await asyncio.wait_for(job_started.wait(), timeout=5)
    tick_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tick_task

    new_timer = service._timer_task
    # Clean up first so a failure does not leak a pending task.
    if new_timer is not None and new_timer is not tick_task:
        new_timer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await new_timer

    assert service._executing is False
    assert new_timer is tick_task
