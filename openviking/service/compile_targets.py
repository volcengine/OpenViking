# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile configurations, execution history, and snapshot-backed execution.

Only registered configurations live in ``_system/compile/tasks``.
Execution history and recovery checkpoints belong to the ordinary Task.
Pre-upgrade executions retain their original Run/private checkpoints until settlement.
The external task queue groups Compile runs by account and output target for serialization.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import (
    Annotated,
    Any,
    AsyncIterator,
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

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, WithJsonSchema, with_config
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
    GitRestoreWritebackPartialError,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.external_task_service import SUBMIT_EXHAUSTED, ExternalTaskSnapshot
from openviking.service.task_domain import TaskPatch, TaskStatus, sanitize_error
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.pathlock_recovery import (
    is_compile_lock_conflict,
    lock_retry_delay,
)
from openviking.utils.compile_recovery import source_set_digest
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)
LOCK_TIMEOUT_SECONDS = 30.0
ROLLBACK_LOCK_MAX_RETRIES = 60
_ROLLBACK_LOCK_EXHAUSTED = "COMPILE_ROLLBACK_LOCK_EXHAUSTED"
# Pin the supported Unified_Ideograph ranges and 〇 so schema consumers need no Unicode property tables.
_HAN = (
    "〇\u3400-\u4dbf\u4e00-\u9fff\ufa0e-\ufa0f\ufa11\ufa13-\ufa14\ufa1f\ufa21\ufa23-\ufa24\ufa27-\ufa29"
    "\U00020000-\U0002a6df\U0002a700-\U0002b739\U0002b740-\U0002b81d\U0002b820-\U0002cea1"
    "\U0002ceb0-\U0002ebe0\U0002ebf0-\U0002ee5d\U00030000-\U0003134a\U00031350-\U000323af"
)
_NAME_PATTERN = (
    r"^(?:[A-Za-z0-9_][A-Za-z0-9_-]{0,63}|"
    rf"[A-Za-z0-9_{_HAN}][A-Za-z0-9_{_HAN}-]{{0,31}})$"
)
# Swagger UI applies top-level query patterns without Unicode mode. Some schema validators
# also let $ match before a final newline, which is invalid in a configuration name.
CompileName = Annotated[
    str,
    Field(strict=True, pattern=_NAME_PATTERN),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "allOf": [{"pattern": _NAME_PATTERN}],
            "not": {"pattern": r"\n"},
        }
    ),
]
_COMPILE_NAME = TypeAdapter(CompileName)
GIT_RETRIES = 3
SNAPSHOT_BUSY_SECONDS = 30.0
_NULL_OID = "0" * 40
_RESULT_KEYS = {"added": "created", "modified": "updated", "deleted": "deleted"}
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
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
        "progress",
        "execution_context",
    )
)


def validate_compile_name(compile_name: str) -> str:
    """Return the name unchanged or raise InvalidArgumentError."""
    try:
        return _COMPILE_NAME.validate_python(compile_name)
    except ValidationError as exc:
        raise InvalidArgumentError("Invalid compile_name") from exc


def validate_task_id(task_id: str) -> str:
    """Accept generated execution IDs, including deterministic idempotency IDs."""
    if not isinstance(task_id, str) or not re.fullmatch(
        r"cmp_[0-9a-f]{32}(?:[0-9a-f]{32})?", task_id
    ):
        raise InvalidArgumentError("Invalid task_id")
    return task_id


def same_json_value(left: Any, right: Any) -> bool:
    """Compare JSON values without equating booleans and numbers."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def new_record(payload: Mapping[str, Any], execution_context: Optional[dict] = None) -> dict:
    """Build configuration inputs and empty lifecycle fields for persistence."""
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
    """Creator-scoped configurations with atomic creation and conditional updates."""

    async def create_config(
        self, account_id: str, owner_user_id: str, compile_name: str, record: dict
    ) -> None:
        """Create a configuration without replacing an existing one."""

    async def update_config(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        changes: dict,
        expected: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        """Apply changes if expected fields match and report whether they were applied."""

    def list_configs(
        self,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        compile_name: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        fresh: bool = False,
    ) -> AsyncIterator[tuple[str, str, dict]]:
        """Iterate configurations matching the filters and cursor."""


class CompileStateStore:
    """Persist creator-scoped configurations under AGFS path locks."""

    def __init__(self, agfs: Any) -> None:
        self._agfs = agfs if isinstance(agfs, AsyncAGFSClient) else AsyncAGFSClient(agfs)

    async def create_config(
        self, account_id: str, owner_user_id: str, compile_name: str, record: dict
    ) -> None:
        await self.create(account_id, owner_user_id, record, compile_name=compile_name)

    async def update_config(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        changes: dict,
        expected: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        async with self.edit(account_id, owner_user_id, compile_name) as record:
            if expected is not None and any(
                not same_json_value(record.get(k), v) for k, v in expected.items()
            ):
                return False
            record.update(deepcopy(changes))
        return True

    async def list_configs(
        self,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        compile_name: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        fresh: bool = False,
    ) -> AsyncIterator[tuple[str, str, dict]]:
        if limit is not None and limit <= 0:
            return
        if account_id is not None and user_id is not None and compile_name and cursor is None:
            if fresh:
                try:
                    await self._agfs.stat(
                        self._record_path(account_id, user_id, compile_name), bypass_cache=True
                    )
                except (AGFSNotFoundError, FileNotFoundError):
                    return
            record = await self.get(account_id, user_id, compile_name)
            if record is not None:
                yield account_id, compile_name, record
            return
        accounts = [account_id] if account_id is not None else await self.list_accounts()
        for account in accounts:
            async for name, record in self.iter_records(
                account, user_id=user_id, cursor=cursor, compile_name=compile_name
            ):
                yield account, name, record
                if limit is not None:
                    limit -= 1
                    if limit == 0:
                        return

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
        if compile_name.isascii():
            return compile_name.encode("ascii").hex()
        encoded = base64.b32encode(compile_name.encode("utf-8")).decode("ascii")
        return "u-" + encoded.rstrip("=").lower()

    @staticmethod
    def _segment_name(segment: str) -> Optional[str]:
        """Return the name of a canonical storage directory, or None for unrelated entries."""
        try:
            if segment.startswith("u-"):
                encoded = segment[2:]
                padded = encoded + "=" * (-len(encoded) % 8)
                name = base64.b32decode(padded, casefold=True).decode("utf-8")
            else:
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

    async def create(
        self,
        account_id: str,
        owner_user_id: str,
        record: dict,
        compile_name: str,
        before_publish: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        """Publish a named configuration, atomically rejecting duplicates for the same creator.

        The creation timestamp records publication time in Unix seconds.
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
            record["config_id"] = str(uuid4())
            record["task_first_create_time"] = time.time()
            await self.write_json(path, record, lease)
        return compile_name

    async def get(self, account_id: str, owner_user_id: str, compile_name: str) -> Optional[dict]:
        path = self._record_path(account_id, owner_user_id, compile_name)
        record = await self.read_json(path)
        if record is None or "config_id" in record:
            return record
        async with self.locked(path) as lease:
            record = await self.read_json(path)
            if record is not None:
                await self._ensure_config_id(path, record, lease)
            return record

    async def _ensure_config_id(self, path: str, record: dict, lease: Any) -> None:
        """Backfill legacy configuration identity under its existing state lock."""
        if "config_id" not in record:
            record["config_id"] = str(uuid4())
            await self.write_json(path, record, lease)

    @asynccontextmanager
    async def edit(
        self, account_id: str, owner_user_id: str, compile_name: str
    ) -> AsyncIterator[dict]:
        """Persist only changed JSON under the configuration lock; failed bodies leave it unchanged."""
        path = self._record_path(account_id, owner_user_id, compile_name)
        async with self.locked(path) as lease:
            record = await self.read_json(path)
            if record is None:
                raise NotFoundError(compile_name, "compile configuration")
            await self._ensure_config_id(path, record, lease)
            config_id = record["config_id"]
            original_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
            yield record
            if set(record) != STATE_FIELDS | {"config_id"}:
                raise InvalidArgumentError(
                    "Compile state fields do not match the configuration schema"
                )
            if record["config_id"] != config_id:
                raise InvalidArgumentError("Compile configuration ID cannot change")
            if record["execution_context"].get("owner_user_id") != owner_user_id:
                raise InvalidArgumentError("Compile configuration owner cannot change")
            if json.dumps(record, ensure_ascii=False, sort_keys=True) != original_json:
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


def run_view(task: Any) -> dict:
    """Project the public Compile execution history from a Task."""
    status = task.status.value
    if status == "pending":
        status = "queued"
    elif status == "running" and task.stage == "committing":
        status = "committing"
    meta = task.meta.get("compile") or {}
    return {
        "task_id": task.task_id,
        "actor": task.user_id,
        "status": status,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "params": _params(task.meta["request"]),
        "result": deepcopy(task.result),
        "error": task.error or ("cancelled" if status == "cancelled" else None),
        "meta": {
            "trigger": meta.get("trigger", "manual"),
            **{
                key: meta.get(key)
                for key in (
                    "mode",
                    "base_snapshot",
                    "start_snapshot",
                    "finish_snapshot",
                    "skill_snapshot_id",
                )
            },
        },
    }


class CompileLifecycle:
    """Execute full and registered compiles with one recoverable snapshot lifecycle."""

    def __init__(self, store: CompileStore, viking_fs: Any) -> None:
        self.store = self._store = store
        self._fs = viking_fs
        self.resolve_groups = None
        self.creation_guard: Callable[[str, str], Awaitable[bool]] | None = None

    @asynccontextmanager
    async def _lock_operation(self, path: str) -> AsyncIterator[None]:
        """Hold a business mutex, independent of the store's persistence locks."""
        agfs = self._fs._async_agfs
        lease = await agfs.pathlock_acquire_exact(path, timeout_secs=LOCK_TIMEOUT_SECONDS)
        try:
            yield
        finally:
            await agfs.pathlock_release(lease)

    def _lock_config(self, account_id: str, owner_user_id: str, compile_name: str) -> Any:
        validate_compile_name(compile_name)
        if error := validate_user_id(owner_user_id):
            raise InvalidArgumentError(error)
        return self._lock_operation(
            f"/local/{account_id}/_system/task_locks/compile_config/"
            f"{owner_user_id}/{hashlib.sha256(compile_name.encode()).hexdigest()}"
        )

    async def create_config(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        record: dict,
        before_publish: Callable[[], Awaitable[None]],
    ) -> None:
        if isinstance(self._store, CompileStateStore):
            # Hold the legacy state.json lease through preparation and publication.
            await self._store.create(
                account_id,
                owner_user_id,
                record,
                compile_name=compile_name,
                before_publish=before_publish,
            )
            return
        async with self._lock_config(account_id, owner_user_id, compile_name):
            if await self.get_config(account_id, owner_user_id, compile_name) is not None:
                raise ConflictError("Compile configuration already exists")
            await before_publish()
            if self.creation_guard is not None and await self.creation_guard(
                account_id, owner_user_id
            ):
                raise FailedPreconditionError("Compile configuration owner is unavailable")
            await self._store.create_config(account_id, owner_user_id, compile_name, record)

    async def get_config(
        self, account_id: str, owner_user_id: str, compile_name: str, fresh: bool = False
    ) -> Optional[dict]:
        async for _, _, record in self._store.list_configs(
            account_id, user_id=owner_user_id, compile_name=compile_name, limit=1, fresh=fresh
        ):
            return record
        return None

    @asynccontextmanager
    async def edit_config(
        self, account_id: str, owner_user_id: str, compile_name: str
    ) -> AsyncIterator[dict]:
        if isinstance(self._store, CompileStateStore):
            async with self._store.edit(account_id, owner_user_id, compile_name) as record:
                yield record
            return
        async with self._lock_config(account_id, owner_user_id, compile_name):
            record = await self.get_config(account_id, owner_user_id, compile_name)
            if record is None:
                raise NotFoundError(compile_name, "compile configuration")
            original = deepcopy(record)
            yield record
            if set(record) != set(original):
                raise InvalidArgumentError(
                    "Compile state fields do not match the configuration schema"
                )
            if not same_json_value(record["config_id"], original["config_id"]):
                raise InvalidArgumentError("Compile configuration ID cannot change")
            changes = {
                key: value
                for key, value in record.items()
                if not same_json_value(original[key], value)
            }
            if changes and not await self._store.update_config(
                account_id,
                owner_user_id,
                compile_name,
                changes,
                expected={
                    key: original[key]
                    for key in changes.keys() | {"config_id", "task_first_create_time"}
                },
            ):
                raise ConflictError("Compile configuration changed during update")

    async def get_run(
        self, account_id: str, owner_user_id: str, compile_name: str, task_id: str
    ) -> Optional[dict]:
        from openviking.service.task_tracker import get_task_tracker

        record = await self.get_config(account_id, owner_user_id, compile_name)
        if record is None:
            return None
        tasks = await get_task_tracker().list_tasks(
            account_id=account_id,
            task_type="compile",
            resource_id=str(record["config_id"]),
            task_id=validate_task_id(task_id),
            limit=1,
        )
        return run_view(tasks[0]) if tasks else None

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

    @staticmethod
    def _legacy_execution(task: Any) -> bool:
        """Recognize pre-Task runs without changing their resource identity."""
        private = (task.auth or {}).get("external_request_private") or {}
        return private.get("compile_execution_format") != "task" and "compile" not in task.meta

    def _settled_outcome(self, task: Any) -> bool:
        checkpoint = self._checkpoint(task)
        return (
            checkpoint.get("phase") in {"finished", "rollback_exhausted"}
            and (checkpoint.get("outcome") or {}).get("status") in _TERMINAL
        )

    def _terminal_legacy_run(self, task: Any, run: dict) -> bool:
        return (
            self._legacy_execution(task)
            and run["status"] in _TERMINAL
            and run.get("finished_at") is not None
        )

    def _run_outcome(self, task: Any, run: dict) -> dict:
        keys: tuple[str, ...] = ("status", "result", "error")
        if self._legacy_execution(task):
            keys += ("started_at", "finished_at")
        return {key: run[key] for key in keys}

    def _legacy_run_store(self) -> CompileStateStore:
        """Use only the old file IO helpers, independently of the configuration provider."""
        if isinstance(self._store, CompileStateStore):
            return self._store
        return CompileStateStore(self._fs._async_agfs)

    def _legacy_run_path(self, task: Any) -> str:
        compile_name = self._compile_name(task)
        assert compile_name is not None
        directory = CompileStateStore.root(task.account_id, self._owner_user_id(task))
        name = CompileStateStore._name_segment(compile_name)
        return f"{directory}/{name}/runs/{validate_task_id(task.task_id)}.json"

    @staticmethod
    async def _patch_task(task: Any, patch: TaskPatch) -> None:
        """Persist a Compile checkpoint or raise if the task rejects it."""
        from openviking.service.task_tracker import get_task_tracker

        result = await get_task_tracker().patch_task(
            task.task_id, patch, account_id=task.account_id, user_id=task.user_id
        )
        if not result.accepted:
            raise FailedPreconditionError("Compile task no longer accepts execution checkpoints")

    async def _save_checkpoint(self, task: Any, updates: dict) -> None:
        """Persist recovery decisions before exposing their effects to another delivery."""
        checkpoint = {**self._checkpoint(task), **deepcopy(updates)}
        await self._patch_task(task, TaskPatch(auth={"compile_execution": checkpoint}))
        task.auth = {**(task.auth or {}), "compile_execution": checkpoint}

    async def _read_run(self, task: Any) -> dict:
        run = run_view(task)
        if self._legacy_execution(task):
            if self._compile_name(task):
                saved = await self._legacy_run_store().read_json(self._legacy_run_path(task))
                if saved is not None:
                    return saved
            else:
                run["meta"].update(self._checkpoint(task).get("snapshots") or {})
                run["meta"]["mode"] = "full"
            run["meta"]["trigger"] = task.meta["request"]["compile"].get("trigger", "manual")
        run.update(self._checkpoint(task).get("outcome") or {})
        return run

    async def _save_run(self, task: Any, run: dict) -> None:
        """Persist public snapshots; defer the final Task outcome until cleanup settles."""
        if run.get("error"):
            run["error"] = sanitize_error(run["error"])
        if self._legacy_execution(task):
            if run["status"] in _TERMINAL:
                run["finished_at"] = run.get("finished_at") or time.time()
            if self._compile_name(task):
                store = self._legacy_run_store()
                path = self._legacy_run_path(task)
                async with store.locked(path) as lease:
                    await store.write_json(path, run, lease)
            else:
                await self._save_checkpoint(
                    task,
                    {
                        "snapshots": {
                            key: run["meta"][key]
                            for key in ("start_snapshot", "finish_snapshot", "skill_snapshot_id")
                        },
                        "outcome": self._run_outcome(task, run),
                    },
                )
            return
        meta = deepcopy(run["meta"])
        stage = "committing" if run["status"] == "committing" else None
        await self._patch_task(task, TaskPatch(meta={"compile": meta}, stage=stage))
        task.meta["compile"] = meta
        if stage:
            task.stage = stage

    async def record_queued(self, task: Any, locked_record: Optional[dict] = None) -> None:
        """Advance the configuration's invocation time idempotently under its lock."""
        compile_name = self._compile_name(task)
        if not compile_name:
            return
        if locked_record is None:
            async with self.edit_config(
                task.account_id, self._owner_user_id(task), compile_name
            ) as record:
                self._record_invocation(task, record)
            return
        self._record_invocation(task, locked_record)

    @staticmethod
    def _record_invocation(task: Any, locked_record: dict) -> None:
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
        """Refresh durable public snapshots and private recovery decisions."""
        from openviking.service.task_tracker import get_task_tracker

        current = await get_task_tracker().get_durable_task(
            task.task_id, account_id=task.account_id, user_id=task.user_id
        )
        if current is not None and current.task_type == "compile":
            if "compile" in current.meta:
                task.meta["compile"] = deepcopy(current.meta["compile"])
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
        compile_name = self._compile_name(task)
        try:
            async with self._admission(task):
                await self._refresh_checkpoint(task)
                if not self._checkpoint(task).get("phase"):
                    await self._save_checkpoint(task, {"phase": "planning"})
                record = None
                if compile_name:
                    async with self.edit_config(
                        task.account_id, self._owner_user_id(task), compile_name
                    ) as record:
                        self._record_invocation(task, record)
                run = await self._read_run(task)
                if self._settled_outcome(task) or self._terminal_legacy_run(task, run):
                    return self._finished_snapshot(run)
                checkpoint = self._checkpoint(task)
                if checkpoint.get("phase") == "committing":
                    return ExternalTaskSnapshot(status="completed", result=run.get("result"))
                if checkpoint.get("phase") == "rolling_back":
                    return ExternalTaskSnapshot(
                        status=checkpoint["terminal_status"], error_message=run.get("error")
                    )
                if not checkpoint.get("plan"):
                    await self._begin_run(task, run, record)
                if self._checkpoint(task).get("noop"):
                    return ExternalTaskSnapshot(
                        status="completed", result=dict.fromkeys(_RESULT_KEYS.values(), 0)
                    )
                return "proceed"
        except LockAcquisitionError:
            return "busy"

    async def _begin_run(self, task: Any, run: dict, record: Optional[dict]) -> None:
        """Freeze a replayable plan using the config read at start, or no config for one-off runs."""
        request = task.meta["request"]
        compile_name = self._compile_name(task)
        ctx = await self.task_ctx(task)
        meta = run["meta"]
        checkpoint = self._checkpoint(task)
        if "baseline" not in checkpoint:
            progress = {}
            recovering = False
            if (
                record is not None
                and execution_signature(record) == request["compile"]["input_signature"]
            ):
                progress = deepcopy(record["progress"])
                recovering = record["execution_context"].get("partial_compile") == {
                    "base": progress.get("source_commit"),
                    "signature": request["compile"]["input_signature"],
                }
            await self._save_checkpoint(
                task,
                {
                    "baseline": progress,
                    "progress_before": progress.get("source_commit"),
                    "recovering": recovering,
                },
            )
        progress = self._checkpoint(task).get("baseline") or {}
        run["status"] = "running"
        if self._legacy_execution(task):
            run["started_at"] = run.get("started_at") or time.time()
            await self._save_run(task, run)
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
            and (request.get("args") or {}).get("model_name", "") == progress.get("model_name")
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
        if self._checkpoint(task).get("recovering") and incremental and not full_dirs and changes:
            if all(change["change_type"] in {"added", "modified"} for change in changes):
                plan["recovery_sources"] = source_set_digest(change["uri"] for change in changes)
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
        """Finalize the run, allowing submit failures to settle after lock retries."""
        skip_rollback = snapshot.stage == SUBMIT_EXHAUSTED and "external_task_id" not in task.auth
        retries = task.meta.get("rollback_lock_retries", 0)
        if not skip_rollback and isinstance(retries, int) and retries >= ROLLBACK_LOCK_MAX_RETRIES:
            return await self._fail_blocked_rollback(task, snapshot)
        submission_retry_delays = iter((1, 3))
        while True:
            try:
                async with self._admission(task):
                    await self._refresh_checkpoint(task)
                    return await self._finish(task, snapshot, skip_rollback=skip_rollback)
            except (LockAcquisitionError, ResourceBusyError, ValueError) as exc:
                if not is_compile_lock_conflict(exc):
                    raise
                if skip_rollback:
                    submission_delay = next(submission_retry_delays, None)
                    if submission_delay is None:
                        logger.warning(
                            "Compile submission failure exceeded run finalization lock retries",
                            extra={"task_id": task.task_id, "error_type": type(exc).__name__},
                        )
                        return snapshot
                    await asyncio.sleep(submission_delay)
                    continue
                if (
                    snapshot.status == "completed"
                    and task.status != TaskStatus.CANCELLING
                    and self._checkpoint(task).get("phase") != "rolling_back"
                ):
                    if not isinstance(exc, LockAcquisitionError):
                        raise
                    return ExternalTaskSnapshot(status="running", stage="committing")
                delay = lock_retry_delay(retries, ROLLBACK_LOCK_MAX_RETRIES)
                if delay is None:
                    return await self._fail_blocked_rollback(task, snapshot)
                update = {"rollback_lock_retries": retries + 1}
                await self._patch_task(task, TaskPatch(meta=update))
                task.meta.update(update)
                await asyncio.sleep(delay)
                return ExternalTaskSnapshot(status="cancelling", stage="cancelling")

    async def _fail_blocked_rollback(
        self, task: Any, snapshot: ExternalTaskSnapshot
    ) -> ExternalTaskSnapshot:
        """Persist the exhausted rollback outcome before allowing the Task to fail."""

        async def save_outcome() -> ExternalTaskSnapshot:
            await self._refresh_checkpoint(task)
            run = await self._read_run(task)
            if self._settled_outcome(task):
                return self._finished_snapshot(run, snapshot.error_code)
            message = f"Compile rollback incomplete after {ROLLBACK_LOCK_MAX_RETRIES} lock retries"
            if snapshot.error_message:
                message += f": {snapshot.error_message}"
            result = {
                "rollback_incomplete": True,
                "start_snapshot": run["meta"]["start_snapshot"],
            }
            run.update(status="failed", result=result, error=message)
            run["error"] = sanitize_error(message)
            await self._save_run(task, run)
            await self._save_checkpoint(
                task,
                {
                    "phase": "rollback_exhausted",
                    "outcome": self._run_outcome(task, run),
                },
            )
            return ExternalTaskSnapshot(
                status="failed",
                error_code=_ROLLBACK_LOCK_EXHAUSTED,
                error_message=message,
                result=result,
            )

        try:
            async with self._admission(task):
                return await save_outcome()
        except (LockAcquisitionError, ResourceBusyError, ValueError) as exc:
            if not is_compile_lock_conflict(exc):
                raise
            return ExternalTaskSnapshot(status="cancelling", stage="cancelling")

    @staticmethod
    def _finished_snapshot(run: dict, error_code: str | None = None) -> ExternalTaskSnapshot:
        """Replay the saved outcome, preferring a local rollback failure over the Runtime code."""
        result = run.get("result")
        if (
            run["status"] == "failed"
            and isinstance(result, dict)
            and result.get("rollback_incomplete") is True
        ):
            error_code = _ROLLBACK_LOCK_EXHAUSTED
        return ExternalTaskSnapshot(
            status=run["status"],
            result=result,
            error_code=error_code,
            error_message=run.get("error"),
        )

    async def _finish(
        self, task: Any, snapshot: ExternalTaskSnapshot, *, skip_rollback: bool
    ) -> ExternalTaskSnapshot:
        from openviking.service.task_tracker import get_task_tracker

        run = await self._read_run(task)
        if self._settled_outcome(task):
            return self._finished_snapshot(run, snapshot.error_code)
        if self._terminal_legacy_run(task, run):
            # Old Run publication preceded config/Task settlement. Replay those effects
            # without recounting outputs, resubmitting Runtime, or guessing missing undo.
            await self._finish_progress(task, run)
            await self._save_checkpoint(task, {"outcome": self._run_outcome(task, run)})
            return self._finished_snapshot(run, snapshot.error_code)
        target = task.meta["request"]["to"]
        meta = run["meta"]
        if (
            not task.has_compile_dependency_failure()
            and await get_task_tracker().is_cancellation_requested(
                task.task_id, account_id=task.account_id, user_id=task.user_id
            )
        ):
            snapshot = ExternalTaskSnapshot(status="cancelled")
        try:
            if snapshot.status == "completed" and meta["start_snapshot"]:
                ctx = await self.task_ctx(task)
                partial = self._checkpoint(task).get("partial_delivery") is True or (
                    isinstance(snapshot.result, dict)
                    and snapshot.result.get("completion_status") == "partial"
                )
                checkpoint: dict[str, Any] = {"phase": "committing"}
                if partial:
                    # Finalization can resume without the original Runtime response.
                    checkpoint["partial_delivery"] = True
                elif isinstance(snapshot.result, dict):
                    expected = (self._checkpoint(task).get("plan") or {}).get("recovery_sources")
                    if expected and snapshot.result.get("recovery_proof") == expected:
                        checkpoint["verified_recovery"] = True
                await self._save_checkpoint(task, checkpoint)
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
                    if partial:
                        run["result"]["completion_status"] = "partial"
                    for change in changes:
                        if is_versioned(change["uri"]):
                            run["result"][_RESULT_KEYS[change["change_type"]]] += 1
                    if await get_task_tracker().is_cancellation_requested(
                        task.task_id, account_id=task.account_id, user_id=task.user_id
                    ):
                        snapshot = ExternalTaskSnapshot(status="cancelled")
                    else:
                        from openviking.observability.reporting.facts import emit_compile_outputs

                        # Persist the event time so finalization retries identify the same facts.
                        if not self._checkpoint(task).get("target_committed_at"):
                            await self._save_checkpoint(
                                task, {"target_committed_at": int(time.time() * 1000)}
                            )
                        emit_compile_outputs(
                            task,
                            changes,
                            snapshot_id=meta["finish_snapshot"],
                            committed_at=self._checkpoint(task)["target_committed_at"],
                        )
            if snapshot.status in {"failed", "cancelled"}:
                if not skip_rollback:
                    # A publication snapshot is not a completed rollback checkpoint.
                    if (
                        self._checkpoint(task).get("phase") != "rolling_back"
                        and not self._checkpoint(task).get("terminal_status")
                        and meta["finish_snapshot"]
                    ):
                        meta["finish_snapshot"] = None
                        await self._save_run(task, run)
                    await self._save_checkpoint(
                        task, {"phase": "rolling_back", "terminal_status": snapshot.status}
                    )
                    if meta["start_snapshot"] and not meta["finish_snapshot"]:
                        ctx = await self.task_ctx(task)
                        await run_to_completion(lambda: self._rollback(target, task, run, ctx))
                run["result"] = None
                run["error"] = "; ".join(
                    filter(None, [snapshot.error_message or snapshot.status, run.get("error")])
                )
        except ResourceBusyError as exc:
            if not exc.retryable or snapshot.status != "completed":
                raise
            return ExternalTaskSnapshot(status="running", stage="committing")
        run["status"] = snapshot.status
        await self._save_run(task, run)
        await self._finish_progress(task, run)
        await self._save_checkpoint(
            task,
            {
                "outcome": self._run_outcome(task, run),
            },
        )
        return ExternalTaskSnapshot(
            status=run["status"],
            result=run.get("result") or snapshot.result,
            error_code=snapshot.error_code,
            error_message=run.get("error"),
        )

    async def _finish_progress(self, task: Any, run: dict) -> None:
        try:
            if run["status"] == "completed":
                await self._advance_progress(task, run)
            elif (
                self._checkpoint(task).get("terminal_status") in {"failed", "cancelled"}
                and run["meta"]["finish_snapshot"]
            ):
                await self._restore_progress(task)
        except NotFoundError as exc:
            # An accepted Task can still settle if there is no config to update.
            if exc.details.get("type") != "compile configuration" or exc.details.get(
                "resource"
            ) != self._compile_name(task):
                raise
        await self._save_checkpoint(task, {"phase": "finished"})

    async def _advance_progress(self, task: Any, run: dict) -> None:
        """Advance a matching baseline after publication or verified recovery."""
        compile_name = self._compile_name(task)
        if not compile_name or run["status"] != "completed" or not run["meta"]["start_snapshot"]:
            return
        result = run.get("result") or {}
        partial = result.get("completion_status") == "partial"
        checkpoint = self._checkpoint(task)
        previous = checkpoint.get("progress_before")
        if partial and not previous:
            return
        if not (
            partial
            or checkpoint.get("verified_recovery")
            or any(result.get(key, 0) for key in _RESULT_KEYS.values())
        ):
            return
        from openviking.service.task_tracker import get_task_tracker

        async with self.edit_config(
            task.account_id, self._owner_user_id(task), compile_name
        ) as record:
            if await get_task_tracker().is_cancellation_requested(
                task.task_id, account_id=task.account_id, user_id=task.user_id
            ):
                return
            if (
                not self._legacy_execution(task) and str(record["config_id"]) != task.resource_id
            ) or execution_signature(record) != task.meta["request"]["compile"]["input_signature"]:
                return
            state = self._progress_state(record)
            undo = checkpoint.get("progress_rollback")
            if undo:
                # Replays reuse the first undo record, even after a config write succeeded.
                if undo["config_id"] != record["config_id"] or same_json_value(
                    state, undo["after"]
                ):
                    return
                if not same_json_value(state, undo["before"]):
                    return
                after = undo["after"]
            else:
                if (record["progress"] or {}).get("source_commit") != previous:
                    return
                after = deepcopy(state)
                if partial:
                    after["partial_compile"] = {
                        "base": previous,
                        "signature": task.meta["request"]["compile"]["input_signature"],
                    }
                else:
                    after["partial_compile"] = None
                    after["progress"] = {
                        "source_commit": run["meta"]["start_snapshot"],
                        "processed_roots": list(run["params"]["from"]),
                        "instruction": run["params"].get("instruction"),
                        # Empty uses Runtime's default model selection.
                        "model_name": (run["params"].get("args") or {}).get("model_name", ""),
                        "skill_snapshot": {
                            "uri": run["params"]["skill"],
                            "snapshot_id": run["meta"]["skill_snapshot_id"],
                        },
                    }
                if same_json_value(state, after):
                    return
                # Persist undo under the config lock before publishing the new baseline.
                await self._save_checkpoint(
                    task,
                    {
                        "progress_rollback": {
                            "config_id": record["config_id"],
                            "before": state,
                            "after": after,
                        }
                    },
                )
            self._set_progress_state(record, after)

    @staticmethod
    def _progress_state(record: dict) -> dict:
        return {
            "progress": deepcopy(record["progress"]),
            "partial_compile": deepcopy(record["execution_context"].get("partial_compile")),
        }

    @staticmethod
    def _set_progress_state(record: dict, state: dict) -> None:
        record["progress"] = deepcopy(state["progress"])
        if state["partial_compile"] is None:
            record["execution_context"].pop("partial_compile", None)
        else:
            record["execution_context"]["partial_compile"] = deepcopy(state["partial_compile"])

    async def _restore_progress(self, task: Any) -> None:
        """Undo this run's baseline only after output rollback, preserving later config changes."""
        undo = self._checkpoint(task).get("progress_rollback")
        if not undo:
            return
        async with self.edit_config(
            task.account_id, self._owner_user_id(task), self._compile_name(task)
        ) as record:
            if (
                record["config_id"] == undo["config_id"]
                and execution_signature(record)
                == task.meta["request"]["compile"]["input_signature"]
                and same_json_value(self._progress_state(record), undo["after"])
            ):
                self._set_progress_state(record, undo["before"])

    async def _rollback(self, to: str, task: Any, run: Dict[str, Any], ctx: RequestContext) -> None:
        """Restore the output baseline and persist its resulting commit under the output lease.

        Each attempt snapshots current files so restore sees unfinished writes from a
        failed attempt. Missing source subtrees represent empty baselines. Changed paths
        are reindexed after releasing the lease, including partial writeback failures.
        """
        run_meta = run["meta"]
        restored, deleted = [], []
        try:
            async with self._locked_output(to, ctx) as lease:
                if run_meta.get("finish_snapshot"):
                    return
                head = await self._commit(
                    [to],
                    f"compile {task.task_id} rollback point",
                    ctx,
                    lease_ref=lease,
                    wait_busy=False,
                )
                if not head:
                    raise FailedPreconditionError("Compile rollback snapshot is unavailable")
                try:
                    result = await retry_git(
                        lambda: self._fs.restore(
                            project_dir=to,
                            source_commit=run_meta["start_snapshot"],
                            message=f"compile {task.task_id} rollback",
                            ctx=ctx,
                            allow_missing_source=True,
                            lease_ref=lease,
                        )
                    )
                except GitRestoreWritebackPartialError as exc:
                    restored, deleted = exc.written_paths, exc.deleted_paths
                    raise
                restored = result.get("written_paths") or []
                deleted = result.get("deleted_paths") or []
                oid = result.get("new_commit_oid") if result.get("result") == "applied" else None
                if result.get("result") == "noop":
                    oid = result.get("head")
                if not oid or oid == _NULL_OID:
                    raise FailedPreconditionError("Compile rollback snapshot is unavailable")
                run_meta["finish_snapshot"] = oid
                await self._save_run(task, run)
        finally:
            if restored or deleted:
                try:
                    await self._fs._schedule_restore_reindex_for_paths(
                        written_paths=restored, deleted_paths=deleted, project_dir=to, real_ctx=ctx
                    )
                except Exception:
                    logger.error("compile rollback reindex scheduling failed")
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
