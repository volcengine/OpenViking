# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Store-compatible adapters for the Task Service HTTP client."""

from __future__ import annotations

from typing import Any, Collection, Dict, List, Optional, Sequence

from pydantic import ValidationError

from openviking.connector.watch_auth import WATCH_PLAINTEXT_AUTH_PROVIDER
from openviking.resource.watch_store import WatchListPage, WatchMoveResult, WatchStore
from openviking.resource.watch_types import (
    WatchAuthority,
    WatchCandidate,
    WatchPatch,
    WatchRun,
    WatchRunning,
    WatchRunResult,
    WatchState,
    WatchTask,
)
from openviking.service.task_domain import (
    DeleteExpiredResult,
    TaskPatch,
    TaskRecord,
    TaskRetentionPolicy,
    TaskStatus,
    WorkRecord,
    WorkRecoveryMode,
    WorkState,
    WorkSummary,
)
from openviking.service.task_store import (
    DeleteTasksResult,
    QueueWorkCounts,
    StoreOperationResult,
    TaskCreationResult,
    TaskStatsSnapshot,
    TaskWorkStore,
)
from openviking_cli.exceptions import InvalidArgumentError

from . import protocol as wire
from .http_client import HTTPTaskService


class HTTPTaskWorkStore(TaskWorkStore):
    def __init__(self, service: HTTPTaskService) -> None:
        self._service = service
        self._closed = False

    async def load(self) -> None:
        """Local Store lifecycle hook; remote calls work without loading."""
        self._ensure_open()
        await self._service.load()

    async def close(self) -> None:
        self._closed = True
        await self._service.close()

    def _ensure_open(self) -> None:
        if self._closed or self._service.is_closed:
            raise RuntimeError("Task Service store is closed")

    @property
    def work_recovery_mode(self) -> WorkRecoveryMode:
        return WorkRecoveryMode.DURABLE_STORE

    def snapshot_task_stats(self) -> TaskStatsSnapshot:
        # Matches relational stores: no authoritative process-local snapshot.
        return {}

    async def create(
        self, task: TaskRecord, *, reject_if_active: bool = False
    ) -> TaskCreationResult:
        self._ensure_open()
        request = wire.TasksCreateRequest(
            task=wire.TaskRecordDTO.from_record(task), reject_if_active=reject_if_active
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "create"], request)
        return TaskCreationResult(result.task.to_record(), result.created)

    async def withdraw_unaccepted(
        self, task_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksWithdrawUnacceptedRequest(
            task_id=task_id, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(
            wire.OPERATIONS["tasks", "withdraw_unaccepted"], request
        )
        return result

    async def get_task(
        self, task_id: str, *, account_id: Optional[str] = None, user_id: Optional[str] = None
    ) -> Optional[TaskRecord]:
        self._ensure_open()
        request = wire.TasksGetTaskRequest(task_id=task_id, account_id=account_id, user_id=user_id)
        result = await self._service.request(wire.OPERATIONS["tasks", "get_task"], request)
        return result.to_record() if result is not None else None

    async def get_durable_task(
        self, task_id: str, *, account_id: str, user_id: str
    ) -> Optional[TaskRecord]:
        self._ensure_open()
        request = wire.TasksGetDurableTaskRequest(
            task_id=task_id, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "get_durable_task"], request)
        return result.to_record() if result is not None else None

    async def get_task_with_events(
        self, task_id: str, *, account_id: Optional[str] = None, user_id: Optional[str] = None
    ) -> Optional[TaskRecord]:
        self._ensure_open()
        request = wire.TasksGetTaskWithEventsRequest(
            task_id=task_id, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(
            wire.OPERATIONS["tasks", "get_task_with_events"], request
        )
        return result.to_record() if result is not None else None

    async def list(
        self,
        account_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: Optional[int] = None,
        before: tuple[float, str] | None = None,
        include_internal: bool = True,
        q: Optional[str] = None,
    ) -> List[TaskRecord]:
        self._ensure_open()
        request = wire.TasksListRequest(
            account_id=account_id,
            user_id=user_id,
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
            before=before,
            include_internal=include_internal,
            q=q,
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "list"], request)
        return [task.to_record() for task in result]

    async def list_durable(
        self, account_id: str, *, task_type: Optional[str] = None
    ) -> List[TaskRecord]:
        self._ensure_open()
        request = wire.TasksListDurableRequest(account_id=account_id, task_type=task_type)
        result = await self._service.request(wire.OPERATIONS["tasks", "list_durable"], request)
        return [task.to_record() for task in result]

    async def delete_tasks(
        self,
        task_id: Optional[str] = None,
        *,
        account_id: str,
        user_id: Optional[str] = None,
        allow_active_types: Collection[str] = (),
    ) -> DeleteTasksResult:
        self._ensure_open()
        request = wire.TasksDeleteTasksRequest(
            task_id=task_id,
            account_id=account_id,
            user_id=user_id,
            allow_active_types=allow_active_types,
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "delete_tasks"], request)
        return DeleteTasksResult(
            result=result.result, deleted=result.deleted, blockers=result.blockers
        )

    async def delete_expired(
        self, policy: TaskRetentionPolicy, *, now: float, limit: int
    ) -> DeleteExpiredResult:
        self._ensure_open()
        request = wire.TasksDeleteExpiredRequest(policy=policy, now=now, limit=limit)
        result = await self._service.request(wire.OPERATIONS["tasks", "delete_expired"], request)
        return result

    async def start_task(
        self,
        task_id: str,
        *,
        stage: Optional[str],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksStartTaskRequest(
            task_id=task_id, stage=stage, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "start_task"], request)
        return result

    async def patch_task(
        self, task_id: str, patch: TaskPatch, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksPatchTaskRequest(
            task_id=task_id, patch=patch, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "patch_task"], request)
        return result

    async def record_event(
        self, task_id: str, kind: str, *, operation: Optional[str], account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksRecordEventRequest(
            task_id=task_id, kind=kind, operation=operation, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "record_event"], request)
        return result

    async def record_outcome(
        self,
        task_id: str,
        *,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
        resource_id: Optional[str],
        cancelled: bool,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksRecordOutcomeRequest(
            task_id=task_id,
            result=result,
            error=error,
            resource_id=resource_id,
            cancelled=cancelled,
            account_id=account_id,
            user_id=user_id,
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "record_outcome"], request)
        return result

    async def request_cancellation(
        self,
        task_id: str,
        *,
        cancellable_types: Collection[str],
        allow_terminal: bool,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksRequestCancellationRequest(
            task_id=task_id,
            cancellable_types=cancellable_types,
            allow_terminal=allow_terminal,
            account_id=account_id,
            user_id=user_id,
        )
        result = await self._service.request(
            wire.OPERATIONS["tasks", "request_cancellation"], request
        )
        return result

    async def add_work(
        self, work: WorkRecord, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksAddWorkRequest(work=work, account_id=account_id, user_id=user_id)
        result = await self._service.request(wire.OPERATIONS["tasks", "add_work"], request)
        return result

    async def reconcile_delivery(
        self, work: WorkRecord, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksReconcileDeliveryRequest(
            work=work, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(
            wire.OPERATIONS["tasks", "reconcile_delivery"], request
        )
        return result

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksDiscardWorkRequest(
            task_id=task_id, work_id=work_id, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "discard_work"], request)
        return result

    async def get_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> Optional[WorkRecord]:
        self._ensure_open()
        request = wire.TasksGetWorkRequest(
            task_id=task_id, work_id=work_id, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "get_work"], request)
        return result

    async def get_works(
        self,
        task_id: str,
        work_ids: Collection[str],
        *,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Dict[str, WorkRecord]:
        self._ensure_open()
        request = wire.TasksGetWorksRequest(
            task_id=task_id, work_ids=work_ids, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "get_works"], request)
        return result

    async def start_work(
        self,
        task_id: str,
        work_id: str,
        *,
        account_id: str,
        user_id: str,
        queue_name: Optional[str] = None,
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksStartWorkRequest(
            task_id=task_id,
            work_id=work_id,
            account_id=account_id,
            user_id=user_id,
            queue_name=queue_name,
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "start_work"], request)
        return result

    async def finish_work(
        self,
        task_id: str,
        work_id: str,
        state: WorkState,
        *,
        error: Optional[str],
        errors: Optional[Collection[Any]],
        context_count_delta: int,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksFinishWorkRequest(
            task_id=task_id,
            work_id=work_id,
            state=state,
            error=error,
            errors=errors,
            context_count_delta=context_count_delta,
            account_id=account_id,
            user_id=user_id,
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "finish_work"], request)
        return result

    async def requeue_work(
        self, task_id: str, work_id: str, *, delta: int, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksRequeueWorkRequest(
            task_id=task_id, work_id=work_id, delta=delta, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "requeue_work"], request)
        return result

    async def record_work_error(
        self, task_id: str, work_id: str, error: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksRecordWorkErrorRequest(
            task_id=task_id, work_id=work_id, error=error, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "record_work_error"], request)
        return result

    async def settle_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> StoreOperationResult:
        self._ensure_open()
        request = wire.TasksSettleWorkRequest(
            task_id=task_id, work_id=work_id, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "settle_work"], request)
        return result

    async def has_open_work(
        self,
        task_id: Optional[str],
        *,
        exclude_work_id: Optional[str],
        account_id: Optional[str],
        user_id: Optional[str],
        exclude_inline: bool = False,
    ) -> bool:
        self._ensure_open()
        request = wire.TasksHasOpenWorkRequest(
            task_id=task_id,
            exclude_work_id=exclude_work_id,
            account_id=account_id,
            user_id=user_id,
            exclude_inline=exclude_inline,
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "has_open_work"], request)
        return result

    async def get_queue_work_counts(self, *, account_id: str) -> QueueWorkCounts:
        self._ensure_open()
        request = wire.TasksGetQueueWorkCountsRequest(account_id=account_id)
        result = await self._service.request(
            wire.OPERATIONS["tasks", "get_queue_work_counts"], request
        )
        return result

    async def summarize_works(
        self,
        task_id: str,
        *,
        work_ids: Optional[Collection[str]],
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> WorkSummary:
        self._ensure_open()
        request = wire.TasksSummarizeWorksRequest(
            task_id=task_id, work_ids=work_ids, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "summarize_works"], request)
        return result

    async def filter_cancellation_requested(
        self, task_ids: Collection[str], *, account_id: str, user_id: str
    ) -> set[str]:
        self._ensure_open()
        request = wire.TasksFilterCancellationRequestedRequest(
            task_ids=task_ids, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(
            wire.OPERATIONS["tasks", "filter_cancellation_requested"], request
        )
        return result

    async def get_task_status(
        self, task_id: str, *, account_id: str, user_id: str
    ) -> Optional[TaskStatus]:
        self._ensure_open()
        request = wire.TasksGetTaskStatusRequest(
            task_id=task_id, account_id=account_id, user_id=user_id
        )
        result = await self._service.request(wire.OPERATIONS["tasks", "get_task_status"], request)
        return result


class HTTPWatchStore(WatchStore):
    def __init__(self, service: HTTPTaskService) -> None:
        self._service = service
        self._closed = False

    async def initialize(self) -> None:
        """Local Store lifecycle hook; no server initialization is required."""
        self._ensure_open()
        await self._service.load()

    async def close(self) -> None:
        self._closed = True
        await self._service.close()

    def _ensure_open(self) -> None:
        if self._closed or self._service.is_closed:
            raise RuntimeError("Task Service store is closed")

    @property
    def provider(self) -> str:
        return "http"

    async def get_state(self, task_id: str) -> Optional[WatchState]:
        self._ensure_open()
        request = wire.WatchesGetStateRequest(task_id=task_id)
        result = await self._service.request(wire.OPERATIONS["watches", "get_state"], request)
        return result

    async def get_task(
        self, task_id: str, authority: WatchAuthority = WatchAuthority()
    ) -> Optional[WatchTask]:
        self._ensure_open()
        request = wire.WatchesGetTaskRequest(task_id=task_id, authority=authority)
        result = await self._service.request(wire.OPERATIONS["watches", "get_task"], request)
        return result

    async def list_tasks(
        self,
        authority: WatchAuthority,
        *,
        active_only: bool = False,
        scope_account_id: Optional[str] = None,
        target_uri: Optional[str] = None,
    ) -> list[WatchTask]:
        self._ensure_open()
        request = wire.WatchesListTasksRequest(
            authority=authority,
            active_only=active_only,
            scope_account_id=scope_account_id,
            target_uri=target_uri,
        )
        result = await self._service.request(wire.OPERATIONS["watches", "list_tasks"], request)
        return result

    async def list_tasks_page(
        self,
        authority: WatchAuthority,
        *,
        active_only: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> WatchListPage:
        self._ensure_open()
        try:
            request = wire.WatchesListTasksPageRequest(
                authority=authority, active_only=active_only, limit=limit, cursor=cursor
            )
        except ValidationError as exc:
            raise InvalidArgumentError("limit must be an integer between 1 and 200") from exc
        result = await self._service.request(wire.OPERATIONS["watches", "list_tasks_page"], request)
        return result

    async def create_task(self, task: WatchTask, *, initial_run_id: Optional[str] = None) -> None:
        self._ensure_open()
        request = wire.WatchesCreateTaskRequest(task=task, initial_run_id=initial_run_id)
        result = await self._service.request(wire.OPERATIONS["watches", "create_task"], request)
        return result

    async def patch_task(
        self,
        task_id: str,
        patch: WatchPatch,
        authority: WatchAuthority = WatchAuthority(),
        *,
        run_id: Optional[str] = None,
    ) -> Optional[WatchTask]:
        self._ensure_open()
        # Credential notifications compare the exact stored Connector envelope.
        # Re-encrypting a legacy plaintext snapshot would change that comparison.
        if (patch.expected_auth_state or {}).get("provider") == WATCH_PLAINTEXT_AUTH_PROVIDER:
            raise InvalidArgumentError(
                "Rebind legacy Connector Watch credentials before updating their state."
            )
        request = wire.WatchesPatchTaskRequest(
            task_id=task_id, patch=patch, authority=authority, run_id=run_id
        )
        result = await self._service.request(wire.OPERATIONS["watches", "patch_task"], request)
        return result

    async def delete_task(self, task_id: str, authority: WatchAuthority) -> bool:
        self._ensure_open()
        request = wire.WatchesDeleteTaskRequest(task_id=task_id, authority=authority)
        result = await self._service.request(wire.OPERATIONS["watches", "delete_task"], request)
        return result

    async def delete_tasks(
        self, account_id: str, user_id: Optional[str] = None
    ) -> list[WatchState]:
        self._ensure_open()
        request = wire.WatchesDeleteTasksRequest(account_id=account_id, user_id=user_id)
        result = await self._service.request(wire.OPERATIONS["watches", "delete_tasks"], request)
        return result

    async def get_candidate(self, task_id: str) -> Optional[WatchCandidate]:
        self._ensure_open()
        request = wire.WatchesGetCandidateRequest(task_id=task_id)
        result = await self._service.request(wire.OPERATIONS["watches", "get_candidate"], request)
        return result

    async def due_candidates(self, limit: int, *, offset: int = 0) -> list[WatchCandidate]:
        self._ensure_open()
        request = wire.WatchesDueCandidatesRequest(limit=limit, offset=offset)
        result = await self._service.request(wire.OPERATIONS["watches", "due_candidates"], request)
        return result

    async def running(self, *, after: str = "", limit: int = 100) -> list[WatchRunning]:
        self._ensure_open()
        request = wire.WatchesRunningRequest(after=after, limit=limit)
        result = await self._service.request(wire.OPERATIONS["watches", "running"], request)
        return result

    async def begin_run(
        self, candidate: WatchCandidate, *, manual: bool = False
    ) -> Optional[WatchRun]:
        self._ensure_open()
        request = wire.WatchesBeginRunRequest(candidate=candidate, manual=manual)
        result = await self._service.request(wire.OPERATIONS["watches", "begin_run"], request)
        return result

    async def attach_task(self, task_id: str, run_id: str, *, started_at: float) -> bool:
        self._ensure_open()
        request = wire.WatchesAttachTaskRequest(
            task_id=task_id, run_id=run_id, started_at=started_at
        )
        result = await self._service.request(wire.OPERATIONS["watches", "attach_task"], request)
        return result

    async def finish_run(
        self,
        task_id: str,
        run_id: Optional[str],
        result: WatchRunResult,
        *,
        started_at: Optional[float] = None,
    ) -> bool:
        self._ensure_open()
        request = wire.WatchesFinishRunRequest(
            task_id=task_id, run_id=run_id, result=result, started_at=started_at
        )
        result = await self._service.request(wire.OPERATIONS["watches", "finish_run"], request)
        return result

    async def deactivate_idle(self, task_id: str, *, target_uri: Optional[str], error: str) -> bool:
        self._ensure_open()
        request = wire.WatchesDeactivateIdleRequest(
            task_id=task_id, target_uri=target_uri, error=error
        )
        result = await self._service.request(wire.OPERATIONS["watches", "deactivate_idle"], request)
        return result

    async def has_running_overlap(self, account_id: str, uris: Sequence[str]) -> bool:
        self._ensure_open()
        request = wire.WatchesHasRunningOverlapRequest(account_id=account_id, uris=uris)
        result = await self._service.request(
            wire.OPERATIONS["watches", "has_running_overlap"], request
        )
        return result

    async def move(
        self,
        *,
        account_id: str,
        from_uri: str,
        to_uri: str,
        task_ids: Optional[Sequence[str]] = None,
    ) -> WatchMoveResult:
        self._ensure_open()
        request = wire.WatchesMoveRequest(
            account_id=account_id, from_uri=from_uri, to_uri=to_uri, task_ids=task_ids
        )
        result = await self._service.request(wire.OPERATIONS["watches", "move"], request)
        return result

    async def deactivate_under_uri(self, account_id: str, uri: str) -> list[WatchTask]:
        self._ensure_open()
        request = wire.WatchesDeactivateUnderUriRequest(account_id=account_id, uri=uri)
        result = await self._service.request(
            wire.OPERATIONS["watches", "deactivate_under_uri"], request
        )
        return result
