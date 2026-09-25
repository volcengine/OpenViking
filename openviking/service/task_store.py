# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Internal storage backends for TaskTracker."""

from __future__ import annotations

import hashlib
import heapq
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol

from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.exceptions import (
    AGFSAlreadyExistsError,
    AGFSDirectoryNotEmptyError,
    AGFSNotFoundError,
)
from openviking.server.error_mapping import is_storage_not_found
from openviking.service.task_tracker_concurrency import StoreIOLimiter, run_to_completion
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime

SYSTEM_TASK_ACCOUNT_ID = "_system"
SYSTEM_TASK_USER_ID = "root"


class TaskStore(Protocol):
    async def create(self, task: Any) -> None: ...

    async def update(self, task: Any) -> None: ...

    async def get(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]: ...

    async def list(
        self, account_id: str, *, user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]: ...

    async def list_page(
        self,
        account_id: str,
        *,
        user_id: str,
        limit: int,
        before: tuple[float, str] | None = None,
        prune_expired: Callable[[Dict[str, Any]], Awaitable[bool]] | None = None,
        io_limiter: StoreIOLimiter | None = None,
        **filters: Any,
    ) -> List[Dict[str, Any]]: ...

    async def delete(
        self, task_id: str, *, account_id: str, user_id: Optional[str] = None
    ) -> None: ...


class PersistentTaskStore:
    """Persist task records into AGFS under account-scoped system task directories."""

    ROOT_PREFIX = "/local"
    SYSTEM_DIRNAME = "_system"
    TASKS_DIRNAME = "tasks"

    def __init__(self, agfs: Any) -> None:
        self._agfs = (
            agfs
            if isinstance(agfs, AsyncAGFSClient) or inspect.iscoroutinefunction(agfs.read)
            else AsyncAGFSClient(agfs)
        )
        self._ensured_task_dirs: set[tuple[str, str]] = set()

    @classmethod
    def schedule_root(cls, kind: str) -> str:
        if not kind or not kind.replace("_", "").isalnum():
            raise ValueError("Invalid scheduled task kind")
        return f"/local/{SYSTEM_TASK_ACCOUNT_ID}/_system/tasks/_scheduled/{kind}"

    @classmethod
    def schedule_path(cls, kind: str, key: str) -> str:
        digest = hashlib.sha256(key.encode()).hexdigest()
        return f"{cls.schedule_root(kind)}/records/{digest[:2]}/{digest}.json"

    @classmethod
    def _due_path(cls, item: dict) -> str:
        when = parse_iso_datetime(item["run_at"]).astimezone(timezone.utc)
        token = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        return f"{cls.schedule_root(item['kind'])}/due/{when:%Y/%m/%d/%H%M%S%f}-{token}.json"

    @asynccontextmanager
    async def _schedule_lock(self, kind: str, key: str):
        path = self.schedule_path(kind, key)
        await self._agfs.ensure_parent_dirs(path)
        lease = await self._agfs.pathlock_acquire_exact(path)
        try:
            yield {
                "account_id": SYSTEM_TASK_ACCOUNT_ID,
                "lease_ref": lease if isinstance(lease, str) else lease["lease_ref"],
            }
        finally:
            await self._agfs.pathlock_release(lease)

    async def get_scheduled(self, kind: str, key: str) -> dict | None:
        try:
            item = json.loads(_decode_bytes(await self._agfs.read(self.schedule_path(kind, key))))
        except Exception as exc:
            if is_storage_not_found(exc):
                return None
            raise
        if item.get("key") != key or item.get("kind") != kind:
            raise ValueError("Scheduled task key mismatch")
        return item

    async def _remove_schedule_file(self, path: str, *, fs_ctx: dict | None = None) -> None:
        try:
            await self._agfs.rm(path, auto_pathlock=False, fs_ctx=fs_ctx)
        except Exception as exc:
            if not is_storage_not_found(exc):
                raise

    async def _put_scheduled(self, item: dict, previous: dict | None, fs_ctx: dict) -> None:
        """Publish the recoverable due entry before updating the keyed projection."""
        due_path = self._due_path(item)
        payload = json.dumps(item, sort_keys=True).encode()
        await self._agfs.ensure_parent_dirs(due_path)
        await self._agfs.write(due_path, payload, auto_pathlock=False)
        await self._agfs.write(
            self.schedule_path(item["kind"], item["key"]),
            payload,
            auto_pathlock=False,
            fs_ctx=fs_ctx,
        )
        if previous is not None and self._due_path(previous) != due_path:
            await self._remove_schedule_file(self._due_path(previous))

    async def schedule(
        self,
        kind: str,
        key: str,
        *,
        payload: dict,
        run_at: str,
        condition: Callable[[dict | None], bool] | None = None,
        preserve: Callable[[dict], bool] | None = None,
    ) -> bool:
        """Schedule/revise work without placing future work in an immediate queue.

        Payloads contain business identity, not a second execution state machine;
        once dispatched, ordinary QueueFS and TaskTracker own execution.
        """
        async with self._schedule_lock(kind, key) as fs_ctx:
            current = await self.get_scheduled(kind, key)
            if condition is not None and not condition(current):
                return False
            if current is not None and preserve is not None and preserve(current):
                return True
            item = {
                "kind": kind,
                "key": key,
                "run_at": format_iso8601(parse_iso_datetime(run_at)),
                "payload": payload,
            }
            await self._put_scheduled(item, current, fs_ctx)
            return True

    async def cancel_scheduled(
        self, kind: str, key: str, *, condition: Callable[[dict], bool]
    ) -> bool:
        async with self._schedule_lock(kind, key) as fs_ctx:
            current = await self.get_scheduled(kind, key)
            if current is None or not condition(current):
                return False
            await self._remove_schedule_file(self._due_path(current))
            await self._remove_schedule_file(self.schedule_path(kind, key), fs_ctx=fs_ctx)
            return True

    async def claim_due(
        self,
        kind: str,
        *,
        now: datetime,
        limit: int = 100,
        max_bytes: int = 1_048_576,
        time_budget: float = 5.0,
        lease_seconds: float = 300.0,
    ):
        """Yield oldest due schedules in bounded pages, with durable claim leases.

        Calendar directories exclude future buckets without reading their records.
        A crash before enqueue leaves a leased entry which becomes due again.
        Cross-process claims serialize on the same keyed PathLock as revisions.
        """
        deadline = time.monotonic() + time_budget
        remaining = limit
        byte_count = 0
        cutoff = now.astimezone(timezone.utc).strftime("%Y/%m/%d/%H%M%S%f").split("/")

        async def walk(path: str, depth: int, prefix: tuple[str, ...] = ()):
            if time.monotonic() >= deadline:
                return
            try:
                entries = await self._agfs.ls(path, limit=limit, sort_by="name")
            except Exception as exc:
                if is_storage_not_found(exc):
                    return
                raise
            for entry in entries:
                if time.monotonic() >= deadline:
                    return
                name = str(entry.get("name") or "")
                part = name.split("-", 1)[0] if depth == 3 else name
                if not part.isdigit():
                    continue
                if (*prefix, part) > tuple(cutoff[: depth + 1]):
                    break
                child = f"{path}/{name}"
                if depth == 3:
                    if name.endswith(".json"):
                        yield child
                else:
                    async for candidate in walk(child, depth + 1, (*prefix, part)):
                        yield candidate
            # Prune empty calendar buckets so old dates cannot starve new work.
            if depth > 0:
                try:
                    await self._agfs.rm(path, auto_pathlock=False)
                except Exception as exc:
                    if not (
                        is_storage_not_found(exc)
                        or isinstance(exc, AGFSDirectoryNotEmptyError)
                    ):
                        raise

        async for path in walk(f"{self.schedule_root(kind)}/due", 0):
            if remaining <= 0 or byte_count >= max_bytes or time.monotonic() >= deadline:
                break
            try:
                raw = await self._agfs.read(path)
            except Exception as exc:
                if is_storage_not_found(exc):
                    continue
                raise
            byte_count += len(raw)
            if byte_count > max_bytes:
                break
            item = json.loads(_decode_bytes(raw))
            if item.get("kind") != kind or self._due_path(item) != path:
                raise ValueError(f"Invalid scheduled task index entry: {path}")
            async with self._schedule_lock(kind, item["key"]) as fs_ctx:
                current = await self.get_scheduled(kind, item["key"])
                if current is not None and self._due_path(current) != path:
                    await self._remove_schedule_file(path)
                    continue
                # A missing keyed projection is an interrupted first publication.
                leased = {**item, "run_at": format_iso8601(now + timedelta(seconds=lease_seconds))}
                await self._put_scheduled(leased, item, fs_ctx)
            remaining -= 1
            yield item["payload"]

    async def create(self, task: Any) -> None:
        await self._write_task(task)

    async def update(self, task: Any) -> None:
        await self._write_task(task)

    async def get(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not account_id or not user_id:
            return None
        path = self._task_path(account_id, user_id, task_id)
        try:
            raw = await self._agfs.read(path)
        except (AGFSNotFoundError, FileNotFoundError):
            return None
        return json.loads(_decode_bytes(raw))

    async def list(self, account_id: str, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if user_id is None:
            try:
                owners = await self._agfs.ls(self._task_root_dir(account_id))
            except (AGFSNotFoundError, FileNotFoundError):
                return []
            tasks: List[Dict[str, Any]] = []
            for owner in owners:
                if owner.get("isDir") and owner.get("name") not in (".", ".."):
                    tasks.extend(await self.list(account_id, user_id=owner["name"]))
            return tasks
        directory = self._task_dir(account_id, user_id)
        try:
            items = await self._agfs.ls(directory)
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        tasks: List[Dict[str, Any]] = []
        for item in items:
            path = item.get("path") or f"{directory}/{item.get('name', '')}"
            if not path.endswith(".json"):
                continue
            try:
                raw = await self._agfs.read(path)
            except (AGFSNotFoundError, FileNotFoundError):
                continue
            tasks.append(json.loads(_decode_bytes(raw)))
        return tasks

    async def list_page(
        self,
        account_id: str,
        *,
        user_id: str,
        limit: int,
        before: tuple[float, str] | None = None,
        prune_expired: Callable[[Dict[str, Any]], Awaitable[bool]] | None = None,
        io_limiter: StoreIOLimiter | None = None,
        **filters: Any,
    ) -> List[Dict[str, Any]]:
        """Scan durable records, retaining only the requested page in memory.

        AGFS has no indexed query API. This bounds record memory, but still
        performs O(n) reads; it deliberately does not load all tasks into the tracker.
        """
        from openviking.service.task_pagination import matches

        async def read(operation: str, factory: Callable[[], Awaitable[Any]]) -> Any:
            if io_limiter is None:
                return await factory()
            return await io_limiter.run(operation, lambda: run_to_completion(factory))

        directory = self._task_dir(account_id, user_id)
        try:
            entries = await read("list_page_ls", lambda: self._agfs.ls(directory))
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        heap: list = []
        for entry in entries:
            path = entry.get("path") or f"{directory}/{entry.get('name', '')}"
            if not path.endswith(".json"):
                continue
            try:
                task = json.loads(
                    _decode_bytes(
                        await read("list_page_read", lambda path=path: self._agfs.read(path))
                    )
                )
            except (AGFSNotFoundError, FileNotFoundError):
                continue
            if task.get("account_id") != account_id or task.get("user_id") != user_id:
                continue
            if prune_expired is not None and await prune_expired(task):
                continue
            key = (float(task["created_at"]), str(task["task_id"]))
            if before is not None and key >= before:
                continue
            if not matches(task, **filters):
                continue
            item = (*key, task)
            if len(heap) < limit:
                heapq.heappush(heap, item)
            elif key > heap[0][:2]:
                heapq.heapreplace(heap, item)
        return [item[2] for item in sorted(heap, reverse=True)]

    async def delete(self, task_id: str, *, account_id: str, user_id: Optional[str] = None) -> None:
        """Delete a task record, succeeding if it has already been removed."""
        if not user_id:
            return
        try:
            await self._agfs.rm(
                self._task_path(account_id, user_id, task_id),
                force=True,
                auto_pathlock=False,
            )
        except (AGFSNotFoundError, FileNotFoundError):
            return

    async def _write_task(self, task: Any) -> None:
        account_id = getattr(task, "account_id", None)
        user_id = getattr(task, "user_id", None)
        if not account_id or not user_id:
            raise ValueError("PersistentTaskStore requires account_id and user_id")
        await self._ensure_task_dir(account_id, user_id)
        path = self._task_path(account_id, user_id, task.task_id)
        payload = json.dumps(_task_to_payload(task), ensure_ascii=False).encode("utf-8")
        try:
            await self._write_task_payload(path, payload)
        except (AGFSNotFoundError, FileNotFoundError):
            self._ensured_task_dirs.discard((account_id, user_id))
            await self._ensure_task_dir(account_id, user_id)
            await self._write_task_payload(path, payload)

    async def _ensure_task_dir(self, account_id: str, user_id: str) -> None:
        cache_key = (account_id, user_id)
        if cache_key in self._ensured_task_dirs:
            return

        seen_paths: set[str] = set()
        for path in self._task_dir_chain(account_id, user_id):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            await self._mkdir_if_missing(path)
        self._ensured_task_dirs.add(cache_key)

    def _task_dir_chain(self, account_id: str, user_id: str) -> tuple[str, ...]:
        return (
            self._account_dir(account_id),
            self._system_dir(account_id),
            self._task_root_dir(account_id),
            self._task_dir(account_id, user_id),
        )

    async def _write_task_payload(self, path: str, payload: bytes) -> None:
        # TaskTracker is the owner of task mutations and serializes updates per
        # task. PersistentTaskStore does not implement store-level revision/CAS,
        # so AGFS pathlock cannot make multiple independent writers correct; it
        # only adds one storage lock around every task lifecycle write. Avoid
        # that extra PathLock overhead on this internal task file.
        await self._agfs.write(path, payload, auto_pathlock=False)

    async def _mkdir_if_missing(self, path: str) -> None:
        try:
            await self._agfs.mkdir(path)
        except AGFSAlreadyExistsError:
            return
        except Exception as exc:
            if "already exists" in str(exc).lower():
                return
            raise

    def _account_dir(self, account_id: str) -> str:
        return f"{self.ROOT_PREFIX}/{account_id}"

    def _system_dir(self, account_id: str) -> str:
        if account_id == SYSTEM_TASK_ACCOUNT_ID:
            return self._account_dir(account_id)
        return f"{self._account_dir(account_id)}/{self.SYSTEM_DIRNAME}"

    def _task_root_dir(self, account_id: str) -> str:
        return f"{self._system_dir(account_id)}/{self.TASKS_DIRNAME}"

    def _task_dir(self, account_id: str, user_id: str) -> str:
        return f"{self._task_root_dir(account_id)}/{user_id}"

    def _task_path(self, account_id: str, user_id: str, task_id: str) -> str:
        return f"{self._task_dir(account_id, user_id)}/{task_id}.json"


def _task_to_payload(task: Any) -> Dict[str, Any]:
    status = getattr(task, "status", None)
    return {
        **deepcopy(getattr(task, "_extra_fields", {})),
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": status.value if hasattr(status, "value") else status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "processing_seconds": task.processing_seconds,
        "resource_id": task.resource_id,
        "account_id": task.account_id,
        "user_id": task.user_id,
        "meta": deepcopy(task.meta),
        "stage": task.stage,
        "result": deepcopy(task.result),
        "error": task.error,
        "execution_events": deepcopy(task.execution_events),
        "auth": deepcopy(task.auth),
    }


def _decode_bytes(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)
