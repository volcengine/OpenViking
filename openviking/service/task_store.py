# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Internal storage backends for TaskTracker."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional, Protocol

from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.exceptions import AGFSAlreadyExistsError, AGFSNotFoundError
from openviking_cli.utils.logger import get_logger

SYSTEM_TASK_ACCOUNT_ID = "_system"
SYSTEM_TASK_USER_ID = "root"

logger = get_logger(__name__)


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

    async def delete(
        self, task_id: str, *, account_id: str, user_id: Optional[str] = None
    ) -> None: ...


class PersistentTaskStore:
    """Persist task records into AGFS under account-scoped system task directories."""

    ROOT_PREFIX = "/local"
    SYSTEM_DIRNAME = "_system"
    TASKS_DIRNAME = "tasks"

    def __init__(self, agfs: Any) -> None:
        self._agfs = agfs if isinstance(agfs, AsyncAGFSClient) else AsyncAGFSClient(agfs)
        self._ensured_task_dirs: set[tuple[str, str]] = set()

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
        if not user_id:
            return []
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

    async def delete(self, task_id: str, *, account_id: str, user_id: Optional[str] = None) -> None:
        if not user_id:
            return
        await self._agfs.rm(
            self._task_path(account_id, user_id, task_id),
            force=True,
            auto_pathlock=False,
        )

    async def _write_task(self, task: Any) -> None:
        account_id = getattr(task, "account_id", None)
        user_id = getattr(task, "user_id", None)
        if not account_id or not user_id:
            raise ValueError("PersistentTaskStore requires account_id and user_id")
        started_at = time.perf_counter()
        ensure_timings = await self._ensure_task_dir(account_id, user_id)
        after_ensure = time.perf_counter()
        path = self._task_path(account_id, user_id, task.task_id)
        payload = json.dumps(_task_to_payload(task), ensure_ascii=False).encode("utf-8")
        try:
            await self._write_task_payload(path, payload)
        except (AGFSNotFoundError, FileNotFoundError):
            self._ensured_task_dirs.discard((account_id, user_id))
            retry_started_at = time.perf_counter()
            retry_timings = await self._ensure_task_dir(account_id, user_id)
            ensure_timings.extend(
                (f"retry_{name}", duration_ms) for name, duration_ms in retry_timings
            )
            after_ensure = time.perf_counter()
            logger.warning(
                "[PersistentTaskStore] task dir cache refreshed after missing parent "
                "task_type=%s task_id=%s retry_ensure_ms=%.1f",
                getattr(task, "task_type", ""),
                getattr(task, "task_id", ""),
                (after_ensure - retry_started_at) * 1000,
            )
            await self._write_task_payload(path, payload)
        finished_at = time.perf_counter()
        total_ms = (finished_at - started_at) * 1000
        if total_ms >= 1000:
            logger.warning(
                "[PersistentTaskStore] slow write_task task_type=%s task_id=%s "
                "total_ms=%.1f ensure_dir_ms=%.1f write_ms=%.1f mkdir_steps=%s",
                getattr(task, "task_type", ""),
                getattr(task, "task_id", ""),
                total_ms,
                (after_ensure - started_at) * 1000,
                (finished_at - after_ensure) * 1000,
                ",".join(f"{name}:{duration_ms:.1f}" for name, duration_ms in ensure_timings),
            )

    async def _ensure_task_dir(self, account_id: str, user_id: str) -> List[tuple[str, float]]:
        cache_key = (account_id, user_id)
        if cache_key in self._ensured_task_dirs:
            return [("cache_hit", 0.0)]

        timings: List[tuple[str, float]] = []
        seen_paths: set[str] = set()
        for name, path in self._task_dir_chain(account_id, user_id):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            started_at = time.perf_counter()
            await self._mkdir_if_missing(path)
            timings.append((name, (time.perf_counter() - started_at) * 1000))
        self._ensured_task_dirs.add(cache_key)
        return timings

    def _task_dir_chain(self, account_id: str, user_id: str) -> tuple[tuple[str, str], ...]:
        return (
            ("account", self._account_dir(account_id)),
            ("system", self._system_dir(account_id)),
            ("task_root", self._task_root_dir(account_id)),
            ("user", self._task_dir(account_id, user_id)),
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
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": status.value if hasattr(status, "value") else status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "resource_id": task.resource_id,
        "account_id": task.account_id,
        "user_id": task.user_id,
        "meta": deepcopy(task.meta),
        "stage": task.stage,
        "result": deepcopy(task.result),
        "error": task.error,
        "auth": deepcopy(task.auth),
    }


def _decode_bytes(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)
