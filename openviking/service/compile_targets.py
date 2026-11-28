# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile configurations, execution history, and snapshot-backed execution.

Only registered configurations and their runs live in ``_system/compile/tasks``.
Transient executions keep recovery checkpoints in the ordinary Task private state.
The external task queue groups Compile runs by account and output target for serialization.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import heapq
import json
import math
import random
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
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
from openviking.service.task_domain import TaskPatch, TaskRecord, TaskStatus, sanitize_error
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


RUN_FIELDS = frozenset(
    (
        "task_id",
        "actor",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "params",
        "result",
        "error",
        "meta",
    )
)


CompileConfigKey = tuple[str, str, str]  # account_id, compile_name, owner_user_id


class CompileStore(Protocol):
    """Persistence for creator-scoped configs and runs.

    Config creation raises ConflictError for an existing key. Run creation
    returns False for an existing key. Conditional writes compare expected
    fields against persisted values within one atomic operation.
    Run replacement requires a complete snapshot. It retains the same existence
    and expected-field checks as partial updates; callers serialize the sequence
    from reading that snapshot to writing it back.
    """

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
        auto_update: bool | None = None,
        after_key: CompileConfigKey | None = None,
    ) -> AsyncIterator[tuple[str, str, dict]]:
        """Iterate matching configurations, strictly after the optional full key."""

    async def list_runs(
        self,
        account_id: str | None = None,
        owner_user_id: str | None = None,
        compile_name: str | None = None,
        task_id: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        keys_only: bool = False,
        finished_before: float | None = None,
        statuses: frozenset[str] | None = None,
        after_key: tuple[float, int, str] | None = None,
    ) -> list[dict]:
        """Return matching runs in history or expiry order."""

    async def create_run(
        self, account_id: str, owner_user_id: str, compile_name: str, run: dict
    ) -> bool:
        """Create a run if its key does not already exist."""

    async def update_run(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        task_id: str,
        changes: dict,
        expected: Optional[Mapping[str, Any]] = None,
        replace: bool = False,
    ) -> bool:
        """Update or replace an existing run if expected fields match."""

    async def delete_run(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        task_id: str,
        expected: Mapping[str, Any],
    ) -> bool:
        """Delete a run only if expected fields match."""


class CompileStateStore:
    """Persist creator-scoped names and run records under AGFS path locks."""

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

    async def _delete_file(self, path: str, lease: Any) -> bool:
        try:
            await self._agfs.rm(
                path,
                fs_ctx={**fs_ctx_from_agfs_path(path), "lease_ref": lease["lease_ref"]},
            )
        except (AGFSNotFoundError, FileNotFoundError):
            return False
        return True

    async def list_configs(
        self,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        compile_name: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        fresh: bool = False,
        auto_update: bool | None = None,
        after_key: CompileConfigKey | None = None,
    ) -> AsyncIterator[tuple[str, str, dict]]:
        if limit is not None and limit <= 0:
            return
        if account_id is not None and user_id is not None and compile_name and cursor is None:
            if after_key is not None and (account_id, compile_name, user_id) <= after_key:
                return
            if fresh:
                try:
                    await self._agfs.stat(
                        self._record_path(account_id, user_id, compile_name), bypass_cache=True
                    )
                except (AGFSNotFoundError, FileNotFoundError):
                    return
            record = await self.get(account_id, user_id, compile_name)
            if record is not None and (
                auto_update is None or record.get("auto_update") is auto_update
            ):
                yield account_id, compile_name, record
            return
        accounts = [account_id] if account_id is not None else sorted(await self.list_accounts())
        for account in accounts:
            if after_key is not None and account < after_key[0]:
                continue
            async for name, record in self.iter_records(
                account,
                user_id=user_id,
                cursor=cursor,
                compile_name=compile_name,
                after_key=after_key,
            ):
                if auto_update is not None and record.get("auto_update") is not auto_update:
                    continue
                yield account, name, record
                if limit is not None:
                    limit -= 1
                    if limit == 0:
                        return

    async def create_run(
        self, account_id: str, owner_user_id: str, compile_name: str, run: dict
    ) -> bool:
        path = self._run_path(account_id, owner_user_id, compile_name, run["task_id"])
        async with self.locked(path) as lease:
            if await self.read_json(path) is not None:
                return False
            await self.write_json(path, run, lease)
        return True

    async def update_run(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        task_id: str,
        changes: dict,
        expected: Optional[Mapping[str, Any]] = None,
        replace: bool = False,
    ) -> bool:
        if replace and RUN_FIELDS - changes.keys():
            raise InvalidArgumentError("Compile run replacement requires a complete record")
        path = self._run_path(account_id, owner_user_id, compile_name, task_id)
        async with self.locked(path) as lease:
            if replace and expected is None:
                try:
                    await self._agfs.stat(path, bypass_cache=True)
                except (AGFSNotFoundError, FileNotFoundError):
                    return False
                run = changes
            else:
                run = await self.read_json(path)
                if run is None or (
                    expected is not None and any(run.get(k) != v for k, v in expected.items())
                ):
                    return False
                if replace:
                    run = changes
                else:
                    run.update(changes)
            if changes.get("task_id", task_id) != task_id:
                raise InvalidArgumentError("Compile run task ID cannot change")
            await self.write_json(path, run, lease)
        return True

    async def delete_run(
        self,
        account_id: str,
        owner_user_id: str,
        compile_name: str,
        task_id: str,
        expected: Mapping[str, Any],
    ) -> bool:
        path = self._run_path(account_id, owner_user_id, compile_name, task_id)
        async with self.locked(path) as lease:
            run = await self.read_json(path)
            if run is None or any(run.get(k) != v for k, v in expected.items()):
                return False
            return await self._delete_file(path, lease)

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

    def _runs_dir(self, account_id: str, owner_user_id: str, compile_name: str) -> str:
        if owner_user_id is None:
            raise InvalidArgumentError("user_id must be a string.")
        return f"{self.root(account_id, owner_user_id)}/{self._name_segment(compile_name)}/runs"

    def _run_path(
        self, account_id: str, owner_user_id: str, compile_name: str, task_id: str
    ) -> str:
        return f"{self._runs_dir(account_id, owner_user_id, compile_name)}/{validate_task_id(task_id)}.json"

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
            record["task_first_create_time"] = time.time()
            await self.write_json(path, record, lease)
        return compile_name

    def lock_run(self, account_id: str, owner_user_id: str, compile_name: str, task_id: str) -> Any:
        # Keep the finalization mutex compatible with existing File workers.
        return self.locked(
            self._run_path(account_id, owner_user_id, compile_name, task_id) + ".finish"
        )

    async def get(self, account_id: str, owner_user_id: str, compile_name: str) -> Optional[dict]:
        return await self.read_json(self._record_path(account_id, owner_user_id, compile_name))

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
            original_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
            yield record
            if set(record) != STATE_FIELDS:
                raise InvalidArgumentError(
                    "Compile state fields do not match the configuration schema"
                )
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
        after_key: CompileConfigKey | None = None,
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
                if after_key is not None and (account_id, name, owner_user_id) <= after_key:
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
        account_id: str | None = None,
        owner_user_id: str | None = None,
        compile_name: str | None = None,
        task_id: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        keys_only: bool = False,
        finished_before: float | None = None,
        statuses: frozenset[str] | None = None,
        after_key: tuple[float, int, str] | None = None,
    ) -> list[dict]:
        """List history in descending (created_at, task_id) order."""
        if account_id is None or owner_user_id is None or compile_name is None:
            raise InvalidArgumentError("A Compile configuration is required for File run queries")
        if finished_before is not None or statuses is not None or after_key is not None:
            raise InvalidArgumentError("File runs do not support cross-configuration query filters")
        if limit is not None and limit <= 0:
            return []
        if task_id is not None:
            run = await self.get_run(account_id, owner_user_id, compile_name, task_id)
            return [run] if run is not None else []
        directory = self._runs_dir(account_id, owner_user_id, compile_name)
        try:
            entries = await self._agfs.ls(directory)
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        if keys_only:
            keys = []
            for entry in entries:
                filename = str(entry.get("name") or "")
                if not filename.endswith(".json") or entry.get("isDir", entry.get("is_dir", False)):
                    continue
                try:
                    candidate = validate_task_id(filename[:-5])
                except InvalidArgumentError:
                    continue
                keys.append({"task_id": candidate})
            return keys[:limit]
        before = None
        cursor_name = None
        if cursor:
            run = await self.get_run(account_id, owner_user_id, compile_name, cursor)
            if run is None:
                raise InvalidArgumentError("Run cursor does not exist")
            before = run["created_at"], run["task_id"]
            cursor_name = f"{cursor}.json"
            del run
        if limit is not None and limit <= 0:
            return []

        candidates: list[tuple[float, str, dict]] = []
        for entry in entries:
            name = str(entry.get("name") or "")
            if name == cursor_name or not name.endswith(".json"):
                continue
            run = await self.read_json(f"{directory}/{name}")
            if run is None:
                continue
            key = run["created_at"], run["task_id"]
            if before is not None and key >= before:
                continue
            candidate = (*key, run)
            if limit is None:
                candidates.append(candidate)
            elif len(candidates) < limit:
                heapq.heappush(candidates, candidate)
            else:
                heapq.heappushpop(candidates, candidate)
        candidates.sort(reverse=True)
        return [run for _, _, run in candidates]


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
        self.creation_guard: Callable[[str, str], Awaitable[bool]] | None = None
        self._cleanup_scan: Optional[AsyncIterator[tuple[str, str, str, str]]] = None
        self._cleanup_lock = asyncio.Lock()

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

    def _lock_run(
        self, account_id: str, owner_user_id: str, compile_name: str, task_id: str
    ) -> Any:
        if isinstance(self._store, CompileStateStore):
            return self._store.lock_run(account_id, owner_user_id, compile_name, task_id)
        return self._lock_operation(
            f"/local/{account_id}/_system/task_locks/compile_run/"
            f"{owner_user_id}/{hashlib.sha256(compile_name.encode()).hexdigest()}/"
            f"{validate_task_id(task_id)}"
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
                    key: original[key] for key in changes.keys() | {"task_first_create_time"}
                },
            ):
                raise ConflictError("Compile configuration changed during update")

    async def get_run(
        self, account_id: str, owner_user_id: str, compile_name: str, task_id: str
    ) -> Optional[dict]:
        runs = await self._store.list_runs(account_id, owner_user_id, compile_name, task_id=task_id)
        return runs[0] if runs else None

    async def cancel_queued_run(self, task: TaskRecord) -> None:
        meta = (task.meta.get("request") or {}).get("compile") or {}
        name, owner = meta.get("compile_name"), meta.get("owner_user_id")
        if not name or not owner or task.account_id is None:
            return
        await self._store.update_run(
            task.account_id,
            owner,
            name,
            task.task_id,
            {"status": "cancelled", "finished_at": task.updated_at},
            expected={"status": "queued", "started_at": None},
        )

    async def _history_candidates(
        self, cutoff: float, page_size: int
    ) -> AsyncIterator[tuple[str, str, str, str]]:
        if isinstance(self._store, CompileStateStore):
            # AGFS lists the whole directory; retain that listing across cleanup ticks.
            async for account, name, record in self._store.list_configs():
                owner = record["execution_context"]["owner_user_id"]
                for row in await self._store.list_runs(account, owner, name, keys_only=True):
                    yield account, owner, name, row["task_id"]
            return
        after_key = None
        while True:
            rows = await self._store.list_runs(
                keys_only=True,
                finished_before=cutoff,
                statuses=TERMINAL,
                limit=page_size,
                after_key=after_key,
            )
            for row in rows:
                yield row["account_id"], row["owner_user_id"], row["compile_name"], row["task_id"]
            if len(rows) < page_size:
                return
            last = rows[-1]
            after_key = (last["finished_at"], last["config_id"], last["task_id"])

    async def delete_expired(self, now: float, limit: int) -> int:
        """Check at most limit runs and retain those still needed by a durable Task."""
        from openviking.service.task_tracker import get_task_tracker

        if limit <= 0:
            return 0
        removed = 0
        cutoff = now - HISTORY_RETENTION_SECONDS
        async with self._cleanup_lock:
            if self._cleanup_scan is None:
                self._cleanup_scan = self._history_candidates(cutoff=cutoff, page_size=limit)
            for _ in range(limit):
                try:
                    scope = await anext(self._cleanup_scan)
                except StopAsyncIteration:
                    self._cleanup_scan = None
                    break
                async with self._lock_run(*scope):
                    run = await self.get_run(*scope)
                    if run is None:
                        continue
                    finished = run.get("finished_at")
                    if (
                        run["status"] not in TERMINAL
                        or isinstance(finished, bool)
                        or not isinstance(finished, (int, float))
                        or not math.isfinite(finished)
                        or finished < 0
                        or finished >= cutoff
                    ):
                        continue
                    task = await get_task_tracker().get_durable_task(
                        scope[3], account_id=scope[0], user_id=run["actor"]
                    )
                    if task is not None and task.task_type == "compile" and not task.is_terminal():
                        continue
                    if await self._store.delete_run(
                        *scope,
                        expected={key: run[key] for key in ("status", "finished_at", "actor")},
                    ):
                        removed += 1
        return removed

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

    def _settled_outcome(self, task: Any) -> bool:
        checkpoint = self._checkpoint(task)
        return (
            checkpoint.get("phase") in {"finished", "rollback_exhausted"}
            and (checkpoint.get("outcome") or {}).get("status") in TERMINAL
        )

    def _published_run_pending(self, task: Any, run: dict) -> bool:
        """Recognize terminal Runs written before durable Task outcomes."""
        checkpoint = self._checkpoint(task)
        return (
            run["status"] in TERMINAL
            and run.get("finished_at") is not None
            and not checkpoint.get("run_outcome_pending")
            and not checkpoint.get("progress_rollback")
        )

    def _run_outcome(self, task: Any, run: dict) -> dict:
        keys = ("status", "result", "error", "started_at", "finished_at")
        return {key: run[key] for key in keys}

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
        compile_name = self._compile_name(task)
        if compile_name:
            run = await self.get_run(
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
            if not await self._store.update_run(
                task.account_id,
                self._owner_user_id(task),
                compile_name,
                task.task_id,
                run,
                replace=True,
            ):
                raise NotFoundError(task.task_id, "compile run")
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

    async def record_queued(self, task: Any, locked_record: Optional[dict] = None) -> None:
        """Record acceptance idempotently before dispatch, marking the first run as initial."""
        compile_name = self._compile_name(task)
        if not compile_name:
            return
        if locked_record is None:
            async with self.edit_config(
                task.account_id, self._owner_user_id(task), compile_name
            ) as record:
                await self._ensure_queued_run(task, compile_name, record)
            return
        await self._ensure_queued_run(task, compile_name, locked_record)

    async def _ensure_queued_run(self, task: Any, compile_name: str, locked_record: dict) -> dict:
        """Return the persisted run, repairing missing registration under the caller's config lock."""
        run = await self.get_run(
            task.account_id, self._owner_user_id(task), compile_name, task.task_id
        )
        if locked_record["last_run_time"] is None:
            # A persisted initial run survives a failed state.json acceptance write.
            history = await self._store.list_runs(
                task.account_id, self._owner_user_id(task), compile_name
            )
            initial = next(
                (item for item in reversed(history) if item["meta"]["trigger"] == "initial"),
                None,
            )
            if initial is not None:
                locked_record["last_run_time"] = initial["created_at"]
        if run is None:
            run = new_run(task)
            if locked_record["last_run_time"] is None:
                run["meta"]["trigger"] = "initial"
            if not await self._store.create_run(
                task.account_id, self._owner_user_id(task), compile_name, run
            ):
                existing = await self.get_run(
                    task.account_id, self._owner_user_id(task), compile_name, task.task_id
                )
                if existing is None:
                    raise NotFoundError(task.task_id, "compile run")
                run = existing
        locked_record["last_run_time"] = max(
            locked_record["last_run_time"] or task.created_at, task.created_at
        )
        return run

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

        current = await get_task_tracker().get_durable_task(
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
                        run = await self._ensure_queued_run(task, compile_name, record)
                else:
                    run = await self._read_run(task)
                if self._settled_outcome(task) or self._published_run_pending(task, run):
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
        run["started_at"] = run["started_at"] or time.time()
        # Snapshot failures must retain the start time for terminal run publication.
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
        """Settle a terminal execution even if its configuration and history were deleted."""
        try:
            return await self._finish_with_retries(task, snapshot)
        except NotFoundError as exc:
            name = self._compile_name(task)
            if (
                name
                and snapshot.status in TERMINAL
                and (exc.details.get("type"), exc.details.get("resource"))
                in {("compile configuration", name), ("compile run", task.task_id)}
                and await self.get_config(
                    task.account_id, self._owner_user_id(task), name, fresh=True
                )
                is None
                and await self.get_run(
                    task.account_id, self._owner_user_id(task), name, task.task_id
                )
                is None
            ):
                # Removing configuration/history does not revoke Runtime's outcome or
                # remove its outputs. The caller still settles Task, Work, and admission.
                if self._settled_outcome(task):
                    return self._finished_snapshot(
                        self._checkpoint(task)["outcome"], snapshot.error_code
                    )
                return snapshot
            raise

    async def _finish_with_retries(
        self, task: Any, snapshot: ExternalTaskSnapshot
    ) -> ExternalTaskSnapshot:
        """Finalize the run, allowing submit failures to settle after lock retries."""
        name = self._compile_name(task)
        scope = (task.account_id, self._owner_user_id(task), name, task.task_id) if name else ()
        skip_rollback = snapshot.stage == SUBMIT_EXHAUSTED and "external_task_id" not in task.auth
        retries = task.meta.get("rollback_lock_retries", 0)
        if (
            not skip_rollback
            and scope
            and isinstance(retries, int)
            and retries >= ROLLBACK_LOCK_MAX_RETRIES
        ):
            await self._refresh_checkpoint(task)
            if self._checkpoint(task).get("phase") == "rollback_exhausted":
                return await self._fail_blocked_rollback(task, snapshot, scope)
        submission_retry_delays = iter((1, 3))
        while True:
            try:
                async with self._admission(task):
                    await self._refresh_checkpoint(task)
                    # Early cancellation can skip run registration.
                    if "baseline" not in self._checkpoint(task):
                        await self.record_queued(task)
                    async with self._lock_run(*scope) if scope else nullcontext():
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
                    await self._refresh_checkpoint(task)
                    if scope:
                        await self._save_checkpoint(task, {"phase": "rollback_exhausted"})
                    return await self._fail_blocked_rollback(task, snapshot, scope)
                update = {"rollback_lock_retries": retries + 1}
                await self._patch_task(task, TaskPatch(meta=update))
                task.meta.update(update)
                await asyncio.sleep(delay)
                return ExternalTaskSnapshot(status="cancelling", stage="cancelling")

    async def _fail_blocked_rollback(
        self, task: Any, snapshot: ExternalTaskSnapshot, scope: tuple
    ) -> ExternalTaskSnapshot:
        """Persist the exhausted rollback outcome before allowing the Task to fail."""

        async def save_outcome() -> ExternalTaskSnapshot:
            await self._refresh_checkpoint(task)
            run = await self.get_run(*scope) if scope else await self._read_run(task)
            missing = run is None
            if missing:
                if scope and await self.get_config(*scope[:3], fresh=True) is None:
                    raise NotFoundError(scope[2], "compile configuration")
                run = new_run(task)
            if self._settled_outcome(task):
                return self._finished_snapshot(run, snapshot.error_code)
            message = f"Compile rollback incomplete after {ROLLBACK_LOCK_MAX_RETRIES} lock retries"
            if snapshot.error_message:
                message += f": {snapshot.error_message}"
            result = {
                "rollback_incomplete": True,
                "start_snapshot": run["meta"]["start_snapshot"],
            }
            run.update(status="failed", result=result, error=message, finished_at=time.time())
            run["error"] = sanitize_error(message)
            if not missing or not await self._store.create_run(*scope[:3], run):
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

        if not scope:
            return await save_outcome()
        try:
            async with self._lock_run(*scope):
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
        if self._published_run_pending(task, run):
            # Old terminal Runs can precede Task settlement. Replay pending progress
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
                checkpoint: dict[str, Any] = {
                    "phase": "committing",
                    "run_outcome_pending": True,
                }
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
                        task,
                        {
                            "phase": "rolling_back",
                            "terminal_status": snapshot.status,
                            "run_outcome_pending": True,
                        },
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
        # Publish the terminal Run after progress settles so recovery can still cancel
        # and compensate a baseline whose checkpoint write was interrupted.
        await self._finish_progress(task, run)
        run["finished_at"] = time.time()
        await self._save_run(task, run)
        await self._save_checkpoint(
            task,
            {
                "outcome": self._run_outcome(task, run),
                "run_outcome_pending": False,
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
            # A saved Run can still settle if there is no config to update.
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
            if execution_signature(record) != task.meta["request"]["compile"]["input_signature"]:
                return
            state = self._progress_state(record)
            generation = record["task_first_create_time"]
            undo = checkpoint.get("progress_rollback")
            if undo:
                # Replays reuse the first undo record, even after a config write succeeded.
                if undo["task_first_create_time"] != generation or same_json_value(
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
                            "task_first_create_time": generation,
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
                record["task_first_create_time"] == undo["task_first_create_time"]
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
