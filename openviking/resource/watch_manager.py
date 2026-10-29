# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch task management over the Watch store.

Permission and runtime conditions are evaluated by the store. Target stability comes
from PathLock: a Watch row is created, and a run starts occupying it, only
while its target's Tree lock is held; transfers rewrite rows inside their own
lock and check occupancy there.
"""

import inspect
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence

from openviking.observability.http_error_context import sanitize_public_http_error
from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode
from openviking.resource.watch_store import WatchMoveResult, WatchStore
from openviking.resource.watch_types import (
    MUTABLE_FIELDS,
    PermissionDeniedError,
    WatchAuthority,
    WatchPatch,
    WatchRunResult,
    WatchTask,
)
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking_cli.exceptions import ConflictError, FailedPreconditionError
from openviking_cli.utils import VikingURI
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

    async def _existing_path(self, account_id: str, uri: str) -> str:
        """Deepest existing ancestor-or-self of *uri*, whose Tree lock covers *uri*.

        Locking a missing path materializes it on some PathLock backends.
        """
        ctx = self._context(account_id)
        while not await self._fs.exists(uri, ctx=ctx):
            parent = VikingURI(uri).parent
            if parent is None:
                break
            uri = parent.uri
        return self._fs._uri_to_path(uri, ctx=ctx)

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
            await self._existing_path(account_id, uri),
            timeout_secs=timeout,
            owner_lease_ref=owner_lease_ref,
        )
        try:
            yield lease
        finally:
            await run_to_completion(lambda: agfs.pathlock_release(lease))


class WatchManager:
    def __init__(self, store: WatchStore, locks: WatchPathLocks) -> None:
        self._store = store
        self._locks = locks
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

    async def admits_run(self, task_id: str, run_id: str) -> bool:
        """Allow Task creation only while the run still owns a live, unfenced Watch."""
        state = await self._store.get_state(task_id)
        return (
            state is not None
            and state.current_run_id == run_id
            and not state.deleted
            and not state.run_fenced
        )

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
        interval = values.get("watch_interval")
        if interval is not None and interval <= 0:
            if values.get("is_active") is True:
                raise ValueError("watch_interval must be > 0 for active tasks")
            values.pop("watch_interval")
            values["is_active"] = False
        task = await self._store.patch_task(
            task_id,
            WatchPatch(values),
            WatchAuthority(account_id, user_id, role),
        )
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        return task

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

    async def finish_run(self, task_id: str, run_id: str, result: WatchRunResult) -> bool:
        if result.error:
            result = replace(result, error=self._public_error(result.error))
        return await self._store.finish_run(task_id, run_id, result)

    async def deactivate_idle(self, task_id: str, *, target_uri: Optional[str], error: str) -> bool:
        return await self._store.deactivate_idle(
            task_id, target_uri=target_uri, error=self._public_error(error)
        )

    @staticmethod
    def _public_error(error: str) -> str:
        return sanitize_public_http_error(code="WATCH_EXECUTION_FAILED", message=error).message

    async def fail_initial_submission(self, task_id: str, run_id: str, error: str) -> None:
        """Release a first run whose ingestion Task was never created."""
        await self.finish_run(task_id, run_id, WatchRunResult(status="failed", error=error))

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
