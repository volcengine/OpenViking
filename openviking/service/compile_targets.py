# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile configurations, execution history, and snapshot-backed execution.

Only registered configurations and their runs live in ``_system/compile/tasks``.
Transient executions keep recovery checkpoints in the ordinary Task private state.
Output admission uses an AGFS mutex and durable Task claims, without a target registry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import (
    Annotated,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Union,
)
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from openviking.core.namespace import classify_uri
from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.pyagfs.exceptions import (
    AGFSAlreadyExistsError,
    AGFSNotFoundError,
    AGFSNotSupportedError,
    GitConcurrentCommitError,
)
from openviking.resource.watch_types import uri_matches_prefix
from openviking.server.identity import RequestContext, Role
from openviking.service.external_task_service import ExternalTaskSnapshot
from openviking.service.task_domain import TaskPatch, sanitize_error
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.acl import AclAction
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)
LOCK_TIMEOUT_SECONDS = 30.0
HISTORY_RETENTION_SECONDS = 7 * 24 * 3600
GIT_RETRIES = 3
SNAPSHOT_BUSY_SECONDS = 30.0
TERMINAL = frozenset({"completed", "failed", "cancelled"})
_NULL_OID = "0" * 40
_RESULT_KEYS = {"added": "created", "modified": "updated", "deleted": "deleted"}
_INPUT_FIELDS = ("from", "to", "skill", "instruction", "args")
STATE_FIELDS = frozenset(
    (
        *_INPUT_FIELDS,
        "auto_update",
        "update_schedule",
        "task_first_create_time",
        "last_run_time",
        "last_triggered_slot",
        "next_triggered_slot",
        "progress",
        "execution_context",
    )
)


def validate_compile_id(compile_id: str) -> str:
    """Validate a configuration ID before it is used in an account-scoped path."""
    if not isinstance(compile_id, str) or not re.fullmatch(r"cpt_[0-9a-f]{32}", compile_id):
        raise InvalidArgumentError("Invalid compile_id")
    return compile_id


def validate_task_id(task_id: str) -> str:
    """Accept generated execution IDs, including deterministic idempotency IDs."""
    if not isinstance(task_id, str) or not re.fullmatch(
        r"cmp_[0-9a-f]{32}(?:[0-9a-f]{32})?", task_id
    ):
        raise InvalidArgumentError("Invalid task_id")
    return task_id


def new_record(payload: Mapping[str, Any], execution_context: Optional[dict] = None) -> dict:
    """Build a configuration; run times and successful progress are initially absent."""
    return {
        "from": list(payload["from"]),
        "to": payload["to"],
        "skill": payload["skill"],
        "instruction": payload.get("instruction"),
        "args": deepcopy(payload.get("args") or {}),
        "auto_update": bool(payload.get("auto_update", False)),
        "update_schedule": deepcopy(payload.get("update_schedule")),
        "task_first_create_time": None,
        "last_run_time": None,
        "last_triggered_slot": None,
        "next_triggered_slot": None,
        "progress": {},
        "execution_context": deepcopy(execution_context or {}),
    }


def execution_signature(record: Mapping[str, Any]) -> str:
    """Identify execution inputs, including encrypted private args, excluding scheduling."""
    inputs = {key: record.get(key) for key in _INPUT_FIELDS}
    inputs["args"] = inputs["args"] or {}
    inputs["private_args"] = (record.get("execution_context") or {}).get("private_args")
    return hashlib.sha256(
        json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class DailySchedule(BaseModel):
    """Daily Beijing-time trigger; hour and minute use the 24-hour clock."""

    model_config = ConfigDict(extra="forbid")
    frequency: Literal["daily"]
    hour: int = Field(default=0, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class WeeklySchedule(DailySchedule):
    """Weekly trigger with ISO weekday numbering, Monday=1 through Sunday=7."""

    frequency: Literal["weekly"]
    weekday: int = Field(default=1, ge=1, le=7)


UpdateSchedule = Annotated[Union[DailySchedule, WeeklySchedule], Field(discriminator="frequency")]


def parse_schedule(value: Any) -> dict:
    """Validate a schedule and return its explicit defaults."""
    try:
        return TypeAdapter(UpdateSchedule).validate_python(value).model_dump()
    except ValidationError as exc:
        raise InvalidArgumentError(
            f"Invalid update_schedule: {exc.errors(include_url=False)[0]['msg']}"
        ) from exc


def split_control_args(args: Optional[Mapping[str, Any]]) -> tuple[dict, dict]:
    """Extract creation-only schedule controls and reject caller-owned execution plans."""
    runtime = dict(args or {})
    reserved = {"mode", "last_compile_time", "changes"} & runtime.keys()
    if reserved:
        raise InvalidArgumentError(f"Reserved Compile args: {', '.join(sorted(reserved))}")
    control = {
        key: runtime.pop(key) for key in ("auto_update", "update_schedule") if key in runtime
    }
    if "auto_update" in control and not isinstance(control["auto_update"], bool):
        raise InvalidArgumentError("args.auto_update must be a boolean")
    if control.get("update_schedule") is not None:
        control["update_schedule"] = parse_schedule(control["update_schedule"])
    return control, runtime


class CompileStore(Protocol):
    """Account-scoped configuration and run persistence, with locked mutations."""

    async def get(self, account_id: str, compile_id: str) -> Optional[dict]: ...
    def edit(self, account_id: str, compile_id: str) -> Any: ...
    async def get_run(self, account_id: str, compile_id: str, task_id: str) -> Optional[dict]: ...
    async def put_run(self, account_id: str, compile_id: str, run: dict) -> None: ...
    def lock_run(self, account_id: str, compile_id: str, task_id: str) -> Any: ...


class CompileStateStore:
    """Persist exact configuration fields and compact run records under AGFS path locks."""

    def __init__(self, agfs: Any) -> None:
        self._agfs = agfs if isinstance(agfs, AsyncAGFSClient) else AsyncAGFSClient(agfs)
        self._cleanup_scan = None
        self._cleanup_lock = asyncio.Lock()

    @staticmethod
    def root(account_id: str) -> str:
        return f"/local/{account_id}/_system/compile/tasks"

    def _record_path(self, account_id: str, compile_id: str) -> str:
        return f"{self.root(account_id)}/{validate_compile_id(compile_id)}/state.json"

    def _run_path(self, account_id: str, compile_id: str, task_id: str) -> str:
        return f"{self.root(account_id)}/{validate_compile_id(compile_id)}/runs/{validate_task_id(task_id)}.json"

    async def read_json(self, path: str) -> Any:
        try:
            raw = await self._agfs.read(path)
        except (AGFSNotFoundError, FileNotFoundError):
            return None
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)

    async def write_json(self, path: str, value: Any, lease: Any = None) -> None:
        """Write under the caller's lock; errors propagate without publishing success."""
        fs_ctx = fs_ctx_from_agfs_path(path)
        if lease is not None:
            fs_ctx["lease_ref"] = lease["lease_ref"]
        try:
            await self._agfs.ensure_parent_dirs(path, fs_ctx=fs_ctx)
        except AGFSAlreadyExistsError:
            pass
        await self._agfs.write(path, json.dumps(value, ensure_ascii=False).encode(), fs_ctx=fs_ctx)

    @asynccontextmanager
    async def locked(self, path: str) -> AsyncIterator[Any]:
        lease = await self._agfs.pathlock_acquire_exact(path, timeout_secs=LOCK_TIMEOUT_SECONDS)
        try:
            yield lease
        finally:
            await self._agfs.pathlock_release(lease)

    def lock_config(self, account_id: str, compile_id: str) -> Any:
        return self.locked(self._record_path(account_id, compile_id))

    def lock_run(self, account_id: str, compile_id: str, task_id: str) -> Any:
        # A distinct mutex serializes multi-write finalization, without creating a file.
        return self.locked(self._run_path(account_id, compile_id, task_id) + ".finish")

    async def create(
        self,
        account_id: str,
        record: dict,
        *,
        compile_id: Optional[str] = None,
        before_publish: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> str:
        """Publish a new validated configuration without execution or snapshot side effects."""
        compile_id = compile_id or f"cpt_{uuid4().hex}"
        path = self._record_path(account_id, compile_id)
        if set(record) != STATE_FIELDS:
            raise InvalidArgumentError("Compile state fields do not match the configuration schema")
        async with self.locked(path) as lease:
            if await self.read_json(path) is not None:
                raise ConflictError("Compile configuration already exists")
            if before_publish is not None:
                await before_publish()
            await self.write_json(path, record, lease)
        return compile_id

    async def get(self, account_id: str, compile_id: str) -> Optional[dict]:
        return await self.read_json(self._record_path(account_id, compile_id))

    @asynccontextmanager
    async def edit(self, account_id: str, compile_id: str) -> AsyncIterator[dict]:
        """Mutate an existing configuration atomically; failed bodies leave it unchanged."""
        path = self._record_path(account_id, compile_id)
        async with self.locked(path) as lease:
            record = await self.read_json(path)
            if record is None:
                raise NotFoundError(compile_id, "compile configuration")
            yield record
            if set(record) != STATE_FIELDS:
                raise InvalidArgumentError(
                    "Compile state fields do not match the configuration schema"
                )
            await self.write_json(path, record, lease)

    async def iter_records(
        self, account_id: str, *, cursor: Optional[str] = None
    ) -> AsyncIterator[tuple[str, dict]]:
        """Enumerate configuration IDs in lexical order; unreadable JSON is an error."""
        if cursor is not None:
            validate_compile_id(cursor)
        try:
            entries = await self._agfs.ls(self.root(account_id))
        except (AGFSNotFoundError, FileNotFoundError):
            return
        for entry in sorted(entries, key=lambda item: str(item.get("name") or "")):
            compile_id = str(entry.get("name") or "")
            if not re.fullmatch(r"cpt_[0-9a-f]{32}", compile_id) or (
                cursor and compile_id <= cursor
            ):
                continue
            record = await self.get(account_id, compile_id)
            if record is not None:
                yield compile_id, record

    async def list_accounts(self) -> list[str]:
        try:
            entries = await self._agfs.ls("/local")
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        return [
            str(e["name"])
            for e in entries
            if e.get("name") and e["name"] != "_system" and e.get("isDir", e.get("is_dir", False))
        ]

    async def get_run(self, account_id: str, compile_id: str, task_id: str) -> Optional[dict]:
        return await self.read_json(self._run_path(account_id, compile_id, task_id))

    async def put_run(self, account_id: str, compile_id: str, run: dict) -> None:
        path = self._run_path(account_id, compile_id, run["task_id"])
        async with self.locked(path) as lease:
            await self.write_json(path, run, lease)

    async def list_runs(
        self,
        account_id: str,
        compile_id: str,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[dict]:
        """Read newest runs by (created_at, task_id); cursor identifies the last returned run."""
        directory = f"{self.root(account_id)}/{validate_compile_id(compile_id)}/runs"
        try:
            entries = await self._agfs.ls(directory)
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        runs = []
        for entry in entries:
            name = str(entry.get("name") or "")
            if name.endswith(".json"):
                run = await self.read_json(f"{directory}/{name}")
                if run is not None:
                    runs.append(run)
        runs.sort(key=lambda run: (run["created_at"], run["task_id"]), reverse=True)
        if cursor:
            validate_task_id(cursor)
            boundary = next((r for r in runs if r["task_id"] == cursor), None)
            if boundary is None:
                raise InvalidArgumentError("Run cursor does not exist")
            key = boundary["created_at"], boundary["task_id"]
            runs = [r for r in runs if (r["created_at"], r["task_id"]) < key]
        return runs if limit is None else runs[: max(limit, 0)]

    async def _history_candidates(self) -> AsyncIterator[tuple[str, str, str]]:
        for account_id in await self.list_accounts():
            async for compile_id, _ in self.iter_records(account_id):
                for run in await self.list_runs(account_id, compile_id):
                    yield account_id, compile_id, run["task_id"]

    async def delete_expired(self, *, now: float, limit: int) -> int:
        """Resume bounded history cleanup, retaining every unsettled Task's recovery run."""
        from openviking.service.task_tracker import get_task_tracker

        if limit <= 0:
            return 0
        removed = 0
        async with self._cleanup_lock:
            if self._cleanup_scan is None:
                self._cleanup_scan = self._history_candidates()
            for _ in range(limit):
                try:
                    account_id, compile_id, task_id = await anext(self._cleanup_scan)
                except StopAsyncIteration:
                    self._cleanup_scan = None
                    break
                async with self.lock_run(account_id, compile_id, task_id):
                    run = await self.get_run(account_id, compile_id, task_id)
                    if run is None:
                        continue
                    finished = run.get("finished_at")
                    if (
                        run["status"] not in TERMINAL
                        or isinstance(finished, bool)
                        or not isinstance(finished, (int, float))
                        or not math.isfinite(finished)
                        or finished < 0
                        or finished >= now - HISTORY_RETENTION_SECONDS
                    ):
                        continue
                    tasks = await get_task_tracker()._store.list_durable(
                        account_id, task_type="compile"
                    )
                    task = next((item for item in tasks if item.task_id == task_id), None)
                    if task is not None and not task.is_terminal():
                        continue
                    path = self._run_path(account_id, compile_id, task_id)
                    async with self.locked(path) as lease:
                        try:
                            await self._agfs.rm(
                                path,
                                fs_ctx={
                                    **fs_ctx_from_agfs_path(path),
                                    "lease_ref": lease["lease_ref"],
                                },
                            )
                        except (AGFSNotFoundError, FileNotFoundError):
                            continue
                    removed += 1
        return removed


def task_ctx(account_id: str, user_id: str) -> RequestContext:
    """Use the accepted task identity for internal Git access; writes recheck actor ACL."""
    return RequestContext(
        user=UserIdentifier(account_id=account_id, user_id=user_id), role=Role.USER, bypass_acl=True
    )


def overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(f"{b}/") or b.startswith(f"{a}/")


def _params(request: Mapping[str, Any]) -> dict:
    control = request.get("compile") or {}
    return {
        **{key: deepcopy(request.get(key)) for key in _INPUT_FIELDS},
        "args": deepcopy(request.get("args") or {}),
        "auto_update": control.get("auto_update", False),
        "update_schedule": deepcopy(control.get("update_schedule")),
    }


def new_run(task: Any) -> dict:
    """Freeze public inputs and execution identity; private checkpoints stay on the Task."""
    return {
        "task_id": task.task_id,
        "actor": task.user_id,
        "status": "queued",
        "created_at": task.created_at,
        "started_at": None,
        "finished_at": None,
        "params": _params(task.meta["request"]),
        "result": None,
        "error": None,
        "meta": {
            "trigger": (task.meta["request"].get("compile") or {}).get("trigger", "manual"),
            "mode": None,
            "base_snapshot": None,
            "start_snapshot": None,
            "finish_snapshot": None,
            "skill_snapshot_id": None,
        },
    }


class CompileLifecycle:
    """Execute full and registered compiles with one recoverable snapshot lifecycle."""

    def __init__(self, store: CompileStore, viking_fs: Any) -> None:
        self.store = self._store = store
        self._fs = viking_fs
        self.resolve_groups = None

    @staticmethod
    def validate_layout(to: str, sources: list[str]) -> None:
        for source in sources:
            if overlaps(source, to):
                raise InvalidArgumentError(
                    f"Compile 'from' directories must not overlap 'to': {source}"
                )

    @staticmethod
    def _compile_id(task: Any) -> Optional[str]:
        return (task.meta["request"].get("compile") or {}).get("compile_id")

    @staticmethod
    def _checkpoint(task: Any) -> dict:
        return (task.auth or {}).get("compile_execution") or {}

    async def _save_checkpoint(self, task: Any, updates: dict) -> None:
        """Persist recovery decisions before exposing their effects to another delivery."""
        from openviking.service.task_tracker import get_task_tracker

        checkpoint = {**self._checkpoint(task), **deepcopy(updates)}
        result = await get_task_tracker().patch_task(
            task.task_id,
            TaskPatch(auth={"compile_execution": checkpoint}),
            account_id=task.account_id,
            user_id=task.user_id,
        )
        if not result.accepted:
            raise FailedPreconditionError("Compile task no longer accepts execution checkpoints")
        task.auth = {**(task.auth or {}), "compile_execution": checkpoint}

    async def _read_run(self, task: Any) -> dict:
        compile_id = self._compile_id(task)
        if compile_id:
            run = await self._store.get_run(task.account_id, compile_id, task.task_id)
            if run is None:
                raise NotFoundError(task.task_id, "compile run")
            return run
        run = new_run(task)
        checkpoint = self._checkpoint(task)
        run.update(checkpoint.get("outcome") or {})
        run["meta"].update(checkpoint.get("snapshots") or {})
        run["meta"]["mode"] = "full"
        return run

    async def _save_run(self, task: Any, run: dict) -> None:
        if run.get("error"):
            run["error"] = sanitize_error(run["error"])
        compile_id = self._compile_id(task)
        if compile_id:
            await self._store.put_run(task.account_id, compile_id, run)
        else:
            # Full executions retain only target recovery facts outside compile storage.
            await self._save_checkpoint(
                task,
                {
                    "snapshots": {
                        key: run["meta"][key]
                        for key in ("start_snapshot", "finish_snapshot", "skill_snapshot_id")
                    },
                    "outcome": {
                        key: run[key]
                        for key in ("status", "started_at", "finished_at", "result", "error")
                    },
                },
            )

    async def record_queued(self, task: Any, *, locked_record: Optional[dict] = None) -> None:
        """Create history and acceptance times idempotently, before queue dispatch."""
        compile_id = self._compile_id(task)
        if not compile_id:
            return
        if locked_record is None:
            async with self._store.edit(task.account_id, compile_id) as record:
                await self.record_queued(task, locked_record=record)
            return
        # Writing the run first lets a replay repair times after interrupted state publication.
        if await self._store.get_run(task.account_id, compile_id, task.task_id) is None:
            await self._store.put_run(task.account_id, compile_id, new_run(task))
        if locked_record["task_first_create_time"] is None:
            from openviking.service.task_tracker import get_task_tracker

            accepted = await get_task_tracker()._store.list_durable(
                task.account_id, task_type="compile"
            )
            # A prior Task can survive an interrupted configuration publication.
            locked_record["task_first_create_time"] = min(
                [task.created_at]
                + [prior.created_at for prior in accepted if self._compile_id(prior) == compile_id]
            )
        locked_record["last_run_time"] = max(
            locked_record["last_run_time"] or task.created_at, task.created_at
        )

    @asynccontextmanager
    async def _admission(self, task: Any) -> AsyncIterator[None]:
        """Serialize claims for a target using a lock key only; no target state file exists."""
        target = task.meta["request"]["to"]
        path = f"/local/{task.account_id}/_system/task_locks/compile/{quote(target, safe='')}"
        lease = await self._fs._async_agfs.pathlock_acquire_exact(
            path, timeout_secs=LOCK_TIMEOUT_SECONDS
        )
        try:
            yield
        finally:
            await self._fs._async_agfs.pathlock_release(lease)

    async def _claim(self, task: Any) -> bool:
        """Inspect shared Task claims while the caller holds the output admission mutex."""
        from openviking.service.task_tracker import get_task_tracker

        tasks = await get_task_tracker()._store.list_durable(task.account_id, task_type="compile")
        for other in tasks:
            if other.task_id == task.task_id:
                task.auth = {
                    **(task.auth or {}),
                    "compile_execution": deepcopy(self._checkpoint(other)),
                }
                continue
            if other.is_terminal() or other.result is not None or other.error is not None:
                continue
            if other.meta.get("request", {}).get("to") == task.meta["request"][
                "to"
            ] and self._checkpoint(other).get("phase") not in {None, "finished"}:
                return False
        if not self._checkpoint(task).get("phase"):
            await self._save_checkpoint(task, {"phase": "planning"})
        return True

    async def _refresh_checkpoint(self, task: Any) -> None:
        """Read the latest private checkpoint without trusting another worker's cache."""
        from openviking.service.task_tracker import get_task_tracker

        tasks = await get_task_tracker()._store.list_durable(task.account_id, task_type="compile")
        current = next((item for item in tasks if item.task_id == task.task_id), None)
        if current is not None:
            task.auth = {
                **(task.auth or {}),
                "compile_execution": deepcopy(self._checkpoint(current)),
            }

    @asynccontextmanager
    async def _locked_output(self, to: str, ctx: RequestContext) -> AsyncIterator[Any]:
        """Hold a content lease while snapshotting or restoring, excluding Runtime writes."""
        try:
            lease = await self._fs._async_agfs.pathlock_acquire_tree(
                self._fs._uri_to_path(to, ctx=ctx)
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError("Compile output is being processed", uri=to) from exc
        try:
            yield lease
        finally:
            await self._fs._async_agfs.pathlock_release(lease)

    async def on_start(self, task: Any) -> Any:
        """Resume a fixed plan; target admission also serializes duplicate task deliveries."""
        if self._compile_id(task):
            await self.record_queued(task)
        try:
            async with self._admission(task):
                if not await self._claim(task):
                    return "busy"
                run = await self._read_run(task)
                if run["status"] in TERMINAL and run["finished_at"] is not None:
                    return ExternalTaskSnapshot(
                        status=run["status"],
                        result=run.get("result"),
                        error_message=run.get("error"),
                    )
                checkpoint = self._checkpoint(task)
                if checkpoint.get("phase") == "committing":
                    return ExternalTaskSnapshot(status="completed", result=run.get("result"))
                if checkpoint.get("phase") == "rolling_back":
                    return ExternalTaskSnapshot(
                        status=checkpoint["terminal_status"], error_message=run.get("error")
                    )
                if not checkpoint.get("plan"):
                    await self._begin_run(task, run)
                if self._checkpoint(task).get("noop"):
                    return ExternalTaskSnapshot(
                        status="completed", result=dict.fromkeys(_RESULT_KEYS.values(), 0)
                    )
                return "proceed"
        except LockAcquisitionError:
            return "busy"

    async def _begin_run(self, task: Any, run: dict) -> None:
        """Freeze source and Skill snapshots plus a replayable Runtime plan before submission."""
        request = task.meta["request"]
        compile_id = self._compile_id(task)
        ctx = task_ctx(task.account_id, task.user_id)
        meta = run["meta"]
        checkpoint = self._checkpoint(task)
        if "baseline" not in checkpoint:
            progress = {}
            if compile_id:
                record = await self._store.get(task.account_id, compile_id)
                if record is None:
                    raise NotFoundError(compile_id, "compile configuration")
                if execution_signature(record) == request["compile"]["input_signature"]:
                    progress = deepcopy(record["progress"])
            await self._save_checkpoint(
                task, {"baseline": progress, "progress_before": progress.get("source_commit")}
            )
        progress = self._checkpoint(task).get("baseline") or {}
        run["status"] = "running"
        run["started_at"] = run["started_at"] or time.time()
        if (
            compile_id
            and not classify_uri(request["skill"]).is_system
            and meta["skill_snapshot_id"] is None
        ):
            async with self._locked_output(request["skill"], ctx) as lease:
                meta["skill_snapshot_id"] = await commit_snapshot(
                    self._fs,
                    [request["skill"]],
                    f"compile {task.task_id} skill",
                    ctx,
                    wait_busy=False,
                    lease_ref=lease,
                )
                if not meta["skill_snapshot_id"]:
                    raise FailedPreconditionError("Compile Skill snapshot is unavailable")
            await self._save_run(task, run)
        if meta["start_snapshot"] is None:
            paths = [request["to"], *request["from"]] if compile_id else [request["to"]]
            async with self._locked_output(request["to"], ctx) as lease:
                meta["start_snapshot"] = await commit_snapshot(
                    self._fs,
                    paths,
                    f"compile {task.task_id} start",
                    ctx,
                    wait_busy=False,
                    lease_ref=lease,
                )
            if not meta["start_snapshot"]:
                raise FailedPreconditionError("Compile start snapshot is unavailable")
            await self._save_run(task, run)
        previous = progress.get("skill_snapshot") or {}
        incremental = bool(
            compile_id
            and progress.get("source_commit")
            and (request.get("instruction") or "") == (progress.get("instruction") or "")
            and previous.get("uri") == request["skill"]
        )
        if incremental and not classify_uri(request["skill"]).is_system:
            base, head = previous.get("snapshot_id"), meta["skill_snapshot_id"]
            incremental = bool(
                base
                and head
                and not await self._fs._async_agfs.run(
                    "git_diff_paths",
                    account=ctx.account_id,
                    base=base,
                    head=head,
                    paths=[self._fs._uri_to_tree_path(request["skill"], ctx=ctx)],
                )
            )
        meta["mode"] = "incremental" if incremental else "full"
        meta["base_snapshot"] = progress.get("source_commit") if incremental else None
        processed = set(progress.get("processed_roots") or [])
        diff_dirs = [uri for uri in request["from"] if incremental and uri in processed]
        full_dirs = [uri for uri in request["from"] if uri not in diff_dirs]
        plan = {
            "base": meta["base_snapshot"],
            "head": meta["start_snapshot"] if compile_id else None,
            "diff_dirs": diff_dirs,
            "full_dirs": full_dirs,
            "execution": {"mode": meta["mode"]},
            "skill_snapshot": {"uri": request["skill"], "snapshot_id": meta["skill_snapshot_id"]},
        }
        changes = (
            await self._fs.diff_paths(dirs=diff_dirs, base=plan["base"], head=plan["head"], ctx=ctx)
            if diff_dirs
            else []
        )
        await self._save_run(task, run)
        await self._save_checkpoint(
            task,
            {
                "phase": "running",
                "plan": plan,
                "baseline": None,
                "noop": incremental and not full_dirs and not changes,
            },
        )

    async def runtime_changes(
        self,
        payload: Mapping[str, Any],
        account_id: str,
        task_id: str,
        user_id: Optional[str] = None,
    ) -> dict:
        """Return the fixed private plan; a missing plan fails closed instead of guessing."""
        from openviking.service.task_tracker import get_task_tracker

        if user_id is None:
            raise FailedPreconditionError("Compile execution identity is missing")
        auth = await get_task_tracker().get_task_auth(
            task_id, account_id=account_id, user_id=user_id
        )
        plan = (auth.get("compile_execution") or {}).get("plan")
        if plan is None:
            raise FailedPreconditionError("Compile execution plan is not prepared")
        return deepcopy(plan)

    async def on_finish(self, task: Any, snapshot: ExternalTaskSnapshot) -> ExternalTaskSnapshot:
        """Finish output publication or rollback before exposing a terminal Task result."""
        compile_id = self._compile_id(task)
        if compile_id:
            await self.record_queued(task)
        try:
            async with self._admission(task):
                await self._refresh_checkpoint(task)
                if compile_id:
                    async with self._store.lock_run(task.account_id, compile_id, task.task_id):
                        return await self._finish(task, snapshot)
                return await self._finish(task, snapshot)
        except LockAcquisitionError:
            return ExternalTaskSnapshot(
                status="running" if snapshot.status == "completed" else "cancelling",
                stage="committing" if snapshot.status == "completed" else "cancelling",
            )

    async def _finish(self, task: Any, snapshot: ExternalTaskSnapshot) -> ExternalTaskSnapshot:
        from openviking.server.dependencies import get_service
        from openviking.service.task_tracker import get_task_tracker

        run = await self._read_run(task)
        if run["finished_at"] is not None:
            await self._advance_progress(task, run)
            await self._save_checkpoint(task, {"phase": "finished"})
            return ExternalTaskSnapshot(
                status=run["status"], result=run.get("result"), error_message=run.get("error")
            )
        ctx = task_ctx(task.account_id, task.user_id)
        target = task.meta["request"]["to"]
        meta = run["meta"]
        if await get_task_tracker().is_cancellation_requested(
            task.task_id, account_id=task.account_id, user_id=task.user_id
        ):
            snapshot = ExternalTaskSnapshot(status="cancelled")
        try:
            if snapshot.status == "completed" and meta["start_snapshot"]:
                await self._save_checkpoint(task, {"phase": "committing"})
                run["status"] = "committing"
                await self._save_run(task, run)
                await get_service().fs.finalize_compile_directories(target, ctx)
                if meta["finish_snapshot"] is None:
                    async with self._locked_output(target, ctx) as lease:
                        if await get_task_tracker().is_cancellation_requested(
                            task.task_id, account_id=task.account_id, user_id=task.user_id
                        ):
                            snapshot = ExternalTaskSnapshot(status="cancelled")
                        elif self._checkpoint(task).get("noop"):
                            meta["finish_snapshot"] = meta["start_snapshot"]
                        else:
                            meta["finish_snapshot"] = await commit_snapshot(
                                self._fs,
                                [target],
                                f"compile {task.task_id} finish",
                                ctx,
                                wait_busy=False,
                                lease_ref=lease,
                            )
                        if snapshot.status == "completed":
                            if not meta["finish_snapshot"]:
                                raise FailedPreconditionError(
                                    "Compile output snapshot is unavailable"
                                )
                            await self._save_run(task, run)
                if snapshot.status == "completed":
                    changes = await self._fs.diff_paths(
                        dirs=[target],
                        base=meta["start_snapshot"],
                        head=meta["finish_snapshot"],
                        ctx=ctx,
                    )
                    run["result"] = dict.fromkeys(_RESULT_KEYS.values(), 0)
                    for change in changes:
                        if is_versioned(change["uri"]):
                            run["result"][_RESULT_KEYS[change["change_type"]]] += 1
                    if await get_task_tracker().is_cancellation_requested(
                        task.task_id, account_id=task.account_id, user_id=task.user_id
                    ):
                        snapshot = ExternalTaskSnapshot(status="cancelled")
            if snapshot.status in {"failed", "cancelled"}:
                # A publication snapshot is not a completed rollback checkpoint.
                if (
                    self._checkpoint(task).get("phase") != "rolling_back"
                    and meta["finish_snapshot"]
                ):
                    meta["finish_snapshot"] = None
                    await self._save_run(task, run)
                await self._save_checkpoint(
                    task, {"phase": "rolling_back", "terminal_status": snapshot.status}
                )
                if meta["start_snapshot"] and not meta["finish_snapshot"]:
                    await run_to_completion(lambda: self._rollback(target, task, run, ctx))
                run["result"] = None
                run["error"] = "; ".join(
                    filter(None, [snapshot.error_message or snapshot.status, run.get("error")])
                )
        except ResourceBusyError as exc:
            if not exc.retryable:
                raise
            return ExternalTaskSnapshot(
                status="running" if snapshot.status == "completed" else "cancelling",
                stage="committing" if snapshot.status == "completed" else "cancelling",
            )
        run["status"] = snapshot.status
        run["finished_at"] = time.time()
        await self._save_run(task, run)
        await self._advance_progress(task, run)
        await self._save_checkpoint(task, {"phase": "finished"})
        return ExternalTaskSnapshot(
            status=run["status"],
            result=run.get("result") or snapshot.result,
            error_code=snapshot.error_code,
            error_message=run.get("error"),
        )

    async def _advance_progress(self, task: Any, run: dict) -> None:
        """Advance only the matching configuration's unchanged successful predecessor."""
        compile_id = self._compile_id(task)
        if not compile_id or run["status"] != "completed" or not run["meta"]["start_snapshot"]:
            return
        from openviking.service.task_tracker import get_task_tracker

        async with self._store.edit(task.account_id, compile_id) as record:
            if await get_task_tracker().is_cancellation_requested(
                task.task_id, account_id=task.account_id, user_id=task.user_id
            ):
                return
            if execution_signature(record) != task.meta["request"]["compile"]["input_signature"]:
                return
            if (record["progress"] or {}).get("source_commit") != self._checkpoint(task).get(
                "progress_before"
            ):
                return
            record["progress"] = {
                "source_commit": run["meta"]["start_snapshot"],
                "processed_roots": list(run["params"]["from"]),
                "instruction": run["params"].get("instruction"),
                "skill_snapshot": {
                    "uri": run["params"]["skill"],
                    "snapshot_id": run["meta"]["skill_snapshot_id"],
                },
            }

    async def _rollback(self, to: str, task: Any, run: Dict[str, Any], ctx: RequestContext) -> None:
        """Restore ``to`` to ``run``'s baseline; ``ctx`` provides snapshot access.

        Writes enforce the owner's USER ACL. Denied paths are recorded; storage
        errors propagate. A persisted head makes completion idempotent.
        """
        run_meta = run["meta"]
        restored, deleted = [], []
        try:
            async with self._locked_output(to, ctx) as lease:
                if run_meta.get("finish_snapshot"):
                    return
                actor_ctx = RequestContext(
                    user=ctx.user,
                    role=Role.USER,
                    bypass_acl=False,
                    group_ids=(
                        tuple(await self.resolve_groups(ctx.account_id, ctx.user.user_id))
                        if self.resolve_groups is not None
                        else ()
                    ),
                )
                head = await self._commit(
                    [to],
                    f"compile {task.task_id} rollback point",
                    ctx,
                    lease_ref=lease,
                    wait_busy=False,
                )
                if not head:
                    raise FailedPreconditionError("Compile rollback snapshot is unavailable")
                changes = await self._fs.diff_paths(
                    dirs=[to], base=run_meta["start_snapshot"], head=head, ctx=ctx
                )
                access = await self._fs._can_access_many(
                    [change["uri"] for change in changes], actor_ctx, action=AclAction.WRITE
                )
                skipped = {uri for uri, allowed in access.items() if not allowed}
                # Check replacements before deleting any descendants.
                for change in changes:
                    uri = change["uri"]
                    if uri in skipped or not await self._fs.exists(uri, ctx=ctx):
                        continue
                    is_dir = (await self._fs.stat(uri, ctx=ctx, skip_count=True)).get("isDir")
                    # Deletion removes direct grants; rebuilding needs parent write access.
                    replaces_node = (is_dir and change["change_type"] != "added") or (
                        change["change_type"] == "added"
                        and any(
                            other["change_type"] != "added"
                            and uri_matches_prefix(other["uri"], uri)
                            for other in changes
                        )
                    )
                    if replaces_node:
                        parent_access = await self._fs._can_access_many(
                            [uri.rsplit("/", 1)[0]], actor_ctx, action=AclAction.WRITE
                        )
                    else:
                        parent_access = {}
                    if is_dir:
                        descendants = await self._fs._collect_uris(
                            self._fs._uri_to_path(uri, ctx=ctx), True, ctx=ctx, strict=True
                        )
                        permissions = await self._fs._can_access_many(
                            [uri, *descendants], actor_ctx, action=AclAction.MANAGE
                        )
                        if not all(permissions.values()):
                            skipped.add(uri)
                    if not all(parent_access.values()):
                        skipped.add(uri)
                applied = set()
                for change in sorted(
                    changes, key=lambda item: item["uri"].count("/"), reverse=True
                ):
                    uri = change["uri"]
                    if any(uri_matches_prefix(uri, denied) for denied in skipped):
                        skipped.add(uri)
                        continue
                    if change["change_type"] == "added" and await self._fs.exists(uri, ctx=ctx):
                        try:
                            await self._fs.rm(uri, recursive=True, ctx=actor_ctx, lease_ref=lease)
                        except PermissionDeniedError:
                            skipped.add(uri)
                            continue
                        deleted.append(self._fs._uri_to_tree_path(uri, ctx=ctx))
                        applied.add(uri)
                for change in sorted(changes, key=lambda item: item["uri"].count("/")):
                    if change["change_type"] == "added":
                        continue
                    uri = change["uri"]
                    if any(uri_matches_prefix(uri, denied) for denied in skipped):
                        skipped.add(uri)
                        continue
                    content = await self._fs.show(run_meta["start_snapshot"], path=uri, ctx=ctx)
                    try:
                        if await self._fs.exists(uri, ctx=ctx):
                            if (await self._fs.stat(uri, ctx=ctx, skip_count=True)).get("isDir"):
                                await self._fs.rm(
                                    uri, recursive=True, ctx=actor_ctx, lease_ref=lease
                                )
                                deleted.append(self._fs._uri_to_tree_path(uri, ctx=ctx))
                                applied.add(uri)
                            elif await self._fs.read_file_bytes(uri, ctx=ctx) == content:
                                continue
                        await self._fs.write_file_bytes(
                            uri, content, ctx=actor_ctx, lease_ref=lease
                        )
                    except PermissionDeniedError:
                        skipped.add(uri)
                        continue
                    restored.append(self._fs._uri_to_tree_path(uri, ctx=ctx))
                    applied.add(uri)
                rollback_head = await self._commit(
                    [to],
                    f"compile {task.task_id} rollback",
                    ctx,
                    lease_ref=lease,
                    wait_busy=False,
                )
                if not rollback_head:
                    raise FailedPreconditionError("Compile rollback snapshot is unavailable")
                run_meta["finish_snapshot"] = rollback_head
                if skipped:
                    run["error"] = "Rollback denied for: " + ", ".join(sorted(skipped))
                await self._save_run(task, run)
        finally:
            if restored or deleted:
                try:
                    await self._fs._schedule_restore_reindex_for_paths(
                        written_paths=restored, deleted_paths=deleted, project_dir=to, real_ctx=ctx
                    )
                except Exception:
                    logger.exception("Compile rollback reindex scheduling failed")
                    self._fs._schedule_vector_rebuild(written=restored, deleted=deleted, ctx=ctx)

    async def _commit(
        self,
        paths: List[str],
        message: str,
        ctx: RequestContext,
        *,
        lease_ref: Optional[Dict[str, Any]] = None,
        wait_busy: bool = True,
    ) -> Optional[str]:
        return await commit_snapshot(
            self._fs, paths, message, ctx, lease_ref=lease_ref, wait_busy=wait_busy
        )


async def retry_git(action: Any) -> Any:
    """Retry a Git ref update that lost a CAS race, with a short random backoff."""
    for attempt in range(GIT_RETRIES):
        try:
            return await action()
        except GitConcurrentCommitError:
            if attempt == GIT_RETRIES - 1:
                raise
            await asyncio.sleep(random.uniform(0.1, 0.5))


async def commit_snapshot(
    viking_fs: Any,
    paths: List[str],
    message: str,
    ctx: RequestContext,
    *,
    lease_ref: Optional[Dict[str, Any]] = None,
    wait_busy: bool = True,
) -> Optional[str]:
    """Return the snapshot oid, or None when Git cannot provide a snapshot.

    Busy paths wait up to ``SNAPSHOT_BUSY_SECONDS`` unless ``wait_busy=False``
    lets the caller defer work through its queue.
    """
    deadline = time.monotonic() + SNAPSHOT_BUSY_SECONDS
    kwargs = {"lease_ref": lease_ref} if lease_ref is not None else {}
    while True:
        try:
            result = await retry_git(
                lambda: viking_fs.commit(message=message, paths=paths, ctx=ctx, **kwargs)
            )
            break
        except AGFSNotSupportedError:
            return None
        except ResourceBusyError:
            if not wait_busy or time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.2)
    oid = result.get("commit_oid")
    return None if not oid or oid == _NULL_OID else oid


def is_versioned(uri: str) -> bool:
    """Exclude derived directory summaries from reported output counts."""
    return uri.rsplit("/", 1)[-1] not in {".abstract.md", ".overview.md", ".relations.json"}
