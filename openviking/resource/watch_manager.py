# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch management, resource submission and single-run settlement.

Permission and runtime conditions are evaluated by the store. Target stability comes
from PathLock: a Watch row is created, and a run starts occupying it, only
while its target's Tree lock is held; transfers rewrite rows inside their own
lock and check occupancy there.

Initialize after FS and TaskTracker; close after callers, Runtime, resource
monitors and queue callbacks stop. The Manager spawns no background coroutines.
"""

import asyncio
import inspect
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence

from openviking.connector.auth import is_external_feishu_auth, restore_feishu_request
from openviking.connector.delegate import ConnectorDelegate
from openviking.observability.http_error_context import sanitize_public_http_error
from openviking.resource.git_watch_auth import (
    git_http_auth_config_from_state,
    is_git_http_auth_state,
)
from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode
from openviking.resource.watch_store import WatchListPage, WatchMoveResult, WatchStore
from openviking.resource.watch_types import (
    MUTABLE_FIELDS,
    PermissionDeniedError,
    WatchAuthority,
    WatchCandidate,
    WatchPatch,
    WatchReceipt,
    WatchRun,
    WatchRunning,
    WatchRunResult,
    WatchState,
    WatchTask,
)
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool, run_to_completion
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.utils.git_auth import GIT_AUTH_FAILED
from openviking.utils.network_guard import ensure_public_remote_target
from openviking_cli.exceptions import ConflictError, FailedPreconditionError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = ["PermissionDeniedError", "WatchManager", "WatchPathLocks", "WatchTask"]


class WatchPathLocks:
    """Tree PathLocks on Watch targets, mapped into each account's AGFS namespace."""

    def __init__(self, viking_fs: Any) -> None:
        self._fs = viking_fs

    @staticmethod
    def _context(account_id: str) -> Any:
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        return RequestContext(user=UserIdentifier(account_id, "default"), role=Role.ROOT)

    @asynccontextmanager
    async def tree(
        self,
        account_id: str,
        uri: str,
        *,
        timeout: float = 0.0,
        owner_lease_ref: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        agfs = self._fs._async_agfs
        lease = await agfs.pathlock_acquire_tree(
            self._fs._uri_to_path(uri, ctx=self._context(account_id)),
            timeout_secs=timeout,
            owner_lease_ref=owner_lease_ref,
        )
        try:
            yield lease
        finally:
            await run_to_completion(lambda: agfs.pathlock_release(lease))


class WatchManager:
    DEFAULT_GIT_PROBE_TIMEOUT = 30.0

    def __init__(
        self,
        store: WatchStore,
        locks: Optional[WatchPathLocks] = None,
        *,
        viking_fs: Any = None,
        tracker: Any = None,
        resource_service: Any = None,
        runtime_config_manager: Any = None,
    ) -> None:
        if locks is None:
            if viking_fs is None:
                raise ValueError("WatchManager requires target locks or VikingFS")
            locks = WatchPathLocks(viking_fs)
        self._store = store
        self._locks = locks
        self._viking_fs = viking_fs if viking_fs is not None else getattr(locks, "_fs", None)
        self._tracker = tracker
        self._resource_service = resource_service
        self._runtime_config_manager = runtime_config_manager
        self._trigger_locks: KeyedAsyncLockPool[str] = KeyedAsyncLockPool()
        self._deletion_guard: Optional[Callable[[str, str], Any]] = None

    def set_deletion_guard(self, guard: Optional[Callable[[str, str], Any]]) -> None:
        """Refuse new Watches for identities the guard reports as being deleted."""
        self._deletion_guard = guard

    async def _owner_deleting(self, account_id: str, user_id: str) -> bool:
        guard = self._deletion_guard
        if guard is None:
            return False
        result = guard(account_id, user_id)
        return bool(await result if inspect.isawaitable(result) else result)

    @property
    def store(self) -> WatchStore:
        return self._store

    @property
    def locks(self) -> WatchPathLocks:
        return self._locks

    async def initialize(self) -> None:
        await self._store.initialize()

    async def close(self) -> None:
        await self._store.close()

    async def create_task(
        self,
        path: str,
        account_id: str = "default",
        user_id: str = "default",
        original_role: str = "user",
        source_type: Optional[str] = None,
        to_uri: Optional[str] = None,
        to_is_directory: Optional[bool] = None,
        parent_uri: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        watch_interval: float = 60.0,
        build_index: bool = True,
        summarize: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        processor_kwargs: Optional[Dict[str, Any]] = None,
        auth_state: Optional[Dict[str, Any]] = None,
        connector_states: Optional[Dict[str, Any]] = None,
        is_active: bool = True,
        *,
        initial_run_id: Optional[str] = None,
        lease_ref: Optional[Dict[str, Any]] = None,
    ) -> WatchTask:
        """Create a Watch under its target's Tree lock.

        *initial_run_id* registers the caller's ingestion Task as the first
        run. *lease_ref* is a lease the caller already holds on the target.

        Identity deletion records its fence before removing the identity's
        Watches; this checks the fence again after the insert, so a Watch
        created concurrently is either removed by the deletion or withdrawn here.
        """
        if not path:
            raise ValueError("Path is required")
        if not to_uri:
            raise ValueError("Watch requires a target URI")
        if watch_interval <= 0:
            raise ValueError("watch_interval must be > 0")
        task = WatchTask(
            path=path,
            source_type=source_type,
            to_uri=to_uri,
            to_is_directory=to_is_directory,
            parent_uri=parent_uri,
            reason=reason,
            instruction=instruction,
            watch_interval=watch_interval,
            build_index=build_index,
            summarize=summarize,
            processing_mode=processing_mode,
            processor_kwargs=processor_kwargs or {},
            auth_state=auth_state,
            connector_states=connector_states,
            is_active=is_active,
            account_id=account_id,
            user_id=user_id,
            original_role=original_role,
        )
        task.next_execution_time = task.calculate_next_execution_time() if is_active else None
        if await self._owner_deleting(account_id, user_id):
            raise FailedPreconditionError("Identity deletion is in progress")
        created = False

        async def persist() -> None:
            nonlocal created
            await self._store.create_task(task, initial_run_id=initial_run_id)
            created = True

        try:
            try:
                async with self._locks.tree(account_id, to_uri, owner_lease_ref=lease_ref):
                    # Keep the target locked until the insert settles, including cancellation.
                    await run_to_completion(persist)
            except LockAcquisitionError as exc:
                raise ResourceBusyError("Watch target is being changed", uri=to_uri) from exc
            if await self._owner_deleting(account_id, user_id):
                raise FailedPreconditionError("Identity deletion is in progress")
        except BaseException:
            if created:
                try:
                    await run_to_completion(lambda: self._withdraw(task, initial_run_id))
                except BaseException:
                    logger.exception("[WatchManager] Failed to withdraw Watch %s", task.task_id)
            raise
        logger.info(
            "[WatchManager] Created task %s for path %s by user %s/%s",
            task.task_id,
            path,
            account_id,
            user_id,
        )
        return task

    async def _withdraw(self, task: WatchTask, initial_run_id: Optional[str]) -> None:
        await self._store.delete_task(
            task.task_id, WatchAuthority(task.account_id, task.user_id, "root")
        )
        if initial_run_id:
            await self._store.finish_run(
                task.task_id,
                initial_run_id,
                WatchRunResult(status="cancelled", error="Identity deletion is in progress"),
            )

    async def update_task(
        self,
        task_id: str,
        account_id: str,
        user_id: str,
        role: str,
        invalidate_source_checkpoint: bool = False,
        credential_state: Optional[Dict[str, Any]] = None,
        **values: Any,
    ) -> WatchTask:
        # Internal callers omit optional None values; auth/state None clears them.
        values = {
            key: value
            for key, value in values.items()
            if value is not None or key in {"auth_state", "connector_states"}
        }
        unknown = values.keys() - MUTABLE_FIELDS
        if unknown:
            raise ValueError(f"Unsupported Watch update fields: {sorted(unknown)}")
        authority = WatchAuthority(account_id, user_id, role)
        expected_auth = None
        if credential_state is not None:
            if "is_active" in values or "watch_interval" in values:
                raise ValueError("credential_state cannot be combined with scheduling changes")
            current = await self._store.get_task(task_id, authority)
            if current is None:
                raise ValueError(f"Task {task_id} not found")
            if not await self._credential_owner_matches(current, credential_state):
                return current
            expected_auth = current.auth_state
        interval = values.get("watch_interval")
        if interval is not None and interval <= 0:
            if values.get("is_active") is True:
                raise ValueError("watch_interval must be > 0 for active tasks")
            values.pop("watch_interval")
            values["is_active"] = False
        task = await self._store.patch_task(
            task_id,
            WatchPatch(
                values,
                invalidate_source_checkpoint=invalidate_source_checkpoint,
                credential_state=credential_state,
                expected_auth_state=expected_auth,
            ),
            authority,
        )
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        return task

    async def _credential_owner_matches(self, task: WatchTask, state: Dict[str, Any]) -> bool:
        from openviking.connector.auth import extract_credential_reference

        if not ConnectorDelegate.is_watch_auth_state(task.auth_state):
            return False
        delegate = ConnectorDelegate(viking_fs=self._viking_fs)
        _, _, _, auth_config = await delegate.restore_watch_request(
            task.auth_state, account_id=task.account_id, path=task.path
        )
        reference = extract_credential_reference(auth_config)
        return (
            reference is not None
            and task.account_id == state["ov_account_id"]
            and task.user_id == state["ov_user_id"]
            and all(
                reference.get(key, "default" if key == "ov_account_id" else None) == state[key]
                for key in (
                    "account_id",
                    "resource_id",
                    "user_id",
                    "ov_user_id",
                    "ov_account_id",
                    "type",
                    "platform",
                )
            )
        )

    async def get_task(
        self,
        task_id: str,
        account_id: str = "default",
        user_id: str = "default",
        role: str = "root",
    ) -> Optional[WatchTask]:
        return await self._store.get_task(task_id, WatchAuthority(account_id, user_id, role))

    async def get_all_tasks(
        self,
        account_id: str,
        user_id: str,
        role: str,
        active_only: bool = False,
    ) -> List[WatchTask]:
        return await self._store.list_tasks(
            WatchAuthority(account_id, user_id, role),
            active_only=active_only,
        )

    async def get_task_by_uri(
        self,
        to_uri: str,
        account_id: str,
        user_id: str,
        role: str,
    ) -> Optional[WatchTask]:
        tasks = await self._store.list_tasks(
            WatchAuthority(account_id, user_id, role),
            target_uri=to_uri,
            scope_account_id=account_id,
        )
        if len(tasks) > 1:
            raise ConflictError(
                f"Target URI '{to_uri}' has {len(tasks)} watch tasks "
                f"({', '.join(sorted(task.task_id for task in tasks))}); address one by task_id.",
                resource=to_uri,
            )
        return tasks[0] if tasks else None

    async def get_tasks_page(
        self,
        account_id: str,
        user_id: str,
        role: str,
        *,
        active_only: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> WatchListPage:
        return await self._store.list_tasks_page(
            WatchAuthority(account_id, user_id, role),
            active_only=active_only,
            limit=limit,
            cursor=cursor,
        )

    async def delete_task(self, task_id: str, account_id: str, user_id: str, role: str) -> bool:
        return await self._store.delete_task(task_id, WatchAuthority(account_id, user_id, role))

    async def update_auth_state(
        self,
        task_id: str,
        auth_state: Optional[Dict[str, Any]],
        *,
        run_id: Optional[str] = None,
    ) -> None:
        await self._store.patch_task(task_id, WatchPatch({"auth_state": auth_state}), run_id=run_id)

    async def update_connector_states(self, task_id: str, connector_states: Dict[str, Any]) -> None:
        await self._store.patch_task(task_id, WatchPatch({"connector_states": connector_states}))

    async def finish_run(
        self,
        task_id: str,
        run_id: Optional[str],
        result: WatchRunResult,
        *,
        started_at: Optional[float] = None,
    ) -> bool:
        if result.error:
            result = replace(result, error=self._public_error(result.error))
        return await self._store.finish_run(task_id, run_id, result, started_at=started_at)

    async def deactivate_idle(self, task_id: str, *, target_uri: Optional[str], error: str) -> bool:
        return await self._store.deactivate_idle(
            task_id, target_uri=target_uri, error=self._public_error(error)
        )

    @staticmethod
    def _public_error(error: str) -> str:
        return sanitize_public_http_error(code="WATCH_EXECUTION_FAILED", message=error).message

    async def has_running_overlap(self, account_id: str, uris: Sequence[str]) -> bool:
        return await self._store.has_running_overlap(account_id, uris)

    async def move(
        self,
        *,
        account_id: str,
        from_uri: str,
        to_uri: str,
        task_ids: Optional[Sequence[str]] = None,
    ) -> WatchMoveResult:
        return await self._store.move(
            account_id=account_id,
            from_uri=from_uri,
            to_uri=to_uri,
            task_ids=task_ids,
        )

    async def deactivate_tasks_under_uri_internal(
        self,
        uri: str,
        account_id: str = "default",
    ) -> List[WatchTask]:
        return await self._store.deactivate_under_uri(account_id, uri)

    @property
    def tracker(self) -> Any:
        return self._tracker if self._tracker is not None else get_task_tracker()

    @staticmethod
    def context(task: WatchTask) -> RequestContext:
        try:
            role = Role(task.original_role)
        except ValueError:
            role = Role.USER
        return RequestContext(
            user=UserIdentifier(task.account_id, task.user_id),
            role=role,
            bypass_acl=True,
        )

    @staticmethod
    def run(state: WatchState) -> WatchRun:
        return WatchRun(
            state.task.task_id,
            state.current_run_id,
            state.task.model_copy(deep=True),
            started_at=state.run_started_at,
        )

    async def trigger(self, task_id: str, *, manual: bool = True) -> Optional[WatchReceipt]:
        """Submit once through ResourceService and return its actual Task ID."""
        if self._resource_service is None or self._viking_fs is None:
            raise RuntimeError("Watch resource submission dependencies are not initialized")

        async def submit() -> Optional[WatchReceipt]:
            async with self._trigger_locks.acquire(task_id):
                return await self._trigger(task_id, manual=manual)

        return await run_to_completion(submit)

    async def _trigger(self, task_id: str, *, manual: bool) -> Optional[WatchReceipt]:
        task = await self.store.get_candidate(task_id)
        if task is None:
            return None
        if task.run_started_at is not None:
            state = await self.store.get_state(task_id)
            if state is None or state.deleted:
                return None
            if state.run_started_at is not None:
                return await self._reuse(state, retry_terminal=True, manual=manual)
        run = None
        lock_error = None
        try:
            async with self.locks.tree(
                task.account_id, task.to_uri, timeout=5.0 if manual else 0.0
            ):
                if await self._owner_deleting(task.account_id, task.user_id):
                    raise FailedPreconditionError("Identity deletion is in progress")
                run = await self.store.begin_run(task, manual=manual)
        except LockAcquisitionError as exc:
            lock_error = exc
        if run is None:
            # Another caller may have attached its Task since the entry read.
            latest = await self.store.get_state(task_id)
            receipt = (
                await self._reuse(latest, retry_terminal=True, manual=manual)
                if latest and not latest.deleted
                else None
            )
            if receipt is None and manual and lock_error is not None:
                raise ResourceBusyError(
                    "Watch target is being changed; retry the trigger",
                    uri=task.to_uri,
                ) from lock_error
            return receipt
        return await self._execute(run)

    async def due(self, limit: int) -> list[WatchRun]:
        """Return already occupied runs; all execution parameters are fixed here."""
        runs: list[WatchRun] = []
        offset = 0
        try:
            while len(runs) < limit:
                size = limit - len(runs)
                candidates = await self.store.due_candidates(size, offset=offset)
                for task in candidates:
                    try:
                        async with self.locks.tree(task.account_id, task.to_uri, timeout=0.0):
                            if await self._owner_deleting(task.account_id, task.user_id):
                                offset += 1
                                continue

                            async def claim(candidate: WatchCandidate = task) -> None:
                                run = await self.store.begin_run(candidate)
                                if run is not None:
                                    runs.append(run)

                            # Keep the target locked until a reservation commits.
                            # Record its result before propagating cancellation.
                            await run_to_completion(claim)
                    except LockAcquisitionError:
                        offset += 1
                if len(candidates) < size:
                    break
            return runs
        except BaseException:

            async def release() -> None:
                for run in runs:
                    await self.finish_run(
                        run.task_id,
                        None,
                        WatchRunResult(status="error", error="Watch dispatch interrupted"),
                        started_at=run.started_at,
                    )

            await run_to_completion(release)
            raise

    async def execute(self, run: WatchRun) -> Optional[WatchReceipt]:
        """Submit a reserved snapshot without re-reading the Watch."""
        return await run_to_completion(lambda: self._execute(run))

    async def _execute(self, run: WatchRun) -> Optional[WatchReceipt]:
        task, task_id = run.task, run.task_id
        # Release the data target lock before preparation or an external
        # Connector request, whose callback may import into this target.
        actual_run = run

        async def on_task_created(record: Any) -> None:
            nonlocal actual_run
            actual_run = replace(run, run_id=record.task_id)
            if not await self.store.attach_task(task_id, record.task_id, started_at=run.started_at):
                raise FailedPreconditionError("Watch submission is no longer current")

        try:
            if task.to_uri and not ConnectorDelegate.is_watch_auth_state(task.auth_state):
                try:
                    await self._viking_fs.stat(task.to_uri, ctx=self.context(task), skip_count=True)
                except Exception as exc:
                    if not is_not_found_error(exc):
                        raise
                    await self.finish_run(
                        task_id,
                        None,
                        WatchRunResult(
                            status="failed",
                            error=f"Watched target URI does not exist: {task.to_uri}",
                            deactivate=True,
                        ),
                        started_at=run.started_at,
                    )
                    return None
            result = await self._submit_resource(run, on_task_created)
            if actual_run.run_id is None:
                if isinstance(result, WatchRunResult):
                    outcome = result
                elif (result or {}).get("status") in {
                    "completed",
                    "partial",
                    "failed",
                    "error",
                    "cancelled",
                }:
                    outcome = self.result(run.task, result["status"], result, None)
                else:
                    raise RuntimeError("Resource submission did not create a Task")
                await self.finish_run(task_id, None, outcome, started_at=run.started_at)
                return None
            record = await self.tracker.get(
                actual_run.run_id, account_id=task.account_id, user_id=task.user_id
            )
            await self.settle_record(actual_run, record)
            return WatchReceipt(task_id, actual_run.run_id, False)
        except Exception as exc:
            if actual_run.run_id is not None:
                owner = {"account_id": task.account_id, "user_id": task.user_id}
                try:
                    record = await self.tracker.get(actual_run.run_id, **owner)
                    if record is not None and not record.is_terminal():
                        await self.tracker.fail(actual_run.run_id, str(exc), **owner)
                        record = await self.tracker.get(actual_run.run_id, **owner)
                    await self.settle_record(actual_run, record)
                except Exception:
                    # The fallback below still releases the Watch row; a
                    # tracker failure must not leave the run attached.
                    logger.exception(
                        "Failed to settle watch run %s after a submission error",
                        actual_run.run_id,
                    )
            await self.finish_run(
                task_id,
                actual_run.run_id,
                WatchRunResult(
                    status="failed",
                    error=str(exc),
                    deactivate=isinstance(exc, FileNotFoundError)
                    or getattr(exc, "code", None) == GIT_AUTH_FAILED,
                ),
                started_at=run.started_at,
            )
            raise

    async def _reuse(
        self, state: WatchState, *, retry_terminal: bool = False, manual: bool = True
    ) -> Optional[WatchReceipt]:
        if not state.current_run_id:
            return None
        run = self.run(state)
        record = await self.tracker.get(
            run.run_id, account_id=run.task.account_id, user_id=run.task.user_id
        )
        if record is None:
            return None
        if record.is_terminal():
            await self.settle_running(self.observation(state), record)
            if retry_terminal:
                return await self._trigger(run.task_id, manual=manual)
        return WatchReceipt(run.task_id, run.run_id, True)

    async def _submit_resource(self, run: WatchRun, on_task_created: Any) -> Any:
        # Keep parser-backed Feishu dependencies out of WatchManager's import boundary.
        from openviking.resource.feishu_watch_auth import (
            FeishuOAuthClient,
            FeishuTokenRefreshError,
            apply_feishu_refreshed_token,
            feishu_auth_state_needs_refresh,
            is_feishu_auth_state,
        )

        task, auth = run.task, run.task.auth_state
        connector = ConnectorDelegate.is_watch_auth_state(auth)
        ctx = self.context(task)
        if (
            not connector
            and not task.path.startswith(("http://", "https://", "git@", "ssh://", "git://"))
            and not Path(task.path).exists()
        ):
            return WatchRunResult(
                status="failed",
                error=f"Resource path does not exist: {task.path}",
                deactivate=True,
            )
        kwargs = dict(task.processor_kwargs or {})
        kwargs.pop("build_index", None)
        kwargs.pop("summarize", None)
        if is_external_feishu_auth(auth):
            ctx.api_key, kwargs["args"] = await restore_feishu_request(
                self._resource_service._connector, auth, path=task.path, ctx=ctx
            )
        elif is_feishu_auth_state(auth):
            try:
                if feishu_auth_state_needs_refresh(auth):
                    from openviking.config.feishu import get_effective_feishu_config

                    if self._runtime_config_manager is None:
                        raise RuntimeError("Runtime config manager is not initialized")
                    config = await get_effective_feishu_config(
                        self._runtime_config_manager, task.account_id
                    )
                    refreshed = await FeishuOAuthClient.from_auth_state(
                        auth, config=config
                    ).refresh_user_access_token(auth.get("refresh_token"))
                    auth = apply_feishu_refreshed_token(auth, refreshed)
                    await self.update_auth_state(task.task_id, auth, run_id=run.run_id)
                kwargs["feishu_access_token"] = auth["access_token"]
            except FeishuTokenRefreshError as exc:
                if not exc.permanent:
                    raise
                return WatchRunResult(status="failed", error=str(exc), deactivate=True)
        elif is_git_http_auth_state(auth):
            kwargs["auth_config"] = git_http_auth_config_from_state(auth, task.path)
        elif connector:
            (
                ctx.api_key,
                kwargs["add_type"],
                kwargs["args"],
                connector_auth,
            ) = await self._resource_service._connector.restore_watch_request(
                auth, account_id=task.account_id, path=task.path
            )
            if connector_auth is not None:
                kwargs["connector_auth_config"] = connector_auth
            kwargs["connector_states"] = task.connector_states
        if not connector:
            checkpoint = await self._probe_git_checkpoint(task, kwargs, ctx)
            if checkpoint is not None:
                kwargs["git_head_sha_hint"] = checkpoint["head_sha"]
                if checkpoint == task.source_checkpoint:
                    return WatchRunResult(status="completed", execution_task_id=task.last_task_id)
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
            on_task_created=on_task_created,
            **kwargs,
        )

    async def _probe_git_checkpoint(
        self, task: WatchTask, kwargs: Dict[str, Any], ctx: RequestContext
    ) -> Optional[Dict[str, Any]]:
        from openviking.parse.accessors.git_accessor import GitAccessor
        from openviking.utils.code_hosting_utils import is_git_repo_url

        if not is_git_repo_url(task.path):
            return None
        try:
            probe_kwargs = dict(kwargs)
            probe_kwargs["request_validator"] = ensure_public_remote_target
            if probe_kwargs.get("auth_config") is None:
                processor = getattr(self._resource_service, "_resource_processor", None)
                if processor is not None:
                    token = await processor.github_token_for(task.path, ctx)
                    if token:
                        probe_kwargs["auth_config"] = {"token": token}
            probed = await asyncio.wait_for(
                GitAccessor().probe_git_remote_head(task.path, **probe_kwargs),
                timeout=self.DEFAULT_GIT_PROBE_TIMEOUT,
            )
        except Exception as exc:
            logger.warning(
                "[WatchManager] Git probe failed for %s: %s; falling back to refresh",
                task.path,
                self._public_error(str(exc)),
            )
            return None
        if probed is None:
            return None
        return {"kind": "git", "head_sha": probed[1], "instruction": task.instruction}

    @staticmethod
    def result(
        task: WatchTask,
        status: str,
        result: Optional[dict[str, Any]],
        error: Optional[str],
    ) -> WatchRunResult:
        return WatchManager._result(
            status,
            result,
            error,
            connector=ConnectorDelegate.is_watch_auth_state(task.auth_state),
            instruction=task.instruction,
        )

    @staticmethod
    def _result(
        status: str,
        result: Optional[dict[str, Any]],
        error: Optional[str],
        *,
        connector: bool,
        instruction: str,
    ) -> WatchRunResult:
        result = result or {}
        if status == "completed" and result.get("import_complete") is False:
            status = "partial"
        states = result.get("connector_states")
        has_states = connector and status == "completed" and "connector_states" in result
        if has_states and not isinstance(states, dict):
            status, error, has_states = "failed", "connector_states must be an object", False
        if status in {"failed", "error"} and not error:
            errors = result.get("errors")
            error = "; ".join(str(item) for item in errors) if errors else "watch ingestion failed"
        # A successful source import may later fail in semantic/embedding Work.
        # Its explicit checkpoint still describes the revision actually imported.
        checkpoint = (
            result.get("source_checkpoint")
            if status != "cancelled" and result.get("import_complete") is not False
            else None
        )
        if status == "completed":
            from openviking.service.resource_service import ResourceService

            checkpoint = checkpoint or ResourceService._initial_source_checkpoint(
                result.get("meta"), instruction=instruction
            )
        return WatchRunResult(
            status=status,
            execution_task_id=result.get("task_id"),
            error=error,
            deactivate=result.get("code") == GIT_AUTH_FAILED,
            connector_states=states if has_states else None,
            has_connector_states=has_states,
            source_checkpoint=checkpoint,
        )

    async def settle(self, task_id: str, run_id: str) -> bool:
        """Settle callbacks that have IDs but no Watch run snapshot."""
        state = await self.store.get_state(task_id)
        if state is None or state.current_run_id != run_id:
            return False
        run = self.run(state)
        record = await self.tracker.get(
            run_id, account_id=run.task.account_id, user_id=run.task.user_id
        )
        return await self.settle_running(self.observation(state), record)

    @staticmethod
    def observation(state: WatchState) -> WatchRunning:
        assert state.run_started_at is not None
        return WatchRunning(
            task_id=state.task.task_id,
            account_id=state.task.account_id,
            user_id=state.task.user_id,
            current_run_id=state.current_run_id,
            run_started_at=state.run_started_at,
            now=state.now,
            connector=ConnectorDelegate.is_watch_auth_state(state.task.auth_state),
            instruction=state.task.instruction,
        )

    async def settle_record(self, run: WatchRun, record: Any) -> bool:
        """Use existing snapshots; the Store condition guards against stale runs."""
        if record is None or record.task_id != run.run_id or not record.is_terminal():
            return False
        result = {**(record.result or {}), "task_id": record.task_id}
        return await run_to_completion(
            lambda: self.finish_run(
                run.task_id,
                run.run_id,
                self.result(run.task, record.status.value, result, record.error),
                started_at=run.started_at,
            )
        )

    async def settle_running(self, state: WatchRunning, record: Any) -> bool:
        """Settle a scanned run without loading or decrypting the complete Watch."""
        if record is None or record.task_id != state.current_run_id or not record.is_terminal():
            return False
        result = {**(record.result or {}), "task_id": record.task_id}
        return await run_to_completion(
            lambda: self.finish_run(
                state.task_id,
                state.current_run_id,
                self._result(
                    record.status.value,
                    result,
                    record.error,
                    connector=state.connector,
                    instruction=state.instruction,
                ),
                started_at=state.run_started_at,
            )
        )

    async def delete_tasks(self, account_id: str, user_id: Optional[str] = None) -> None:
        """Identity cleanup uses durable rows, independently of any local Runtime."""
        states = await self.store.delete_tasks(account_id, user_id)
        for state in states:
            run = self.run(state)
            owner = {"account_id": run.task.account_id, "user_id": run.task.user_id}
            record = await self.tracker.get(run.run_id, **owner) if run.run_id else None
            if record is not None and not record.is_terminal():
                try:
                    await self.tracker.cancel(run.run_id, **owner)
                except ValueError:
                    await self.tracker.fail(run.run_id, "Watch deleted", **owner)
                record = await self.tracker.get(run.run_id, **owner)
            if record is None:
                await self.finish_run(
                    run.task_id,
                    run.run_id,
                    WatchRunResult(status="cancelled", execution_task_id=run.run_id),
                    started_at=run.started_at,
                )
            else:
                await self.settle_running(self.observation(state), record)
