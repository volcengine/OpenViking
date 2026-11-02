# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch store kept in ``viking://resources/.watch_tasks.json``.

The task snapshot is read once at startup and reads are answered from memory.
Run occupancy belongs to this process and never writes to disk. Task changes
and settlement publish a full snapshot with tmp/backup rotation before updating
the cache. Every operation runs under one in-process lock.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from heapq import nlargest
from typing import Any, Iterable, Mapping, Optional, Sequence

from openviking.concurrency import AsyncSemaphore
from openviking.resource.watch_pagination import WatchPageQuery
from openviking.resource.watch_storage import (
    WATCH_TASK_STORAGE_BAK_URI,
    WATCH_TASK_STORAGE_TMP_URI,
    WATCH_TASK_STORAGE_URI,
)
from openviking.resource.watch_store import (
    WatchListPage,
    WatchMoveResult,
    WatchStore,
    is_connector,
    parent_uri,
    target_conflict_message,
)
from openviking.resource.watch_types import (
    PAYLOAD_FIELDS,
    PRIVATE_PATCH_FIELDS,
    PermissionDeniedError,
    WatchAuthority,
    WatchCandidate,
    WatchPatch,
    WatchRun,
    WatchRunning,
    WatchRunResult,
    WatchState,
    WatchTask,
    uri_matches_prefix,
)
from openviking_cli.exceptions import ConflictError, NotFoundError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Row:
    task: WatchTask
    current_run_id: Optional[str] = None
    run_started_at: Optional[float] = None
    deleted: bool = False

    def to_storage_dict(self) -> dict[str, Any]:
        data = self.task.to_storage_dict()
        data.pop("run_source_checkpoint_version", None)
        return data

    @classmethod
    def from_storage_dict(cls, data: dict[str, Any]) -> "_Row":
        return cls(task=WatchTask.from_dict({**data, "run_source_checkpoint_version": None}))


def _changed(task: WatchTask, **values: Any) -> WatchTask:
    return WatchTask.from_dict({**task.to_storage_dict(), **values})


def _due(task: WatchTask, now: datetime) -> bool:
    return task.next_execution_time is not None and task.next_execution_time <= now


class FileWatchStore(WatchStore):
    """Returned tasks are the cached objects and must be treated as read-only."""

    STORAGE_URI = WATCH_TASK_STORAGE_URI
    STORAGE_BAK_URI = WATCH_TASK_STORAGE_BAK_URI
    STORAGE_TMP_URI = WATCH_TASK_STORAGE_TMP_URI

    def __init__(self, viking_fs: Any) -> None:
        if viking_fs is None:
            raise ValueError("FileWatchStore requires a VikingFS")
        self._fs = viking_fs
        self._rows: dict[str, _Row] = {}
        # (account_id, to_uri) -> live task IDs, and the occupied task IDs.
        self._targets: dict[tuple[str, str], set[str]] = {}
        self._running: set[str] = set()
        self._lock = AsyncSemaphore()
        self._initialized = False

    @property
    def provider(self) -> str:
        return "file"

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            await self._load()
            self._initialized = True
            logger.info("[FileWatchStore] Initialized with %d tasks", len(self._rows))

    async def close(self) -> None:
        return None

    # ── Persistence ─────────────────────────────────────────────────────

    @staticmethod
    def _ctx() -> Any:
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        return RequestContext(user=UserIdentifier.the_default_user(), role=Role(Role.ROOT))

    async def _read(self, uri: str) -> Optional[dict[str, Any]]:
        try:
            content = await self._fs.read_file(uri, ctx=self._ctx())
        except NotFoundError:
            return None
        except Exception as exc:
            logger.warning("[FileWatchStore] Failed to read %s: %s", uri, exc)
            return None
        if not content or not content.strip():
            return None
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("storage must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[FileWatchStore] Invalid %s JSON: %s", uri, exc)
            return None
        return data

    async def _load(self) -> None:
        data = await self._read(self.STORAGE_URI)
        recovered = data is None and (data := await self._read(self.STORAGE_BAK_URI)) is not None
        normalized = recovered
        rows: dict[str, _Row] = {}
        for item in (data or {}).get("tasks", []):
            try:
                if item.get("deleted"):
                    normalized = True
                    continue
                if any(
                    field in item
                    for field in (
                        "current_run_id",
                        "run_started_at",
                        "run_source_checkpoint_version",
                    )
                ):
                    normalized = True
                row = _Row.from_storage_dict(item)
            except Exception as exc:
                task_id = item.get("task_id") if isinstance(item, dict) else None
                logger.warning("[FileWatchStore] Failed to load task %s: %s", task_id, exc)
                continue
            task = row.task
            if not task.is_active:
                if task.next_execution_time is not None:
                    task.next_execution_time = None
                    normalized = True
            elif task.watch_interval <= 0:
                task.is_active = False
                task.next_execution_time = None
                normalized = True
            elif task.next_execution_time is None:
                task.next_execution_time = task.calculate_next_execution_time()
                normalized = True
            rows[task.task_id] = row
        if normalized:
            await self._save(rows)
        self._rows = {}
        self._index(rows)

    async def _save(self, rows: dict[str, _Row]) -> None:
        await self._write_document(
            self.STORAGE_URI,
            self.STORAGE_TMP_URI,
            self.STORAGE_BAK_URI,
            {
                "tasks": [row.to_storage_dict() for row in rows.values() if not row.deleted],
                "updated_at": datetime.now().isoformat(),
            },
            indent=2,
        )

    async def _write_document(
        self,
        uri: str,
        tmp_uri: str,
        bak_uri: str,
        data: dict[str, Any],
        *,
        indent: Optional[int] = None,
    ) -> None:
        # Every write happens under self._lock and users cannot reach these
        # control URIs, so the file needs no PathLock; taking one would collide
        # with a caller's Tree lock on the resources root.
        ctx = self._ctx()
        content = json.dumps(data, ensure_ascii=False, indent=indent)
        await self._fs.write_file(tmp_uri, content, ctx=ctx, auto_pathlock=False)
        try:
            if await self._fs.exists(uri, ctx=ctx):
                try:
                    if await self._fs.exists(bak_uri, ctx=ctx):
                        await self._fs.rm(bak_uri, ctx=ctx, auto_pathlock=False)
                except Exception as exc:
                    # Rename can replace the backup even if its removal failed.
                    logger.warning("[FileWatchStore] Failed to remove %s backup: %s", bak_uri, exc)
                await self._rename(uri, bak_uri)
        except Exception as exc:
            logger.warning("[FileWatchStore] Failed to rotate %s backup: %s", uri, exc)
        await self._rename(tmp_uri, uri)

    async def _rename(self, old_uri: str, new_uri: str) -> None:
        ctx = self._ctx()
        await self._fs._async_agfs.mv(
            self._fs._uri_to_path(old_uri, ctx=ctx),
            self._fs._uri_to_path(new_uri, ctx=ctx),
            auto_pathlock=False,
        )

    async def _commit(self, updates: dict[str, Optional[_Row]], *, persist: bool = True) -> None:
        """Apply row updates (None removes a row) once the file holds them."""
        updates = {
            task_id: row for task_id, row in updates.items() if row != self._rows.get(task_id)
        }
        if not updates:
            return
        if persist:
            rows = dict(self._rows)
            for task_id, row in updates.items():
                if row is None:
                    rows.pop(task_id, None)
                else:
                    rows[task_id] = row
            await self._save(rows)
        self._index(updates)

    def _index(self, updates: Mapping[str, Optional[_Row]]) -> None:
        for task_id, row in updates.items():
            old = self._rows.get(task_id)
            if old is not None:
                key = (old.task.account_id, old.task.to_uri or "")
                ids = self._targets.get(key)
                if ids is not None:
                    ids.discard(task_id)
                    if not ids:
                        del self._targets[key]
            self._running.discard(task_id)
            if row is None:
                self._rows.pop(task_id, None)
                continue
            self._rows[task_id] = row
            if not row.deleted and row.task.to_uri:
                key = (row.task.account_id, row.task.to_uri)
                self._targets.setdefault(key, set()).add(task_id)
            if row.run_started_at is not None:
                self._running.add(task_id)

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("FileWatchStore must be initialized")

    # ── Row helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _allowed(row: _Row, authority: WatchAuthority) -> bool:
        return authority.root or (
            row.task.account_id == authority.account_id and row.task.user_id == authority.user_id
        )

    @staticmethod
    def _state(row: _Row, now: float) -> WatchState:
        return WatchState(
            task=row.task.model_copy(deep=True),
            current_run_id=row.current_run_id,
            run_started_at=row.run_started_at,
            deleted=row.deleted,
            now=now,
        )

    def _on_target(self, account_id: str, uri: Optional[str]) -> list[_Row]:
        if not uri:
            return []
        return [self._rows[task_id] for task_id in self._targets.get((account_id, uri), ())]

    @staticmethod
    def _settled(row: _Row) -> Optional[_Row]:
        """The row to keep after a change; an idle tombstone is purged."""
        return None if row.deleted and row.run_started_at is None else row

    # ── API reads and writes ────────────────────────────────────────────

    async def get_state(self, task_id: str) -> Optional[WatchState]:
        self._require_initialized()
        async with self._lock:
            row = self._rows.get(task_id)
            return self._state(row, time.time()) if row else None

    async def get_task(
        self, task_id: str, authority: WatchAuthority = WatchAuthority()
    ) -> Optional[WatchTask]:
        self._require_initialized()
        async with self._lock:
            row = self._rows.get(task_id)
            if row is None or row.deleted or not self._allowed(row, authority):
                return None
            return row.task

    async def list_tasks(
        self,
        authority: WatchAuthority,
        *,
        active_only: bool = False,
        scope_account_id: Optional[str] = None,
        target_uri: Optional[str] = None,
    ) -> list[WatchTask]:
        self._require_initialized()
        async with self._lock:
            if target_uri is not None and scope_account_id is not None:
                candidates: Iterable[_Row] = self._on_target(scope_account_id, target_uri)
            else:
                candidates = self._rows.values()
            root = authority.root
            tasks = []
            for row in candidates:
                task = row.task
                if (
                    not row.deleted
                    and (
                        root
                        or (
                            task.account_id == authority.account_id
                            and task.user_id == authority.user_id
                        )
                    )
                    and (not active_only or task.is_active)
                    and (scope_account_id is None or task.account_id == scope_account_id)
                    and (target_uri is None or task.to_uri == target_uri)
                ):
                    tasks.append(task)
            return tasks

    async def list_tasks_page(
        self,
        authority: WatchAuthority,
        *,
        active_only: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> WatchListPage:
        self._require_initialized()
        query = WatchPageQuery(
            authority, active_only=active_only, limit=limit, cursor=cursor, store_scope="file"
        )
        async with self._lock:
            total = 0

            def candidates() -> Iterable[WatchTask]:
                nonlocal total
                for row in self._rows.values():
                    if (
                        row.deleted
                        or not self._allowed(row, authority)
                        or (active_only and not row.task.is_active)
                    ):
                        continue
                    total += 1
                    task = row.task
                    if (
                        query.after is None
                        or (task.created_at.timestamp(), task.task_id) < query.after
                    ):
                        yield task

            tasks = nlargest(
                limit + 1,
                candidates(),
                key=lambda task: (task.created_at.timestamp(), task.task_id),
            )
            has_more = len(tasks) > limit
            tasks = tasks[:limit]
            return WatchListPage(
                tasks=[task.to_dict() for task in tasks],
                total=total,
                next_cursor=query.next_cursor((tasks[-1].created_at.timestamp(), tasks[-1].task_id))
                if has_more
                else None,
            )

    async def create_task(self, task: WatchTask, *, initial_run_id: Optional[str] = None) -> None:
        self._require_initialized()
        async with self._lock:
            others = self._on_target(task.account_id, task.to_uri)
            if others and not (
                is_connector(task.auth_state)
                and all(is_connector(r.task.auth_state) for r in others)
            ):
                raise ConflictError(target_conflict_message(task.to_uri), resource=task.to_uri)
            if task.task_id in self._rows:
                raise ConflictError(f"Watch '{task.task_id}' already exists", resource=task.task_id)
            row = _Row(
                task=task.model_copy(
                    deep=True,
                    update={
                        "run_source_checkpoint_version": (
                            task.source_checkpoint_version if initial_run_id else None
                        )
                    },
                ),
                current_run_id=initial_run_id,
                run_started_at=time.time() if initial_run_id else None,
            )
            await self._commit({task.task_id: row})

    async def patch_task(
        self,
        task_id: str,
        patch: WatchPatch,
        authority: WatchAuthority = WatchAuthority(),
        *,
        run_id: Optional[str] = None,
    ) -> Optional[WatchTask]:
        values = patch.normalized()
        self._require_initialized()
        async with self._lock:
            row = self._rows.get(task_id)
            if row is None:
                return None
            matches = self._allowed(row, authority) and (
                not row.deleted if run_id is None else row.current_run_id == run_id
            )
            if not matches:
                if run_id is None and not row.deleted:
                    raise PermissionDeniedError(f"No permission to update Watch {task_id}")
                return None
            task = row.task
            values = patch.resolve(task)
            changes = {
                name: values[name]
                for name in (PAYLOAD_FIELDS | PRIVATE_PATCH_FIELDS) & values.keys()
            }
            for name in ("auth_state", "connector_states"):
                if name in values:
                    changes[name] = values[name]
            if "is_active" in values or "watch_interval" in values:
                enabled = bool(values.get("is_active", task.is_active))
                interval = float(values.get("watch_interval", task.watch_interval))
                next_run = task.next_execution_time
                if "watch_interval" in values or next_run is None:
                    base = task.last_execution_time or task.created_at
                    next_run = base + timedelta(minutes=interval)
                changes.update(
                    is_active=enabled,
                    watch_interval=interval,
                    next_execution_time=next_run if enabled else None,
                )
            if changes:
                updated = _changed(task, **changes)
                if updated != task:
                    await self._commit({task_id: replace(row, task=updated)})
                    task = updated
            return task

    async def delete_task(self, task_id: str, authority: WatchAuthority) -> bool:
        self._require_initialized()
        async with self._lock:
            row = self._rows.get(task_id)
            if row is None or row.deleted:
                return False
            if not self._allowed(row, authority):
                raise PermissionDeniedError(f"No permission to delete Watch {task_id}")
            tombstone = replace(
                row,
                task=_changed(row.task, is_active=False, next_execution_time=None),
                deleted=True,
            )
            await self._commit({task_id: self._settled(tombstone)})
            return True

    async def delete_tasks(
        self, account_id: str, user_id: Optional[str] = None
    ) -> list[WatchState]:
        self._require_initialized()
        async with self._lock:
            now = time.time()
            updates: dict[str, Optional[_Row]] = {}
            for task_id, row in self._rows.items():
                if row.task.account_id != account_id or (
                    user_id is not None and row.task.user_id != user_id
                ):
                    continue
                if row.run_started_at is None:
                    updates[task_id] = None
                elif not row.deleted:
                    updates[task_id] = replace(
                        row,
                        task=_changed(row.task, is_active=False, next_execution_time=None),
                        deleted=True,
                    )
            await self._commit(updates)
            return [
                self._state(self._rows[task_id], now)
                for task_id in sorted(self._running)
                if self._rows[task_id].task.account_id == account_id
                and (user_id is None or self._rows[task_id].task.user_id == user_id)
            ]

    # ── Run occupancy ───────────────────────────────────────────────────

    @staticmethod
    def _candidate(row: _Row) -> WatchCandidate:
        task = row.task
        return WatchCandidate(
            task.task_id, task.account_id, task.user_id, task.to_uri, row.run_started_at
        )

    async def get_candidate(self, task_id: str) -> Optional[WatchCandidate]:
        self._require_initialized()
        async with self._lock:
            row = self._rows.get(task_id)
            return self._candidate(row) if row is not None and not row.deleted else None

    async def due_candidates(self, limit: int, *, offset: int = 0) -> list[WatchCandidate]:
        self._require_initialized()
        if limit <= 0:
            return []
        async with self._lock:
            now = datetime.now()
            candidates = []
            skipped = 0
            for row in self._rows.values():
                task = row.task
                next_run = task.next_execution_time
                if (
                    next_run is not None
                    and next_run <= now
                    and task.is_active
                    and not row.deleted
                    and row.run_started_at is None
                ):
                    if skipped < offset:
                        skipped += 1
                        continue
                    candidates.append(self._candidate(row))
                    if len(candidates) == limit:
                        break
            return candidates

    async def running(self, *, after: str = "", limit: int = 100) -> list[WatchRunning]:
        self._require_initialized()
        async with self._lock:
            now = time.time()
            ids = sorted(task_id for task_id in self._running if task_id > after)[:limit]
            result = []
            for task_id in ids:
                row = self._rows[task_id]
                assert row.run_started_at is not None
                result.append(
                    WatchRunning(
                        task_id=task_id,
                        account_id=row.task.account_id,
                        user_id=row.task.user_id,
                        current_run_id=row.current_run_id,
                        run_started_at=row.run_started_at,
                        now=now,
                        connector=is_connector(row.task.auth_state),
                        instruction=row.task.instruction,
                    )
                )
            return result

    async def begin_run(
        self,
        candidate: WatchCandidate,
        *,
        manual: bool = False,
    ) -> Optional[WatchRun]:
        self._require_initialized()
        async with self._lock:
            now = datetime.now()
            task_id = candidate.task_id
            row = self._rows.get(task_id)
            if (
                row is None
                or row.deleted
                or row.run_started_at is not None
                or row.task.to_uri != candidate.to_uri
                or row.task.account_id != candidate.account_id
                or row.task.user_id != candidate.user_id
                or (not manual and not (row.task.is_active and _due(row.task, now)))
            ):
                return None
            begun = replace(
                row,
                task=row.task.model_copy(
                    update={"run_source_checkpoint_version": row.task.source_checkpoint_version}
                ),
                run_started_at=now.timestamp(),
            )
            self._index({task_id: begun})
            return WatchRun(
                task_id=task_id,
                run_id=None,
                task=begun.task.model_copy(deep=True),
                started_at=begun.run_started_at,
            )

    async def attach_task(
        self,
        task_id: str,
        run_id: str,
        *,
        started_at: float,
    ) -> bool:
        self._require_initialized()
        async with self._lock:
            row = self._rows.get(task_id)
            if (
                row is None
                or row.deleted
                or row.current_run_id is not None
                or row.run_started_at != started_at
            ):
                return False
            self._index({task_id: replace(row, current_run_id=run_id)})
            return True

    async def finish_run(
        self,
        task_id: str,
        run_id: Optional[str],
        result: WatchRunResult,
        *,
        started_at: Optional[float] = None,
    ) -> bool:
        self._require_initialized()
        async with self._lock:
            row = self._rows.get(task_id)
            if (
                row is None
                or row.run_started_at is None
                or row.current_run_id != run_id
                or (started_at is not None and row.run_started_at != started_at)
            ):
                return False
            now = datetime.now()
            task = row.task
            enabled = task.is_active and not result.deactivate
            changes: dict[str, Any] = {
                "is_active": enabled,
                "next_execution_time": (
                    now + timedelta(minutes=task.watch_interval)
                    if enabled and not row.deleted
                    else None
                ),
                "last_execution_time": now,
                "last_task_id": result.execution_task_id,
                "last_status": result.status,
                "last_error": result.error,
            }
            if result.has_connector_states:
                changes["connector_states"] = result.connector_states
            if (
                result.source_checkpoint is not None
                and task.run_source_checkpoint_version == task.source_checkpoint_version
            ):
                changes["source_checkpoint"] = result.source_checkpoint
            changes["run_source_checkpoint_version"] = None
            settled = replace(
                row,
                task=_changed(task, **changes),
                current_run_id=None,
                run_started_at=None,
            )
            await self._commit({task_id: self._settled(settled)}, persist=not row.deleted)
            return True

    async def deactivate_idle(self, task_id: str, *, target_uri: Optional[str], error: str) -> bool:
        self._require_initialized()
        async with self._lock:
            row = self._rows.get(task_id)
            if (
                row is None
                or row.deleted
                or row.run_started_at is not None
                or row.task.to_uri != target_uri
            ):
                return False
            task = _changed(
                row.task,
                is_active=False,
                next_execution_time=None,
                last_execution_time=datetime.now(),
                last_task_id=None,
                last_status="failed",
                last_error=error,
            )
            await self._commit({task_id: replace(row, task=task)})
            return True

    async def has_running_overlap(self, account_id: str, uris: Sequence[str]) -> bool:
        uris = [uri for uri in uris if uri]
        self._require_initialized()
        async with self._lock:
            for task_id in self._running:
                task = self._rows[task_id].task
                if task.account_id != account_id or not task.to_uri:
                    continue
                if any(
                    uri_matches_prefix(task.to_uri, uri) or uri_matches_prefix(uri, task.to_uri)
                    for uri in uris
                ):
                    return True
            return False

    # ── Target transfers (caller holds the transfer's PathLock) ─────────

    def _plan_move(
        self,
        account_id: str,
        from_uri: str,
        to_uri: str,
        task_ids: Optional[Sequence[str]],
    ) -> list[tuple[_Row, str]]:
        old_prefix, new_prefix = from_uri.rstrip("/"), to_uri.rstrip("/")
        selected_ids = None if task_ids is None else set(task_ids)
        plan = [
            (row, new_prefix + row.task.to_uri[len(old_prefix) :])
            for row in self._rows.values()
            if not row.deleted
            and row.task.account_id == account_id
            and row.task.to_uri is not None
            and uri_matches_prefix(row.task.to_uri, old_prefix)
            and (selected_ids is None or row.task.task_id in selected_ids)
        ]
        if selected_ids is not None and {row.task.task_id for row, _ in plan} != selected_ids:
            raise ConflictError(
                "Watch move source changed before the operation completed",
                resource=from_uri,
            )
        moving = {row.task.task_id for row, _ in plan}
        landing: dict[str, list[_Row]] = {}
        for row, target in plan:
            landing.setdefault(target, []).append(row)
        for target, occupants in landing.items():
            occupants.extend(
                row for row in self._on_target(account_id, target) if row.task.task_id not in moving
            )
            if len(occupants) > 1 and not all(is_connector(r.task.auth_state) for r in occupants):
                raise ConflictError(target_conflict_message(target), resource=target)
        return plan

    async def move(
        self,
        *,
        account_id: str,
        from_uri: str,
        to_uri: str,
        task_ids: Optional[Sequence[str]] = None,
    ) -> WatchMoveResult:
        self._require_initialized()
        requested_ids = None if task_ids is None else tuple(dict.fromkeys(task_ids))
        if requested_ids == ():
            return WatchMoveResult(account_id, from_uri.rstrip("/"), to_uri.rstrip("/"), ())
        async with self._lock:
            updates: dict[str, Optional[_Row]] = {}
            moved_ids: list[str] = []
            for row, target in self._plan_move(account_id, from_uri, to_uri, requested_ids):
                changes: dict[str, Any] = {"to_uri": target}
                task = row.task
                if (
                    task.to_uri is not None
                    and task.parent_uri is not None
                    and task.parent_uri == parent_uri(task.to_uri)
                ):
                    changes["parent_uri"] = parent_uri(target)
                task = _changed(task, **changes)
                if task != row.task:
                    updates[task.task_id] = replace(row, task=task)
                moved_ids.append(task.task_id)
            await self._commit(updates)
            return WatchMoveResult(
                account_id,
                from_uri.rstrip("/"),
                to_uri.rstrip("/"),
                tuple(moved_ids),
            )

    async def deactivate_under_uri(self, account_id: str, uri: str) -> list[WatchTask]:
        self._require_initialized()
        async with self._lock:
            updates: dict[str, Optional[_Row]] = {}
            matched: list[WatchTask] = []
            for task_id, row in self._rows.items():
                if (
                    row.deleted
                    or row.task.account_id != account_id
                    or not uri_matches_prefix(row.task.to_uri, uri)
                ):
                    continue
                task = row.task
                if task.is_active or task.next_execution_time is not None:
                    task = _changed(task, is_active=False, next_execution_time=None)
                    updates[task_id] = replace(row, task=task)
                matched.append(task)
            await self._commit(updates)
            return matched
