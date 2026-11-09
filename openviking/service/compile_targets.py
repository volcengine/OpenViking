# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile configurations, execution history, and snapshot-backed execution.

Only registered configurations and their runs live in ``_system/compile/tasks``.
Transient executions keep recovery checkpoints in the ordinary Task private state.
The external task queue groups Compile runs by account and output target for serialization.
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

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, with_config
from typing_extensions import NotRequired, TypedDict

from openviking.core.identifiers import validate_user_id
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
from openviking.service.task_domain import TaskPatch, TaskStatus, sanitize_error
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.acl import AclAction
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.utils.tags import build_search_tags_filter
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
COMPILE_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$"
HISTORY_RETENTION_SECONDS = 7 * 24 * 3600
GIT_RETRIES = 3
SNAPSHOT_BUSY_SECONDS = 30.0
CONFLICT_INDEX_WAIT_SECONDS = 30.0
TERMINAL = frozenset({"completed", "failed", "cancelled"})
_NULL_OID = "0" * 40
_RESULT_KEYS = {"added": "created", "modified": "updated", "deleted": "deleted"}
_INPUT_FIELDS = ("from", "to", "skill", "instruction", "args")
STATE_FIELDS = frozenset(
    (
        *_INPUT_FIELDS,
        "updated_by",
        "auto_update",
        "update_schedule",
        "task_first_create_time",
        "last_run_time",
        "last_triggered_slot",
        "next_triggered_slot",
        "conflict_num",
        "progress",
        "execution_context",
    )
)


def validate_compile_name(compile_name: str) -> str:
    """Return an unchanged safe name, rejecting invalid account-scoped path segments."""
    if not isinstance(compile_name, str) or not re.fullmatch(COMPILE_NAME_PATTERN, compile_name):
        raise InvalidArgumentError("Invalid compile_name")
    return compile_name


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
        # Null means no completed count; integers count conflicting files, capped at 100.
        "conflict_num": None,
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


@with_config(ConfigDict(extra="forbid"))
class ScheduleTime(TypedDict, total=False):
    """Beijing wall-clock time. Omit both parts to assign a minute before 04:00."""

    hour: Annotated[int, Field(ge=0, le=23)]
    minute: Annotated[int, Field(ge=0, le=59)]


class DailySchedule(ScheduleTime):
    """Run every day at the same Beijing time."""

    frequency: Literal["daily"]


class WeeklySchedule(ScheduleTime):
    """Run on one ISO weekday (Monday=1 through Sunday=7), assigned if omitted."""

    frequency: Literal["weekly"]
    weekday: NotRequired[Annotated[int, Field(ge=1, le=7)]]


UpdateSchedule = Annotated[Union[DailySchedule, WeeklySchedule], Field(discriminator="frequency")]


def parse_schedule(value: Any, schedule_key: tuple[str, str, str]) -> dict:
    """Validate the rule and fill omitted fields from the stable (account, owner, name) seed."""
    try:
        schedule: UpdateSchedule = TypeAdapter(UpdateSchedule).validate_python(value)
    except ValidationError as exc:
        raise InvalidArgumentError(
            f"Invalid update_schedule: {exc.errors(include_url=False)[0]['msg']}"
        ) from exc
    rng = random.Random(json.dumps(schedule_key))
    if schedule["frequency"] == "weekly":
        schedule.setdefault("weekday", rng.randint(1, 7))
    hour, minute = divmod(rng.randrange(4 * 60), 60)
    schedule.setdefault("hour", 0 if "minute" in schedule else hour)
    schedule.setdefault("minute", minute)
    return dict(schedule)


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
    return control, runtime


class CompileStore(Protocol):
    """Account-unique named configurations and creator-scoped runs, with locked mutations."""

    async def get(
        self, account_id: str, owner_user_id: str, compile_name: str
    ) -> Optional[dict]: ...
    async def list_users(self, account_id: str) -> list[str]: ...
    def edit(self, account_id: str, owner_user_id: str, compile_name: str) -> Any: ...
    async def get_run(
        self, account_id: str, owner_user_id: str, compile_name: str, task_id: str
    ) -> Optional[dict]: ...
    async def put_run(
        self, account_id: str, owner_user_id: str, compile_name: str, run: dict
    ) -> None: ...
    def lock_run(
        self, account_id: str, owner_user_id: str, compile_name: str, task_id: str
    ) -> Any: ...


class CompileStateStore:
    """Persist creator-scoped names and run records under AGFS path locks."""

    def __init__(self, agfs: Any) -> None:
        self._agfs = agfs if isinstance(agfs, AsyncAGFSClient) else AsyncAGFSClient(agfs)
        self._cleanup_scan = None
        self._cleanup_lock = asyncio.Lock()

    @staticmethod
    def root(account_id: str, owner_user_id: Optional[str] = None) -> str:
        """Return the account or creator directory, rejecting unsafe user path segments."""
        root = f"/local/{account_id}/_system/compile/tasks"
        if owner_user_id is None:
            return root
        error = validate_user_id(owner_user_id)
        if error:
            raise InvalidArgumentError(error)
        return f"{root}/{owner_user_id}"

    @staticmethod
    def _name_segment(compile_name: str) -> str:
        """Encode case-sensitive names as lowercase filesystem path segments."""
        validate_compile_name(compile_name)
        return compile_name.encode("ascii").hex()

    @staticmethod
    def _segment_name(segment: str) -> Optional[str]:
        """Return the name of a canonical storage directory, or None for unrelated entries."""
        try:
            name = bytes.fromhex(segment).decode("ascii")
            return name if CompileStateStore._name_segment(name) == segment else None
        except (ValueError, InvalidArgumentError):
            return None

    def _record_path(self, account_id: str, owner_user_id: str, compile_name: str) -> str:
        if owner_user_id is None:
            raise InvalidArgumentError("user_id must be a string.")
        return (
            f"{self.root(account_id, owner_user_id)}/{self._name_segment(compile_name)}/state.json"
        )

    def _run_path(
        self, account_id: str, owner_user_id: str, compile_name: str, task_id: str
    ) -> str:
        if owner_user_id is None:
            raise InvalidArgumentError("user_id must be a string.")
        return f"{self.root(account_id, owner_user_id)}/{self._name_segment(compile_name)}/runs/{validate_task_id(task_id)}.json"

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

    def lock_config(self, account_id: str, owner_user_id: str, compile_name: str) -> Any:
        return self.locked(self._record_path(account_id, owner_user_id, compile_name))

    def lock_run(self, account_id: str, owner_user_id: str, compile_name: str, task_id: str) -> Any:
        # A distinct mutex serializes multi-write finalization, without creating a file.
        return self.locked(
            self._run_path(account_id, owner_user_id, compile_name, task_id) + ".finish"
        )

    async def create(
        self,
        account_id: str,
        owner_user_id: str,
        record: dict,
        *,
        compile_name: str,
        before_publish: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> str:
        """Publish a named configuration, atomically rejecting duplicates for the same creator.

        ``before_publish`` registers scheduling under the same state lock; its failure
        leaves the configuration unpublished. This method does not execute Compile.
        """
        path = self._record_path(account_id, owner_user_id, compile_name)
        record = deepcopy(record)
        record["updated_by"] = owner_user_id
        if set(record) != STATE_FIELDS:
            raise InvalidArgumentError("Compile state fields do not match the configuration schema")
        record["execution_context"]["owner_user_id"] = owner_user_id
        async with self.locked(path) as lease:
            if await self.read_json(path) is not None:
                raise ConflictError("Compile configuration already exists")
            if before_publish is not None:
                await before_publish()
            await self.write_json(path, record, lease)
        return compile_name

    async def get(self, account_id: str, owner_user_id: str, compile_name: str) -> Optional[dict]:
        return await self.read_json(self._record_path(account_id, owner_user_id, compile_name))

    @asynccontextmanager
    async def edit(
        self, account_id: str, owner_user_id: str, compile_name: str
    ) -> AsyncIterator[dict]:
        """Mutate an existing configuration atomically; failed bodies leave it unchanged."""
        path = self._record_path(account_id, owner_user_id, compile_name)
        async with self.locked(path) as lease:
            record = await self.read_json(path)
            if record is None:
                raise NotFoundError(compile_name, "compile configuration")
            yield record
            if set(record) != STATE_FIELDS:
                raise InvalidArgumentError(
                    "Compile state fields do not match the configuration schema"
                )
            if record["execution_context"].get("owner_user_id") != owner_user_id:
                raise InvalidArgumentError("Compile configuration owner cannot change")
            await self.write_json(path, record, lease)

    async def iter_records(
        self,
        account_id: str,
        *,
        user_id: Optional[str] = None,
        cursor: Optional[str] = None,
        compile_name: Optional[str] = None,
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yield matching configs in (name, owner) order; cursors are name or name:owner."""
        if compile_name is not None:
            validate_compile_name(compile_name)
        cursor_name, cursor_owner = None, None
        if cursor is not None:
            cursor_name, separator, owner = cursor.partition(":")
            validate_compile_name(cursor_name)
            if separator:
                if validate_user_id(owner):
                    raise InvalidArgumentError("Invalid Compile cursor owner")
                cursor_owner = owner
        owners = [user_id] if user_id is not None else await self.list_users(account_id)
        # shortcut: scans user directories; add an account index if listing is slow.
        candidates = []
        for owner_user_id in owners:
            try:
                entries = await self._agfs.ls(self.root(account_id, owner_user_id))
            except (AGFSNotFoundError, FileNotFoundError):
                continue
            for entry in entries:
                name = self._segment_name(str(entry.get("name") or ""))
                if name is None or (compile_name is not None and name != compile_name):
                    continue
                if cursor_name is not None:
                    if cursor_owner is None and name <= cursor_name:
                        continue
                    if cursor_owner is not None and (name, owner_user_id) <= (
                        cursor_name,
                        cursor_owner,
                    ):
                        continue
                candidates.append((name, owner_user_id))
        for name, owner_user_id in sorted(candidates):
            record = await self.get(account_id, owner_user_id, name)
            if record is not None:
                yield name, record

    async def list_users(self, account_id: str) -> list[str]:
        """List safe creator directories for account-wide discovery and scheduling."""
        try:
            entries = await self._agfs.ls(self.root(account_id))
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        return sorted(
            str(entry["name"])
            for entry in entries
            if entry.get("isDir", entry.get("is_dir", False))
            and validate_user_id(entry.get("name")) is None
        )

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

    async def get_run(
        self, account_id: str, owner_user_id: str, compile_name: str, task_id: str
    ) -> Optional[dict]:
        return await self.read_json(
            self._run_path(account_id, owner_user_id, compile_name, task_id)
        )

    async def put_run(
        self, account_id: str, owner_user_id: str, compile_name: str, run: dict
    ) -> None:
        path = self._run_path(account_id, owner_user_id, compile_name, run["task_id"])
        async with self.locked(path) as lease:
            await self.write_json(path, run, lease)

    async def list_runs(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> list[dict]:
        """Read newest runs by (created_at, task_id); cursor identifies the last returned run."""
        if owner_user_id is None:
            raise InvalidArgumentError("user_id must be a string.")
        directory = (
            f"{self.root(account_id, owner_user_id)}/{self._name_segment(compile_name)}/runs"
        )
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

    async def _history_candidates(self) -> AsyncIterator[tuple[str, str, str, str]]:
        """Yield run IDs from filenames so cleanup reads each candidate under its lock."""
        for account_id in await self.list_accounts():
            async for compile_name, record in self.iter_records(account_id):
                owner_user_id = record["execution_context"]["owner_user_id"]
                root = self.root(account_id, owner_user_id)
                directory = f"{root}/{self._name_segment(compile_name)}/runs"
                try:
                    entries = await self._agfs.ls(directory)
                except (AGFSNotFoundError, FileNotFoundError):
                    continue
                for entry in entries:
                    name = entry["name"]
                    if not name.endswith(".json") or entry.get("isDir", entry.get("is_dir", False)):
                        continue
                    try:
                        task_id = validate_task_id(name[:-5])
                    except InvalidArgumentError:
                        continue
                    yield account_id, owner_user_id, compile_name, task_id

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
                    account_id, owner_user_id, compile_name, task_id = await anext(
                        self._cleanup_scan
                    )
                except StopAsyncIteration:
                    self._cleanup_scan = None
                    break
                async with self.lock_run(account_id, owner_user_id, compile_name, task_id):
                    run = await self.get_run(account_id, owner_user_id, compile_name, task_id)
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
                    path = self._run_path(account_id, owner_user_id, compile_name, task_id)
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

    async def task_ctx(self, task: Any) -> RequestContext:
        """Restore the accepted executor with current groups and normal ACL checks.

        Private connection data supplies the authenticated role and fallback groups.
        Tasks without a saved role have USER privileges. Resolver failures propagate
        so authorization cannot silently reuse stale group membership.
        """
        connection = (task.auth or {}).get("openviking_connection") or {}
        groups = (
            await self.resolve_groups(task.account_id, task.user_id)
            if self.resolve_groups is not None
            else connection.get("group_ids", ())
        )
        return RequestContext(
            user=UserIdentifier(account_id=task.account_id, user_id=task.user_id),
            role=Role.parse(connection.get("role", Role.USER)),
            group_ids=tuple(groups),
            bypass_acl=False,
        )

    @staticmethod
    def validate_layout(to: str, sources: list[str]) -> None:
        for source in sources:
            if overlaps(source, to):
                raise InvalidArgumentError(
                    f"Compile 'from' directories must not overlap 'to': {source}"
                )

    @staticmethod
    def _compile_name(task: Any) -> Optional[str]:
        """Return the saved configuration name, or None for a one-off execution."""
        return (task.meta["request"].get("compile") or {}).get("compile_name")

    @staticmethod
    def _owner_user_id(task: Any) -> str:
        """Locate the configuration by its immutable creator, independently of the run actor."""
        return task.meta["request"]["compile"]["owner_user_id"]

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
        compile_name = self._compile_name(task)
        if compile_name:
            run = await self._store.get_run(
                task.account_id, self._owner_user_id(task), compile_name, task.task_id
            )
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
        compile_name = self._compile_name(task)
        if compile_name:
            await self._store.put_run(task.account_id, self._owner_user_id(task), compile_name, run)
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
        """Record acceptance idempotently before dispatch, marking the first run as initial."""
        compile_name = self._compile_name(task)
        if not compile_name:
            return
        if locked_record is None:
            async with self._store.edit(
                task.account_id, self._owner_user_id(task), compile_name
            ) as record:
                await self.record_queued(task, locked_record=record)
            return
        run = await self._store.get_run(
            task.account_id, self._owner_user_id(task), compile_name, task.task_id
        )
        first = None
        if locked_record["task_first_create_time"] is None or (
            run is None and task.created_at == locked_record["task_first_create_time"]
        ):
            from openviking.service.task_tracker import get_task_tracker

            accepted = await get_task_tracker()._store.list_durable(
                task.account_id, task_type="compile"
            )
            # A prior Task can survive an interrupted configuration publication.
            first = min(
                [task]
                + [
                    prior
                    for prior in accepted
                    if self._compile_name(prior) == compile_name
                    and self._owner_user_id(prior) == self._owner_user_id(task)
                ],
                key=lambda prior: (prior.created_at, prior.task_id),
            )
        # Writing the run first lets a replay repair times after interrupted state publication.
        if run is None:
            run = new_run(task)
            if first is not None and first.task_id == task.task_id:
                run["meta"]["trigger"] = "initial"
            await self._store.put_run(task.account_id, self._owner_user_id(task), compile_name, run)
        if locked_record["task_first_create_time"] is None:
            locked_record["task_first_create_time"] = first.created_at
        locked_record["last_run_time"] = max(
            locked_record["last_run_time"] or task.created_at, task.created_at
        )

    @asynccontextmanager
    async def _admission(self, task: Any) -> AsyncIterator[None]:
        """Serialize checkpoint and snapshot transitions for this output target."""
        target = task.meta["request"]["to"]
        path = f"/local/{task.account_id}/_system/task_locks/compile/{quote(target, safe='')}"
        lease = await self._fs._async_agfs.pathlock_acquire_exact(
            path, timeout_secs=LOCK_TIMEOUT_SECONDS
        )
        try:
            yield
        finally:
            await self._fs._async_agfs.pathlock_release(lease)

    async def _refresh_checkpoint(self, task: Any) -> None:
        """Read the latest private checkpoint without trusting another worker's cache."""
        from openviking.service.task_tracker import get_task_tracker

        current = await get_task_tracker()._store.get_durable_task(
            task.task_id, account_id=task.account_id, user_id=task.user_id
        )
        if current is not None and current.task_type == "compile":
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
        if self._compile_name(task):
            await self.record_queued(task)
        try:
            async with self._admission(task):
                await self._refresh_checkpoint(task)
                if not self._checkpoint(task).get("phase"):
                    await self._save_checkpoint(task, {"phase": "planning"})
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
        compile_name = self._compile_name(task)
        ctx = await self.task_ctx(task)
        meta = run["meta"]
        checkpoint = self._checkpoint(task)
        if "baseline" not in checkpoint:
            progress = {}
            if compile_name:
                record = await self._store.get(
                    task.account_id, self._owner_user_id(task), compile_name
                )
                if record is None:
                    raise NotFoundError(compile_name, "compile configuration")
                if execution_signature(record) == request["compile"]["input_signature"]:
                    progress = deepcopy(record["progress"])
            await self._save_checkpoint(
                task, {"baseline": progress, "progress_before": progress.get("source_commit")}
            )
        progress = self._checkpoint(task).get("baseline") or {}
        run["status"] = "running"
        run["started_at"] = run["started_at"] or time.time()
        if (
            compile_name
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
            paths = [request["to"], *request["from"]] if compile_name else [request["to"]]
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
            compile_name
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
            "head": meta["start_snapshot"] if compile_name else None,
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
        compile_name = self._compile_name(task)
        if compile_name:
            await self.record_queued(task)
        try:
            async with self._admission(task):
                await self._refresh_checkpoint(task)
                if compile_name:
                    async with self._store.lock_run(
                        task.account_id, self._owner_user_id(task), compile_name, task.task_id
                    ):
                        return await self._finish(task, snapshot)
                return await self._finish(task, snapshot)
        except LockAcquisitionError:
            return ExternalTaskSnapshot(
                status="running" if snapshot.status == "completed" else "cancelling",
                stage="committing" if snapshot.status == "completed" else "cancelling",
            )

    async def _finish(self, task: Any, snapshot: ExternalTaskSnapshot) -> ExternalTaskSnapshot:
        from openviking.service.task_tracker import get_task_tracker

        run = await self._read_run(task)
        if run["finished_at"] is not None:
            await self._record_conflicts(task, run)
            await self._advance_progress(task, run)
            await self._save_checkpoint(task, {"phase": "finished"})
            return ExternalTaskSnapshot(
                status=run["status"], result=run.get("result"), error_message=run.get("error")
            )
        ctx = await self.task_ctx(task)
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
        await self._record_conflicts(task, run)
        await self._advance_progress(task, run)
        await self._save_checkpoint(task, {"phase": "finished"})
        return ExternalTaskSnapshot(
            status=run["status"],
            result=run.get("result") or snapshot.result,
            error_code=snapshot.error_code,
            error_message=run.get("error"),
        )

    async def _record_conflicts(self, task: Any, run: dict) -> None:
        """Publish the final target's capped file count, retaining a durable value for retries.

        Search failures yield null without changing the execution outcome. State/checkpoint
        write failures propagate, so the target claim remains held until publication finishes.
        """
        compile_name = self._compile_name(task)
        checkpoint = self._checkpoint(task)
        if not compile_name or checkpoint.get("phase") == "finished":
            return
        if "conflict_num" not in checkpoint and not run["meta"]["start_snapshot"]:
            # Unstarted runs do not own the target; another Compile may be changing it.
            await self._save_checkpoint(task, {"conflict_num": None})
            checkpoint = self._checkpoint(task)
        if "conflict_num" not in checkpoint:
            from openviking.server.dependencies import get_service

            conflict_num = None
            try:
                from openviking.service.task_tracker import get_task_tracker

                if checkpoint.get("rollback_reindex_pending"):
                    raise FailedPreconditionError("Compile rollback indexes are not ready")
                if checkpoint.get("rollback_reindex_task_id"):
                    terminal = await get_task_tracker().wait(
                        checkpoint["rollback_reindex_task_id"],
                        account_id=task.account_id,
                        user_id=task.user_id,
                        timeout=CONFLICT_INDEX_WAIT_SECONDS,
                    )
                    if terminal.status != TaskStatus.COMPLETED:
                        raise FailedPreconditionError("Compile rollback index rebuild failed")
                search_ctx = await self.task_ctx(task)
                result = await get_service().search.find(
                    query="",
                    ctx=search_ctx,
                    target_uri=task.meta["request"]["to"],
                    filter=build_search_tags_filter(["has_conflict=true"]),
                    level=[2],
                    limit=100,
                )
                # shortcut: one 100-hit page is a capped count; use pagination for exact totals.
                conflict_num = min(result.total, 100)
            except Exception:
                logger.warning("Compile conflict count failed for %s", task.task_id, exc_info=True)
            await self._save_checkpoint(task, {"conflict_num": conflict_num})
        async with self._store.edit(
            task.account_id, self._owner_user_id(task), compile_name
        ) as record:
            if execution_signature(record) == task.meta["request"]["compile"]["input_signature"]:
                record["conflict_num"] = self._checkpoint(task)["conflict_num"]

    async def _advance_progress(self, task: Any, run: dict) -> None:
        """Advance only the matching configuration's unchanged successful predecessor."""
        compile_name = self._compile_name(task)
        if not compile_name or run["status"] != "completed" or not run["meta"]["start_snapshot"]:
            return
        from openviking.service.task_tracker import get_task_tracker

        async with self._store.edit(
            task.account_id, self._owner_user_id(task), compile_name
        ) as record:
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

        Writes enforce the executor's current permissions. Denied paths are recorded; storage
        errors propagate. A persisted head makes completion idempotent.
        """
        run_meta = run["meta"]
        restored, deleted = [], []
        try:
            async with self._locked_output(to, ctx) as lease:
                if run_meta.get("finish_snapshot"):
                    return
                actor_ctx = await self.task_ctx(task)
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
                if restored or deleted:
                    # The run may survive a crash before its rebuild can be scheduled.
                    await self._save_checkpoint(task, {"rollback_reindex_pending": True})
                run_meta["finish_snapshot"] = rollback_head
                if skipped:
                    run["error"] = "Rollback denied for: " + ", ".join(sorted(skipped))
                await self._save_run(task, run)
        finally:
            if restored or deleted:
                try:
                    reindex_task_id = await self._fs._schedule_restore_reindex_for_paths(
                        written_paths=restored, deleted_paths=deleted, project_dir=to, real_ctx=ctx
                    )
                except Exception:
                    logger.exception("Compile rollback reindex scheduling failed")
                    self._fs._schedule_vector_rebuild(written=restored, deleted=deleted, ctx=ctx)
                else:
                    await self._save_checkpoint(
                        task,
                        {
                            "rollback_reindex_pending": False,
                            "rollback_reindex_task_id": reindex_task_id,
                        },
                    )

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
