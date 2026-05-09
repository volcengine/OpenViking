# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Internal storage backends for TaskTracker."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Optional, Protocol

from openviking.pyagfs.exceptions import AGFSAlreadyExistsError


class TaskStore(Protocol):
    def create(self, task: Any) -> None: ...

    def update(self, task: Any) -> None: ...

    def get(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]: ...

    def list(self, account_id: str, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]: ...

    def delete(self, task_id: str, *, account_id: str, user_id: Optional[str] = None) -> None: ...


class InMemoryTaskStore:
    """Simple in-process task store."""

    def __init__(self) -> None:
        self._tasks: Dict[str, Dict[str, Any]] = {}

    def create(self, task: Any) -> None:
        self._tasks[task.task_id] = _task_to_payload(task)

    def update(self, task: Any) -> None:
        self._tasks[task.task_id] = _task_to_payload(task)

    def get(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        payload = self._tasks.get(task_id)
        if payload is None:
            return None
        if account_id is not None and payload.get("account_id") != account_id:
            return None
        if user_id is not None and payload.get("user_id") != user_id:
            return None
        return deepcopy(payload)

    def list(self, account_id: str, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return [
            deepcopy(payload)
            for payload in self._tasks.values()
            if payload.get("account_id") == account_id
            and (user_id is None or payload.get("user_id") == user_id)
        ]

    def delete(self, task_id: str, *, account_id: str, user_id: Optional[str] = None) -> None:
        payload = self._tasks.get(task_id)
        if (
            payload
            and payload.get("account_id") == account_id
            and (user_id is None or payload.get("user_id") == user_id)
        ):
            del self._tasks[task_id]


class PersistentTaskStore:
    """Persist task records into AGFS under account-scoped task directories."""

    ROOT_PREFIX = "/local"
    RESERVED_DIRNAME = "tasks"

    def __init__(self, agfs: Any) -> None:
        self._agfs = agfs

    def create(self, task: Any) -> None:
        self._write_task(task)

    def update(self, task: Any) -> None:
        self._write_task(task)

    def get(
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
            raw = self._agfs.read(path)
        except Exception:
            return None
        return json.loads(_decode_bytes(raw))

    def list(self, account_id: str, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not user_id:
            return []
        directory = self._task_dir(account_id, user_id)
        try:
            items = self._agfs.ls(directory)
        except Exception:
            return []
        tasks: List[Dict[str, Any]] = []
        for item in items:
            path = item.get("path") or f"{directory}/{item.get('name', '')}"
            if not path.endswith(".json"):
                continue
            try:
                raw = self._agfs.read(path)
                tasks.append(json.loads(_decode_bytes(raw)))
            except Exception:
                continue
        return tasks

    def delete(self, task_id: str, *, account_id: str, user_id: Optional[str] = None) -> None:
        if not user_id:
            return
        self._agfs.rm(self._task_path(account_id, user_id, task_id), force=True)

    def _write_task(self, task: Any) -> None:
        account_id = getattr(task, "account_id", None)
        user_id = getattr(task, "user_id", None)
        if not account_id or not user_id:
            raise ValueError("PersistentTaskStore requires account_id and user_id")
        self._ensure_task_dir(account_id, user_id)
        self._agfs.write(
            self._task_path(account_id, user_id, task.task_id),
            json.dumps(_task_to_payload(task), ensure_ascii=False).encode("utf-8"),
        )

    def _ensure_task_dir(self, account_id: str, user_id: str) -> None:
        self._mkdir_if_missing(self._account_dir(account_id))
        self._mkdir_if_missing(self._task_root_dir(account_id))
        self._mkdir_if_missing(self._task_dir(account_id, user_id))

    def _mkdir_if_missing(self, path: str) -> None:
        try:
            self._agfs.mkdir(path)
        except AGFSAlreadyExistsError:
            return
        except Exception as exc:
            if "already exists" in str(exc).lower():
                return
            raise

    def _account_dir(self, account_id: str) -> str:
        return f"{self.ROOT_PREFIX}/{account_id}"

    def _task_root_dir(self, account_id: str) -> str:
        return f"{self._account_dir(account_id)}/{self.RESERVED_DIRNAME}"

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
        "result": deepcopy(task.result),
        "error": task.error,
    }


def _decode_bytes(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)
