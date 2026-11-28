# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task Service v1: explicit operation schemas, independent of SQL and HTTP."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from openviking.resource.watch_store import WatchListPage, WatchMoveResult
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
    DeletedTask,
    DeleteExpiredResult,
    TaskPatch,
    TaskRecord,
    TaskRetentionPolicy,
    TaskStatus,
    WorkRecord,
    WorkState,
    WorkSummary,
)
from openviking.service.task_store import (
    QueueWorkCounts,
    StoreOperationResult,
    TaskDeletionBlocker,
)
from openviking.storage.internal_http import Route, WireModel

PROTOCOL_VERSION = "1"
BASE_PATH = "/api/v1/inner/tasks"


class TaskRecordDTO(WireModel):
    # All persisted fields are required on the wire, even when nullable.
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: float
    updated_at: float
    resource_id: Optional[str]
    account_id: Optional[str]
    user_id: Optional[str]
    meta: Dict[str, Any]
    stage: Optional[str]
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    auth: Dict[str, Any]
    execution_events: Optional[Any]
    processing_seconds: Optional[float]

    @classmethod
    def from_record(cls, task: TaskRecord) -> TaskRecordDTO:
        return cls(**{name: getattr(task, name) for name in cls.model_fields})

    def to_record(self) -> TaskRecord:
        return TaskRecord(**{name: getattr(self, name) for name in type(self).model_fields})


class TaskCreationDTO(WireModel):
    task: TaskRecordDTO
    created: bool


class DeleteTasksResponse(WireModel):
    # The peer must acknowledge the operation and identify which tasks it removed.
    result: StoreOperationResult
    deleted: tuple[DeletedTask, ...]
    blockers: tuple[TaskDeletionBlocker, ...] = ()


class TasksCreateRequest(WireModel):
    task: TaskRecordDTO
    reject_if_active: bool = False


class TasksWithdrawUnacceptedRequest(WireModel):
    task_id: str
    account_id: str
    user_id: str


class TasksGetTaskRequest(WireModel):
    task_id: str
    account_id: Optional[str] = None
    user_id: Optional[str] = None


class TasksGetDurableTaskRequest(WireModel):
    task_id: str
    account_id: str
    user_id: str


class TasksGetTaskWithEventsRequest(WireModel):
    task_id: str
    account_id: Optional[str] = None
    user_id: Optional[str] = None


class TasksListRequest(WireModel):
    account_id: Optional[str] = None
    user_id: Optional[str] = None
    task_type: Optional[str] = None
    status: Optional[str] = None
    resource_id: Optional[str] = None
    limit: Optional[int] = None
    before: tuple[float, str] | None = None
    include_internal: bool = True
    q: Optional[str] = None


class TasksListDurableRequest(WireModel):
    account_id: str
    task_type: Optional[str] = None


class TasksDeleteTasksRequest(WireModel):
    task_id: Optional[str] = None
    account_id: str
    user_id: Optional[str] = None
    allow_active_types: list[str] = []


class TasksDeleteExpiredRequest(WireModel):
    policy: TaskRetentionPolicy
    now: float
    limit: int


class TasksStartTaskRequest(WireModel):
    task_id: str
    stage: Optional[str]
    account_id: Optional[str]
    user_id: Optional[str]


class TasksPatchTaskRequest(WireModel):
    task_id: str
    patch: TaskPatch
    account_id: str
    user_id: str


class TasksRecordEventRequest(WireModel):
    task_id: str
    kind: str
    operation: Optional[str]
    account_id: str
    user_id: str


class TasksRecordOutcomeRequest(WireModel):
    task_id: str
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    resource_id: Optional[str]
    cancelled: bool
    account_id: Optional[str]
    user_id: Optional[str]


class TasksRequestCancellationRequest(WireModel):
    task_id: str
    cancellable_types: list[str]
    allow_terminal: bool
    account_id: str
    user_id: str


class TasksAddWorkRequest(WireModel):
    work: WorkRecord
    account_id: str
    user_id: str


class TasksReconcileDeliveryRequest(WireModel):
    work: WorkRecord
    account_id: str
    user_id: str


class TasksDiscardWorkRequest(WireModel):
    task_id: str
    work_id: str
    account_id: str
    user_id: str


class TasksGetWorkRequest(WireModel):
    task_id: str
    work_id: str
    account_id: str
    user_id: str


class TasksGetWorksRequest(WireModel):
    task_id: str
    work_ids: list[str]
    account_id: Optional[str]
    user_id: Optional[str]


class TasksStartWorkRequest(WireModel):
    task_id: str
    work_id: str
    account_id: str
    user_id: str
    queue_name: Optional[str] = None


class TasksFinishWorkRequest(WireModel):
    task_id: str
    work_id: str
    state: WorkState
    error: Optional[str]
    errors: Optional[list[Any]]
    context_count_delta: int
    account_id: str
    user_id: str


class TasksRequeueWorkRequest(WireModel):
    task_id: str
    work_id: str
    delta: int
    account_id: str
    user_id: str


class TasksRecordWorkErrorRequest(WireModel):
    task_id: str
    work_id: str
    error: str
    account_id: str
    user_id: str


class TasksSettleWorkRequest(WireModel):
    task_id: str
    work_id: str
    account_id: str
    user_id: str


class TasksHasOpenWorkRequest(WireModel):
    task_id: Optional[str]
    exclude_work_id: Optional[str]
    account_id: Optional[str]
    user_id: Optional[str]
    exclude_inline: bool = False


class TasksGetQueueWorkCountsRequest(WireModel):
    account_id: str


class TasksSummarizeWorksRequest(WireModel):
    task_id: str
    work_ids: Optional[list[str]]
    account_id: Optional[str]
    user_id: Optional[str]


class TasksFilterCancellationRequestedRequest(WireModel):
    task_ids: list[str]
    account_id: str
    user_id: str


class TasksGetTaskStatusRequest(WireModel):
    task_id: str
    account_id: str
    user_id: str


class WatchesGetStateRequest(WireModel):
    task_id: str


class WatchesGetTaskRequest(WireModel):
    task_id: str
    authority: WatchAuthority = WatchAuthority()


class WatchesListTasksRequest(WireModel):
    authority: WatchAuthority
    active_only: bool = False
    scope_account_id: Optional[str] = None
    target_uri: Optional[str] = None


class WatchesListTasksPageRequest(WireModel):
    authority: WatchAuthority
    active_only: bool = False
    limit: int = Field(default=100, ge=1, le=200, strict=True)
    cursor: Optional[str] = None


class WatchesCreateTaskRequest(WireModel):
    task: WatchTask
    initial_run_id: Optional[str] = None


class WatchesPatchTaskRequest(WireModel):
    task_id: str
    patch: WatchPatch
    authority: WatchAuthority = WatchAuthority()
    run_id: Optional[str] = None


class WatchesDeleteTaskRequest(WireModel):
    task_id: str
    authority: WatchAuthority


class WatchesDeleteTasksRequest(WireModel):
    account_id: str
    user_id: Optional[str] = None


class WatchesGetCandidateRequest(WireModel):
    task_id: str


class WatchesDueCandidatesRequest(WireModel):
    limit: int
    offset: int = 0


class WatchesRunningRequest(WireModel):
    after: str = ""
    limit: int = 100


class WatchesBeginRunRequest(WireModel):
    candidate: WatchCandidate
    manual: bool = False


class WatchesAttachTaskRequest(WireModel):
    task_id: str
    run_id: str
    started_at: float


class WatchesFinishRunRequest(WireModel):
    task_id: str
    run_id: Optional[str]
    result: WatchRunResult
    started_at: Optional[float] = None


class WatchesDeactivateIdleRequest(WireModel):
    task_id: str
    target_uri: Optional[str]
    error: str


class WatchesHasRunningOverlapRequest(WireModel):
    account_id: str
    uris: list[str]


class WatchesMoveRequest(WireModel):
    account_id: str
    from_uri: str
    to_uri: str
    task_ids: Optional[list[str]] = None


class WatchesDeactivateUnderUriRequest(WireModel):
    account_id: str
    uri: str


def _auth_secrets(prefix: str) -> tuple[str, ...]:
    # New Connector and ExternalTask credentials already have their own envelope.
    return tuple(
        f"{prefix}{name}"
        for name in (
            "token",
            "access_token",
            "refresh_token",
            "app_secret",
            "request",
            "credentials.request",
        )
    )


def _task_secrets(prefix: str = "") -> tuple[str, ...]:
    return (
        *_auth_secrets(f"{prefix}auth."),
        f"{prefix}meta.request.submit.stream_states",
        f"{prefix}auth.external_terminal_snapshot.result.connector_states",
        f"{prefix}result.connector_states",
    )


def _watch_secrets(prefix: str = "") -> tuple[str, ...]:
    return (*_auth_secrets(f"{prefix}auth_state."), f"{prefix}connector_states")


OPERATIONS: dict[tuple[str, str], Route] = {
    ("tasks", "create"): Route(
        f"{BASE_PATH}/create",
        TasksCreateRequest,
        TaskCreationDTO,
        mutating=True,
        request_secrets=_task_secrets("task."),
        response_secrets=_task_secrets("task."),
    ),
    ("tasks", "withdraw_unaccepted"): Route(
        f"{BASE_PATH}/withdraw-unaccepted",
        TasksWithdrawUnacceptedRequest,
        StoreOperationResult,
        mutating=True,
    ),
    ("tasks", "get_task"): Route(
        f"{BASE_PATH}/get",
        TasksGetTaskRequest,
        Optional[TaskRecordDTO],
        response_secrets=_task_secrets(),
    ),
    ("tasks", "get_durable_task"): Route(
        f"{BASE_PATH}/get-durable",
        TasksGetDurableTaskRequest,
        Optional[TaskRecordDTO],
        response_secrets=_task_secrets(),
    ),
    ("tasks", "get_task_with_events"): Route(
        f"{BASE_PATH}/get-with-events",
        TasksGetTaskWithEventsRequest,
        Optional[TaskRecordDTO],
        response_secrets=_task_secrets(),
    ),
    ("tasks", "list"): Route(
        f"{BASE_PATH}/list",
        TasksListRequest,
        List[TaskRecordDTO],
        response_secrets=_task_secrets("*."),
    ),
    ("tasks", "list_durable"): Route(
        f"{BASE_PATH}/list-durable",
        TasksListDurableRequest,
        List[TaskRecordDTO],
        response_secrets=_task_secrets("*."),
    ),
    ("tasks", "delete_tasks"): Route(
        f"{BASE_PATH}/delete", TasksDeleteTasksRequest, DeleteTasksResponse, mutating=True
    ),
    ("tasks", "delete_expired"): Route(
        f"{BASE_PATH}/delete-expired", TasksDeleteExpiredRequest, DeleteExpiredResult, mutating=True
    ),
    ("tasks", "start_task"): Route(
        f"{BASE_PATH}/start", TasksStartTaskRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "patch_task"): Route(
        f"{BASE_PATH}/patch",
        TasksPatchTaskRequest,
        StoreOperationResult,
        mutating=True,
        request_secrets=_task_secrets("patch."),
    ),
    ("tasks", "record_event"): Route(
        f"{BASE_PATH}/record-event", TasksRecordEventRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "record_outcome"): Route(
        f"{BASE_PATH}/record-outcome",
        TasksRecordOutcomeRequest,
        StoreOperationResult,
        mutating=True,
        request_secrets=("result.connector_states",),
    ),
    ("tasks", "request_cancellation"): Route(
        f"{BASE_PATH}/cancel", TasksRequestCancellationRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "add_work"): Route(
        f"{BASE_PATH}/work/add", TasksAddWorkRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "reconcile_delivery"): Route(
        f"{BASE_PATH}/work/reconcile-delivery",
        TasksReconcileDeliveryRequest,
        StoreOperationResult,
        mutating=True,
    ),
    ("tasks", "discard_work"): Route(
        f"{BASE_PATH}/work/discard", TasksDiscardWorkRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "get_work"): Route(
        f"{BASE_PATH}/work/get", TasksGetWorkRequest, Optional[WorkRecord]
    ),
    ("tasks", "get_works"): Route(
        f"{BASE_PATH}/work/batch-get", TasksGetWorksRequest, Dict[str, WorkRecord]
    ),
    ("tasks", "start_work"): Route(
        f"{BASE_PATH}/work/start", TasksStartWorkRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "finish_work"): Route(
        f"{BASE_PATH}/work/finish", TasksFinishWorkRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "requeue_work"): Route(
        f"{BASE_PATH}/work/requeue", TasksRequeueWorkRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "record_work_error"): Route(
        f"{BASE_PATH}/work/record-error",
        TasksRecordWorkErrorRequest,
        StoreOperationResult,
        mutating=True,
    ),
    ("tasks", "settle_work"): Route(
        f"{BASE_PATH}/work/settle", TasksSettleWorkRequest, StoreOperationResult, mutating=True
    ),
    ("tasks", "has_open_work"): Route(f"{BASE_PATH}/work/has-open", TasksHasOpenWorkRequest, bool),
    ("tasks", "get_queue_work_counts"): Route(
        f"{BASE_PATH}/work/queue-counts", TasksGetQueueWorkCountsRequest, QueueWorkCounts
    ),
    ("tasks", "summarize_works"): Route(
        f"{BASE_PATH}/work/summarize", TasksSummarizeWorksRequest, WorkSummary
    ),
    ("tasks", "filter_cancellation_requested"): Route(
        f"{BASE_PATH}/filter-cancellation-requested",
        TasksFilterCancellationRequestedRequest,
        set[str],
    ),
    ("tasks", "get_task_status"): Route(
        f"{BASE_PATH}/get-status", TasksGetTaskStatusRequest, Optional[TaskStatus]
    ),
    ("watches", "get_state"): Route(
        f"{BASE_PATH}/watch/get-state",
        WatchesGetStateRequest,
        Optional[WatchState],
        response_secrets=_watch_secrets("task."),
    ),
    ("watches", "get_task"): Route(
        f"{BASE_PATH}/watch/get",
        WatchesGetTaskRequest,
        Optional[WatchTask],
        response_secrets=_watch_secrets(),
    ),
    ("watches", "list_tasks"): Route(
        f"{BASE_PATH}/watch/list",
        WatchesListTasksRequest,
        list[WatchTask],
        response_secrets=_watch_secrets("*."),
    ),
    ("watches", "list_tasks_page"): Route(
        f"{BASE_PATH}/watch/list-page",
        WatchesListTasksPageRequest,
        WatchListPage,
        response_secrets=_watch_secrets("tasks.*."),
    ),
    ("watches", "create_task"): Route(
        f"{BASE_PATH}/watch/create",
        WatchesCreateTaskRequest,
        type(None),
        mutating=True,
        request_secrets=_watch_secrets("task."),
    ),
    ("watches", "patch_task"): Route(
        f"{BASE_PATH}/watch/patch",
        WatchesPatchTaskRequest,
        Optional[WatchTask],
        mutating=True,
        request_secrets=_watch_secrets("patch.values."),
        response_secrets=_watch_secrets(),
    ),
    ("watches", "delete_task"): Route(
        f"{BASE_PATH}/watch/delete", WatchesDeleteTaskRequest, bool, mutating=True
    ),
    ("watches", "delete_tasks"): Route(
        f"{BASE_PATH}/watch/bulk-delete",
        WatchesDeleteTasksRequest,
        list[WatchState],
        mutating=True,
        response_secrets=_watch_secrets("*.task."),
    ),
    ("watches", "get_candidate"): Route(
        f"{BASE_PATH}/watch/get-candidate", WatchesGetCandidateRequest, Optional[WatchCandidate]
    ),
    ("watches", "due_candidates"): Route(
        f"{BASE_PATH}/watch/list-due", WatchesDueCandidatesRequest, list[WatchCandidate]
    ),
    ("watches", "running"): Route(
        f"{BASE_PATH}/watch/list-running", WatchesRunningRequest, list[WatchRunning]
    ),
    ("watches", "begin_run"): Route(
        f"{BASE_PATH}/watch/begin-run",
        WatchesBeginRunRequest,
        Optional[WatchRun],
        mutating=True,
        response_secrets=_watch_secrets("task."),
    ),
    ("watches", "attach_task"): Route(
        f"{BASE_PATH}/watch/attach-task", WatchesAttachTaskRequest, bool, mutating=True
    ),
    ("watches", "finish_run"): Route(
        f"{BASE_PATH}/watch/finish-run",
        WatchesFinishRunRequest,
        bool,
        mutating=True,
        request_secrets=("result.connector_states",),
    ),
    ("watches", "deactivate_idle"): Route(
        f"{BASE_PATH}/watch/deactivate-idle", WatchesDeactivateIdleRequest, bool, mutating=True
    ),
    ("watches", "has_running_overlap"): Route(
        f"{BASE_PATH}/watch/has-running-overlap", WatchesHasRunningOverlapRequest, bool
    ),
    ("watches", "move"): Route(
        f"{BASE_PATH}/watch/move", WatchesMoveRequest, WatchMoveResult, mutating=True
    ),
    ("watches", "deactivate_under_uri"): Route(
        f"{BASE_PATH}/watch/deactivate-under-uri",
        WatchesDeactivateUnderUriRequest,
        list[WatchTask],
        mutating=True,
        response_secrets=_watch_secrets("*."),
    ),
}
