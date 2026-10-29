# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Leader-elected Watch scheduler.

Every node serves the Watch API and may start manual runs. Eligible nodes
compete through AGFS leadership; the leader polls due Watches and sweeps runs
whose Tasks have finished, disappeared or exceeded the run timeout. AGFS
signals the active term when its lease is lost.

Leadership only avoids duplicated polling. Exclusion comes from beginning a run:
the target's Tree PathLock is held while ``current_run_id`` is set by a
compare-and-set, and the run ID is the ingestion Task ID, so a run is
submitted, observed and settled by that ID from any node.

Occupancy is released only once the run's Task is terminal, or once it is
certain the Task will never exist: a submission may create its Task only
while its row is still occupied by it, not deleted and not fenced, and
whoever releases a run without a Task first fences or deletes the row.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Set

from openviking.connector.auth import is_external_feishu_auth, restore_feishu_request
from openviking.connector.delegate import ConnectorDelegate
from openviking.resource.feishu_watch_auth import (
    FeishuOAuthClient,
    FeishuTokenRefreshError,
    apply_feishu_refreshed_token,
    feishu_auth_state_needs_refresh,
    is_feishu_auth_state,
)
from openviking.resource.git_watch_auth import (
    git_http_auth_config_from_state,
    is_git_http_auth_state,
)
from openviking.resource.watch_manager import WatchManager, WatchPathLocks
from openviking.resource.watch_store import WatchStore
from openviking.resource.watch_types import (
    TERMINAL_RUN_STATUSES,
    WatchRun,
    WatchRunResult,
    WatchState,
    WatchTask,
)
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.service.task_domain import TaskStatus
from openviking.service.task_tracker import get_task_tracker, task_admission
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher, run_to_completion
from openviking.storage.errors import LockAcquisitionError
from openviking.utils.git_auth import GIT_AUTH_FAILED
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class WatchScheduler:
    POLL_INTERVAL = 15.0
    LEADER_TTL = 45.0
    DUE_BATCH = 100
    # A live submission past this is fenced off: it can no longer create its
    # Task, so releasing it never leaves a running Task unprotected.
    TASK_MISSING_GRACE = 60 * 60.0
    RUN_TIMEOUT = 3 * 60 * 60.0
    CANCEL_GRACE = 30 * 60.0

    def __init__(
        self,
        resource_service: Any,
        store: WatchStore,
        *,
        viking_fs: Any,
        runtime_config_manager: Any = None,
        leader_enabled: bool = True,
        max_concurrency: int = 4,
        poll_interval: float = POLL_INTERVAL,
        leader_ttl: float = LEADER_TTL,
        task_missing_grace: float = TASK_MISSING_GRACE,
        run_timeout: float = RUN_TIMEOUT,
        cancel_grace: float = CANCEL_GRACE,
        locks: Optional[WatchPathLocks] = None,
        leadership_candidate: Any = None,
    ) -> None:
        if (
            min(
                max_concurrency,
                poll_interval,
                leader_ttl,
                task_missing_grace,
                run_timeout,
                cancel_grace,
            )
            <= 0
        ):
            raise ValueError("Watch scheduling limits must be positive")
        if leader_ttl <= poll_interval:
            raise ValueError("leader_ttl must exceed poll_interval")
        self._resource_service = resource_service
        self._store = store
        self._viking_fs = viking_fs
        self._watch_manager = WatchManager(store, locks or WatchPathLocks(viking_fs))
        self._runtime_config_manager = runtime_config_manager
        self._leader_enabled = leader_enabled
        # Queue consumers use separate event loops. Scheduler Tasks and its
        # submission semaphore belong to the loop that starts the scheduler.
        self._dispatcher = OwnerLoopDispatcher()
        self._slots = asyncio.Semaphore(max_concurrency)
        self._max_concurrency = max_concurrency
        self._poll_interval = poll_interval
        self._task_missing_grace = task_missing_grace
        self._run_timeout = run_timeout
        self._cancel_grace = cancel_grace
        self._owner = str(uuid.uuid4())
        self._leadership_candidate = leadership_candidate
        if leader_enabled and self._leadership_candidate is None:
            self._leadership_candidate = viking_fs._async_agfs.leader_candidate(
                "watch-scheduler",
                self._owner,
                ttl_secs=leader_ttl,
                renew_interval_secs=poll_interval,
                retry_interval_secs=poll_interval,
                mode="local" if store.provider == "file" else "distributed",
            )
        self._is_leader = False
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._background: Set[asyncio.Task] = set()
        self._submissions: Dict[str, asyncio.Task] = {}
        self._cancel_requested: Set[str] = set()
        self._dispatching = 0

    @property
    def watch_manager(self) -> WatchManager:
        return self._watch_manager

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
        await self._watch_manager.initialize()
        self._running = True
        if self._leader_enabled:
            self._loop_task = asyncio.create_task(self._leader_loop())
        logger.info(
            "[WatchScheduler] Started store=%s leader_candidate=%s",
            self._store.provider,
            self._leader_enabled,
        )

    async def stop(self) -> None:
        if self._running:
            await self._dispatcher.run(self._stop)

    async def _stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        tasks = set(self._background) | set(self._submissions.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background.clear()
        self._submissions.clear()
        self._is_leader = False
        await self._watch_manager.close()
        logger.info("[WatchScheduler] Stopped")

    def _spawn(self, coroutine: Any) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    # ── Leader loop ─────────────────────────────────────────────────────

    async def _leader_loop(self) -> None:
        candidate = self._leadership_candidate
        if candidate is None:
            return
        try:
            async with candidate:
                async for leadership in candidate:
                    if not self._running:
                        return
                    self._is_leader = True
                    logger.info("[WatchScheduler] Leadership acquired owner=%s", self._owner)
                    try:
                        await self._run_leadership_term(leadership.stop_event)
                    finally:
                        self._is_leader = False
                        logger.info("[WatchScheduler] Leadership stopped owner=%s", self._owner)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[WatchScheduler] Leader candidate failed")

    async def _run_leadership_term(self, stop_event: asyncio.Event) -> None:
        while self._running and not stop_event.is_set():
            try:
                await self.sweep(stop_event)
                if not stop_event.is_set():
                    await self.dispatch_due(stop_event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[WatchScheduler] Leader round failed")
            if stop_event.is_set():
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass

    async def dispatch_due(self, stop_event: Optional[asyncio.Event] = None) -> int:
        """Begin due Watches up to free submission capacity; returns runs started.

        A Watch that cannot begin (its target is busy) stays due, so the
        scan pages past it instead of letting it hold the capacity.
        """
        if not self._running:
            return 0
        return await self._dispatcher.run(lambda: self._dispatch_due(stop_event))

    async def _dispatch_due(self, stop_event: Optional[asyncio.Event]) -> int:
        capacity = self._max_concurrency - self._dispatching
        started = skipped = 0
        while self._running and started < capacity:
            if stop_event is not None and stop_event.is_set():
                break
            page = await self._store.due(self.DUE_BATCH, offset=skipped)
            for state in page:
                if started >= capacity or (stop_event is not None and stop_event.is_set()):
                    break
                launched = self._launch_run(state.task)
                if launched is None:
                    break
                begun, _runner = launched
                if not await asyncio.shield(begun):
                    skipped += 1
                    continue
                started += 1
            if len(page) < self.DUE_BATCH:
                break
        return started

    def _launch_run(
        self, task: WatchTask, *, manual: bool = False
    ) -> Optional[tuple[asyncio.Future[bool], asyncio.Task]]:
        if not self._running:
            return None
        run_id = str(uuid.uuid4())
        begun: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._dispatching += 1
        runner = self._spawn(self._run(task, run_id, manual=manual, begun=begun))
        self._submissions[run_id] = runner
        return begun, runner

    async def _begin_run(
        self, task: WatchTask, run_id: str, *, manual: bool = False
    ) -> Optional[WatchRun]:
        # Locking a missing path materializes it on some PathLock backends, so a
        # native Watch whose target is gone is retired before any lock is taken.
        if task.to_uri and not ConnectorDelegate.is_watch_auth_state(task.auth_state):
            if not await self._target_exists(task):
                error = f"Watched target URI does not exist: {task.to_uri}"
                if await self._watch_manager.deactivate_idle(
                    task.task_id, target_uri=task.to_uri, error=error
                ):
                    logger.warning(
                        "[WatchScheduler] Deactivating Watch %s: %s", task.task_id, error
                    )
                return None
        try:
            async with self._watch_manager.locks.tree(task.account_id, task.to_uri):
                return await self._store.begin_run(
                    task.task_id,
                    run_id=run_id,
                    target_uri=task.to_uri,
                    manual=manual,
                )
        except LockAcquisitionError:
            logger.info(
                "[WatchScheduler] Target busy; Watch %s waits for the next round",
                task.task_id,
            )
            return None

    async def _run(
        self,
        task: WatchTask,
        run_id: str,
        *,
        manual: bool,
        begun: asyncio.Future[bool],
    ) -> None:
        run: Optional[WatchRun] = None
        try:
            try:
                run = await run_to_completion(lambda: self._begin_run(task, run_id, manual=manual))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[WatchScheduler] Failed to begin Watch %s", task.task_id)
                await run_to_completion(
                    lambda: self._release_without_task(
                        task,
                        run_id,
                        WatchRunResult(status="failed", error="Watch run failed to begin"),
                    )
                )
                return
            if run is None:
                return
            if not begun.done():
                begun.set_result(True)
            await self._submit(run)
        except asyncio.CancelledError:
            await run_to_completion(
                lambda: self._release_without_task(
                    task,
                    run_id,
                    WatchRunResult(status="cancelled", error="Watch submission cancelled"),
                )
            )
            raise
        finally:
            if not begun.done():
                begun.set_result(False)
            self._submissions.pop(run_id, None)
            self._dispatching -= 1

    async def schedule_task(self, task_id: str) -> bool:
        """Start a run now, from any node; False when the Watch is occupied or busy."""
        if not self._running:
            raise RuntimeError("Watch scheduler is not running")
        return await self._dispatcher.run(lambda: self._schedule_task(task_id))

    async def _schedule_task(self, task_id: str) -> bool:
        if not self._running:
            raise RuntimeError("Watch scheduler is not running")
        state = await self._store.get_state(task_id)
        if state is None or state.deleted or state.current_run_id:
            return False
        launched = self._launch_run(state.task, manual=True)
        if launched is None:
            return False
        begun, runner = launched
        if not await asyncio.shield(begun):
            await asyncio.gather(runner, return_exceptions=True)
            return False
        await runner
        return True

    # ── Submission ──────────────────────────────────────────────────────

    async def _submit(self, run: WatchRun) -> None:
        """Submit a begun run; the owning runner handles cancellation cleanup."""
        task = run.task
        try:
            async with self._slots:
                with task_admission(run.run_id, lambda: self._admits(run)):
                    result = await self._submit_resource(run)
        except Exception as exc:
            failure = WatchRunResult(
                status="failed",
                error=str(exc) or type(exc).__name__,
                deactivate=isinstance(exc, FileNotFoundError)
                or getattr(exc, "code", None) == GIT_AUTH_FAILED,
            )
            if await run_to_completion(lambda: self._finish_unsubmitted(run, failure)):
                logger.warning("[WatchScheduler] Watch %s submission failed: %s", task.task_id, exc)
                return
            result = {"task_id": run.run_id}
        if result is None:
            return
        if result.get("task_id"):
            self._spawn(self._await_settlement(run))
            return
        status = str(result.get("status") or "completed").lower()
        if status not in TERMINAL_RUN_STATUSES:
            status = "completed"
        await run_to_completion(
            lambda: self._watch_manager.finish_run(
                run.task_id,
                run.run_id,
                self._result(task, status, result, result.get("error")),
            )
        )

    async def _admits(self, run: WatchRun) -> bool:
        return await self._watch_manager.admits_run(run.task_id, run.run_id)

    async def _finish_unsubmitted(self, run: WatchRun, result: WatchRunResult) -> bool:
        """Settle a run whose Task was never committed; False when the Task exists."""
        return await self._release_without_task(run.task, run.run_id, result)

    async def _release_without_task(
        self, task: WatchTask, run_id: str, result: WatchRunResult
    ) -> bool:
        if not await self._store.fence_run(task.task_id, run_id):
            return False
        record = await get_task_tracker().get(
            run_id, account_id=task.account_id, user_id=task.user_id
        )
        if record is not None:
            return False
        return await self._watch_manager.finish_run(task.task_id, run_id, result)

    async def _submit_resource(self, run: WatchRun) -> Optional[dict[str, Any]]:
        task = run.task
        auth = task.auth_state
        connector = ConnectorDelegate.is_watch_auth_state(auth)
        ctx = self._context(task)
        if (
            not connector
            and not task.path.startswith(("http://", "https://", "git@", "ssh://", "git://"))
            and not Path(task.path).exists()
        ):
            await self._deactivate(run, f"Resource path does not exist: {task.path}")
            return None
        kwargs = dict(task.processor_kwargs or {})
        kwargs.pop("build_index", None)
        kwargs.pop("summarize", None)
        if is_external_feishu_auth(auth):
            ctx.api_key, kwargs["args"] = await restore_feishu_request(
                self._resource_service._connector,
                auth,
                path=task.path,
                ctx=ctx,
            )
        elif is_feishu_auth_state(auth):
            try:
                if feishu_auth_state_needs_refresh(auth):
                    from openviking.config.feishu import get_effective_feishu_config

                    if self._runtime_config_manager is None:
                        raise RuntimeError("Runtime config manager is not initialized")
                    config = await get_effective_feishu_config(
                        self._runtime_config_manager,
                        task.account_id,
                    )
                    refreshed = await FeishuOAuthClient.from_auth_state(
                        auth,
                        config=config,
                    ).refresh_user_access_token(auth.get("refresh_token"))
                    auth = apply_feishu_refreshed_token(auth, refreshed)
                    await self._watch_manager.update_auth_state(
                        task.task_id,
                        auth,
                        run_id=run.run_id,
                    )
                kwargs["feishu_access_token"] = auth["access_token"]
            except FeishuTokenRefreshError as exc:
                if not exc.permanent:
                    raise
                await self._deactivate(run, str(exc))
                return None
        elif is_git_http_auth_state(auth):
            kwargs["auth_config"] = git_http_auth_config_from_state(auth, task.path)
        elif connector:
            (
                ctx.api_key,
                kwargs["add_type"],
                kwargs["args"],
                connector_auth_config,
            ) = await self._resource_service._connector.restore_watch_request(
                auth,
                account_id=task.account_id,
                path=task.path,
            )
            if connector_auth_config is not None:
                kwargs["connector_auth_config"] = connector_auth_config
            kwargs["connector_states"] = task.connector_states
        return await self._resource_service.refresh_resource(
            path=task.path,
            ctx=ctx,
            to=task.to_uri,
            to_is_directory=task.to_is_directory,
            parent=task.parent_uri,
            reason=task.reason,
            instruction=task.instruction,
            build_index=task.build_index,
            summarize=task.summarize,
            processing_mode=task.processing_mode,
            watch_interval=task.watch_interval,
            enforce_public_remote_targets=True,
            wait_for_completion=False,
            task_id=run.run_id,
            **kwargs,
        )

    async def _target_exists(self, task: WatchTask) -> bool:
        # Connector targets are rebuilt by the plugin's own writes; only native
        # Watches are tied to their target's existence.
        try:
            await self._viking_fs.stat(task.to_uri, ctx=self._context(task), skip_count=True)
        except Exception as exc:
            if not is_not_found_error(exc):
                raise
            return False
        return True

    async def _deactivate(self, run: WatchRun, error: str) -> None:
        logger.warning("[WatchScheduler] Deactivating Watch %s: %s", run.task_id, error)
        await run_to_completion(
            lambda: self._watch_manager.finish_run(
                run.task_id,
                run.run_id,
                WatchRunResult(status="failed", error=error, deactivate=True),
            )
        )

    @staticmethod
    def _context(task: WatchTask) -> RequestContext:
        try:
            role = Role(task.original_role)
        except ValueError:
            role = Role.USER
        return RequestContext(
            user=UserIdentifier(task.account_id, task.user_id),
            role=role,
            bypass_acl=True,
        )

    # ── Settlement ──────────────────────────────────────────────────────

    @staticmethod
    def _result(
        task: WatchTask,
        status: str,
        result: Optional[dict[str, Any]],
        error: Optional[str],
    ) -> WatchRunResult:
        result = result or {}
        connector = ConnectorDelegate.is_watch_auth_state(task.auth_state)
        states = result.get("connector_states")
        has_states = connector and status == "completed" and "connector_states" in result
        if has_states and not isinstance(states, dict):
            status, error, has_states = "failed", "connector_states must be an object", False
        if status in {"failed", "error"} and not error:
            errors = result.get("errors")
            error = "; ".join(str(item) for item in errors) if errors else "watch ingestion failed"
        return WatchRunResult(
            status=status,
            execution_task_id=result.get("task_id"),
            error=error,
            deactivate=result.get("code") == GIT_AUTH_FAILED,
            connector_states=states if has_states else None,
            has_connector_states=has_states,
        )

    async def _await_settlement(self, run: WatchRun) -> None:
        """Settle promptly on the submitting node; the leader sweep covers any gap."""
        task = run.task
        try:
            await get_task_tracker().wait(
                run.run_id,
                account_id=task.account_id,
                user_id=task.user_id,
                timeout=self._run_timeout,
            )
        except (KeyError, TimeoutError, asyncio.TimeoutError):
            return
        await self.settle(run.task_id, run.run_id)

    async def settle(self, task_id: str, run_id: str, *, follow: bool = False) -> bool:
        """Record a terminal Task, optionally following active Work in the background."""
        state = await self._store.get_state(task_id)
        if state is None or state.current_run_id != run_id:
            return False
        task = state.task
        record = await get_task_tracker().get(
            run_id, account_id=task.account_id, user_id=task.user_id
        )
        if record is None:
            return False
        if not record.is_terminal():
            if follow and self._running:
                await self._dispatcher.run(
                    lambda: self._follow_settlement(
                        WatchRun(task_id=task_id, run_id=run_id, task=task)
                    )
                )
            return False
        result = dict(record.result or {})
        result["task_id"] = record.task_id
        return await run_to_completion(
            lambda: self._watch_manager.finish_run(
                task_id,
                run_id,
                self._result(task, record.status.value, result, record.error),
            )
        )

    async def _follow_settlement(self, run: WatchRun) -> None:
        # stop() may have run while a worker callback was reading its Task.
        if self._running:
            self._spawn(self._await_settlement(run))

    async def sweep(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Settle finished runs and force-settle missing or timed-out ones."""
        tracker = get_task_tracker()
        after = ""
        seen: Set[str] = set()
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            page = await self._store.running(after=after, limit=self.DUE_BATCH)
            for state in page:
                if stop_event is not None and stop_event.is_set():
                    return
                seen.add(state.current_run_id or "")
                try:
                    await self._sweep_one(tracker, state)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "[WatchScheduler] Sweep failed for Watch %s", state.task.task_id
                    )
            if len(page) < self.DUE_BATCH:
                self._cancel_requested &= seen
                return
            after = page[-1].task.task_id

    async def _sweep_one(self, tracker: Any, state: WatchState) -> None:
        task, run_id = state.task, state.current_run_id
        age = state.now - (state.run_started_at or state.now)
        record = await tracker.get(run_id, account_id=task.account_id, user_id=task.user_id)
        if record is None:
            if age > self._task_missing_grace:
                await self._release_without_task(
                    task,
                    run_id,
                    WatchRunResult(
                        status="error",
                        execution_task_id=run_id,
                        error="Watch run Task is missing",
                    ),
                )
            return
        if record.is_terminal():
            self._cancel_requested.discard(run_id)
            await self.settle(task.task_id, run_id)
            return
        # Request cancellation even when the first sweep arrives after the
        # release deadline. The deadline remains relative to the run's start.
        if (
            age > self._run_timeout
            and record.status.value != TaskStatus.CANCELLING.value
            and run_id not in self._cancel_requested
        ):
            logger.warning(
                "[WatchScheduler] Watch %s run %s exceeded %gs; cancelling its Task",
                task.task_id,
                run_id,
                self._run_timeout,
            )
            if not await self._cancel_run_task(tracker, task, run_id):
                # A transient failure must be retried before releasing occupancy.
                return
            self._cancel_requested.add(run_id)
        if age > self._run_timeout + self._cancel_grace:
            self._cancel_requested.discard(run_id)
            # Cancellation may have made the Task terminal; preserve its result.
            if await self.settle(task.task_id, run_id):
                return
            logger.warning(
                "[WatchScheduler] Watch %s run %s exceeded the %gs release deadline; "
                "releasing it after requesting cancellation",
                task.task_id,
                run_id,
                self._run_timeout + self._cancel_grace,
            )
            await self._watch_manager.finish_run(
                task.task_id,
                run_id,
                WatchRunResult(
                    status="failed",
                    execution_task_id=run_id,
                    error=f"Watch run timed out after {self._run_timeout:g}s",
                ),
            )

    async def delete_tasks(self, account_id: str, user_id: Optional[str] = None) -> None:
        """Remove an identity's Watches and release the runs they still occupy.

        Rows are tombstoned before their Tasks are looked up, and a submission
        may only create its Task while its row is not deleted, so a Task
        created by a submission on any node is either found here or withdrawn
        by its creator, also after the rows are purged. Local submissions are
        cancelled and awaited first so they stop promptly.
        """
        tracker = get_task_tracker()
        states = await self._store.delete_tasks(account_id, user_id)
        caller = asyncio.current_task()
        run_ids = {state.current_run_id for state in states if state.current_run_id}
        if run_ids and self._submissions:
            await self._dispatcher.run(lambda: self._cancel_submissions(run_ids, caller))
        for state in states:
            task, run_id = state.task, state.current_run_id
            record = await tracker.get(run_id, account_id=task.account_id, user_id=task.user_id)
            if record is not None and not record.is_terminal():
                await self._stop_run_task(tracker, task, run_id)
            await self._watch_manager.finish_run(
                task.task_id,
                run_id,
                WatchRunResult(status="cancelled", execution_task_id=run_id),
            )

    async def _cancel_submissions(self, run_ids: Set[str], caller: Optional[asyncio.Task]) -> None:
        local = [
            submission
            for run_id in run_ids
            if (submission := self._submissions.get(run_id)) is not None
            and submission is not caller
        ]
        for submission in local:
            submission.cancel()
        if local:
            await asyncio.gather(*local, return_exceptions=True)

    @staticmethod
    async def _stop_run_task(tracker: Any, task: WatchTask, run_id: str) -> None:
        """Cancel the run's Task, or fail one that cannot be cancelled.

        A Connector import runs remotely and cannot be cancelled; failing its
        Task ends local monitoring, so identity cleanup need not wait for it.
        """
        owner = {"account_id": task.account_id, "user_id": task.user_id}
        try:
            await tracker.cancel(run_id, **owner)
        except ValueError:
            try:
                await tracker.fail(run_id, "Watch deleted", **owner)
            except Exception:
                logger.exception(
                    "[WatchScheduler] Failed to stop Task %s of Watch %s", run_id, task.task_id
                )
        except Exception:
            logger.exception(
                "[WatchScheduler] Failed to cancel Task %s of Watch %s", run_id, task.task_id
            )

    @staticmethod
    async def _cancel_run_task(tracker: Any, task: WatchTask, run_id: str) -> bool:
        try:
            await tracker.cancel(run_id, account_id=task.account_id, user_id=task.user_id)
        except ValueError:
            # Non-cancellable Tasks still use the bounded release policy.
            logger.info("[WatchScheduler] Task %s cannot be cancelled", run_id)
        except Exception:
            logger.exception(
                "[WatchScheduler] Failed to cancel Task %s of Watch %s", run_id, task.task_id
            )
            return False
        return True
