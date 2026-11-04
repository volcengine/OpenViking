# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Leader-polled Watch triggers and observation of existing resource Tasks."""

import asyncio
import uuid
from functools import partial
from typing import Any, Optional

from openviking.concurrency import AsyncSemaphore
from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_types import WatchRun, WatchRunning, WatchRunResult
from openviking.service.task_domain import TERMINAL_TASK_STATUSES
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher, run_to_completion
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class WatchRuntime:
    POLL_INTERVAL = 15.0
    LEADER_TTL = 45.0
    DUE_BATCH = 100
    TASK_MISSING_GRACE = 3600.0
    RUN_TIMEOUT = 10800.0

    def __init__(
        self,
        watch_manager: WatchManager,
        *,
        leader_enabled: bool = True,
        max_concurrency: int = 4,
        poll_interval: float = POLL_INTERVAL,
        leader_ttl: float = LEADER_TTL,
        leader_provider: str = "auto",
        task_missing_grace: float = TASK_MISSING_GRACE,
        run_timeout: float = RUN_TIMEOUT,
        leadership_candidate: Any = None,
    ) -> None:
        if (
            min(max_concurrency, poll_interval, leader_ttl, task_missing_grace, run_timeout) <= 0
            or leader_ttl <= poll_interval
        ):
            raise ValueError("Watch scheduling limits must be positive; ttl must exceed poll")
        self.watch_manager = watch_manager
        self._leader_enabled = leader_enabled
        self._max_concurrency = max_concurrency
        self._poll_interval = poll_interval
        self._run_timeout = run_timeout
        self._task_missing_grace = task_missing_grace
        self._dispatcher = OwnerLoopDispatcher()
        self._leadership_candidate = leadership_candidate
        if leader_enabled and leadership_candidate is None:
            leader_mode = self._leader_mode(leader_provider)
            viking_fs = watch_manager._viking_fs
            if viking_fs is None:
                raise ValueError(
                    "Watch leader election requires VikingFS or a leadership candidate"
                )
            self._leadership_candidate = viking_fs._async_agfs.leader_candidate(
                "watch-scheduler",
                str(uuid.uuid4()),
                ttl_secs=leader_ttl,
                renew_interval_secs=poll_interval,
                retry_interval_secs=poll_interval,
                mode=leader_mode,
            )
        self._running = False
        self._is_leader = False
        self._loop_task: Optional[asyncio.Task] = None
        self._submissions: dict[str, asyncio.Task] = {}
        self._reserved_runs: dict[str, WatchRun] = {}
        self._active_runs: set[str] = set()
        self._occupancy_initialized = False
        self._dispatch_lock = AsyncSemaphore()

    def _leader_mode(self, provider: str) -> str:
        provider = provider.strip().lower()
        if provider == "auto":
            return "local" if self.watch_manager.store.provider == "file" else "distributed"
        if provider == "local":
            return "local"
        if provider == "cache":
            return "distributed"
        raise ValueError("watch_leader_provider must be one of: 'auto', 'local', 'cache'")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    async def start(self) -> None:
        if self._running:
            return
        self._dispatcher.bind_current_loop()
        self._occupancy_initialized = False
        self._running = True
        if self._leader_enabled:
            self._loop_task = asyncio.create_task(self._leader_loop())

    async def stop(self, *, timeout: Optional[float] = None) -> None:
        """Keep dependencies open if a submission cannot finish before timeout."""
        await self._dispatcher.run(lambda: self._stop(timeout))

    async def _stop(self, timeout: Optional[float]) -> None:
        self._running = False
        reserved = list(self._reserved_runs.values())
        tasks = set(self._submissions.values())
        if self._loop_task is not None:
            tasks.add(self._loop_task)
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                raise TimeoutError(
                    "Watch Runtime coroutines have not stopped; keep dependencies open"
                )
        for run in reserved:
            await self._release(run)
        self._loop_task = None
        self._is_leader = False

    async def _leader_loop(self) -> None:
        try:
            leadership_candidate = self._leadership_candidate
            if leadership_candidate is None:
                raise RuntimeError("Watch leadership candidate is not initialized")
            async with leadership_candidate as candidate:
                async for leadership in candidate:
                    if not self._running:
                        return
                    self._is_leader = True
                    self._occupancy_initialized = False
                    try:
                        while self._continue(leadership.stop_event):
                            try:
                                await self._sweep(leadership.stop_event)
                                await self._dispatch_due(leadership.stop_event)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Watch Runtime round failed")
                            try:
                                await asyncio.wait_for(
                                    leadership.stop_event.wait(), self._poll_interval
                                )
                            except asyncio.TimeoutError:
                                pass
                    finally:
                        self._is_leader = False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Watch leader candidate failed")

    def _continue(self, stop_event: Optional[asyncio.Event]) -> bool:
        return self._running and (stop_event is None or not stop_event.is_set())

    async def dispatch_due(self, stop_event: Optional[asyncio.Event] = None) -> int:
        return await self._dispatcher.run(lambda: self._dispatch_due(stop_event))

    async def _dispatch_due(self, stop_event: Optional[asyncio.Event]) -> int:
        async with self._dispatch_lock:
            return await self._dispatch_due_locked(stop_event)

    async def _dispatch_due_locked(self, stop_event: Optional[asyncio.Event]) -> int:
        if not self._continue(stop_event):
            return 0
        if not self._occupancy_initialized:
            await self._sweep_locked(stop_event)
        if not self._occupancy_initialized or not self._continue(stop_event):
            return 0
        # A submitted coroutine may finish long before its real Task. Count
        # observed runs and pending submissions once per Watch, in memory.
        capacity = self._max_concurrency - len(self._active_runs | self._submissions.keys())
        if capacity <= 0:
            return 0
        runs = await self.watch_manager.due(min(capacity, self.DUE_BATCH))
        started = 0
        try:
            for run in runs:
                if not self._continue(stop_event):
                    break
                runner = asyncio.create_task(self.watch_manager.execute(run))
                self._submissions[run.task_id] = runner
                self._reserved_runs[run.task_id] = run
                runner.add_done_callback(partial(self._finished, run.task_id))
                started += 1
        finally:
            for run in runs[started:]:
                await self._release(run)
        return started

    async def _release(self, run: WatchRun) -> None:
        # Only an unassociated reservation can be released here. A real Task
        # remains occupied and is recovered through the normal sweep.
        await run_to_completion(
            lambda: self.watch_manager.finish_run(
                run.task_id,
                None,
                WatchRunResult(status="error", error="Watch dispatch interrupted"),
                started_at=run.started_at,
            )
        )

    def _finished(self, task_id: str, task: asyncio.Task) -> None:
        self._submissions.pop(task_id, None)
        self._reserved_runs.pop(task_id, None)
        if task.cancelled():
            self._active_runs.add(task_id)
            self._occupancy_initialized = False
        elif task.exception() is not None:
            # Failed cleanup may have left a durable run; recover before
            # admitting another submission rather than guessing its state.
            self._active_runs.add(task_id)
            self._occupancy_initialized = False
            logger.error("Watch trigger %s failed", task_id, exc_info=task.exception())
        elif task.result() is not None:
            self._active_runs.add(task_id)

    async def sweep(self, stop_event: Optional[asyncio.Event] = None) -> None:
        await self._dispatcher.run(lambda: self._sweep(stop_event))

    async def _sweep(self, stop_event: Optional[asyncio.Event]) -> None:
        async with self._dispatch_lock:
            await self._sweep_locked(stop_event)

    async def _sweep_locked(self, stop_event: Optional[asyncio.Event]) -> None:
        self._occupancy_initialized = False
        previous = set(self._active_runs)
        seen: set[str] = set()
        after = ""
        while self._continue(stop_event):
            page = await self.watch_manager.store.running(after=after, limit=self.DUE_BATCH)
            for state in page:
                if not self._continue(stop_event):
                    return
                task_id = state.task_id
                seen.add(task_id)
                self._active_runs.add(task_id)
                try:
                    if await self._sweep_one(state):
                        self._active_runs.discard(task_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Watch sweep failed for %s", state.task_id)
            if len(page) < self.DUE_BATCH:
                # Preserve submission receipts arriving during this scan, and
                # drop old runs already settled by another API/process.
                self._active_runs.difference_update(previous - seen)
                self._occupancy_initialized = True
                return
            after = page[-1].task_id

    async def _sweep_one(self, state: WatchRunning) -> bool:
        run_id = state.current_run_id
        owner = {"account_id": state.account_id, "user_id": state.user_id}
        tracker = self.watch_manager.tracker
        status = await tracker.get_task_status(run_id, **owner) if run_id else None
        age = state.now - state.run_started_at
        if status is None:
            if age > self._task_missing_grace:
                return await self.watch_manager.finish_run(
                    state.task_id,
                    run_id,
                    WatchRunResult(status="error", error="Watch run Task is missing"),
                    started_at=state.run_started_at,
                )
            return False
        if status not in TERMINAL_TASK_STATUSES:
            if age <= self._run_timeout:
                return False
            timeout_error = f"Watch run Task timed out after {self._run_timeout:g}s"
            timed_out_record = await tracker.get(run_id, **owner)
            if state.connector or (
                timed_out_record is not None
                and timed_out_record.task_type == "connector_import"
            ):
                try:
                    await tracker.cancel(run_id, **owner)
                except ValueError:
                    # A completion racing the timeout wins over timeout settlement.
                    record = await tracker.get(run_id, **owner)
                    if record is not None and record.is_terminal():
                        return await self.watch_manager.settle_running(state, record)
                    raise
                return await self.watch_manager.finish_run(
                    state.task_id,
                    run_id,
                    WatchRunResult(
                        status="failed",
                        execution_task_id=run_id,
                        error=timeout_error,
                    ),
                    started_at=state.run_started_at,
                )
            else:
                try:
                    await tracker.cancel(run_id, **owner)
                except ValueError as exc:
                    logger.warning("Failed to cancel timed-out Watch Task %s: %s", run_id, exc)
                status = await tracker.get_task_status(run_id, **owner)
                if status not in TERMINAL_TASK_STATUSES:
                    await tracker.fail(
                        run_id,
                        timeout_error,
                        **owner,
                    )
        record = await tracker.get(run_id, **owner)
        return await self.watch_manager.settle_running(state, record)
