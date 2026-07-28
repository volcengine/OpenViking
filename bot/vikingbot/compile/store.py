"""Small process-local JSON store for durable compile task status."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from vikingbot.compile.models import (
    TERMINAL_STATUSES,
    CompileErrorInfo,
    CompileTask,
    utc_now,
)


class CompileTaskStore:
    def __init__(self, bot_data_path: Path):
        self.root = Path(bot_data_path) / "compile_tasks"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _task_lock(self, task_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(task_id, asyncio.Lock())

    def _path(self, task_id: str) -> Path:
        if not task_id.startswith("cmp_") or any(ch in task_id for ch in "/\\"):
            raise ValueError("invalid compile task id")
        return self.root / f"{task_id}.json"

    async def create(self, task: CompileTask) -> None:
        lock = await self._task_lock(task.task_id)
        async with lock:
            path = self._path(task.task_id)
            if path.exists():
                raise FileExistsError(task.task_id)
            self._write_atomic(path, task)

    async def get(self, task_id: str) -> CompileTask | None:
        lock = await self._task_lock(task_id)
        async with lock:
            path = self._path(task_id)
            if not path.exists():
                return None
            return CompileTask.model_validate_json(path.read_text(encoding="utf-8"))

    async def update(
        self,
        task_id: str,
        mutate: Callable[[CompileTask], None],
    ) -> CompileTask:
        lock = await self._task_lock(task_id)
        async with lock:
            path = self._path(task_id)
            if not path.exists():
                raise FileNotFoundError(task_id)
            task = CompileTask.model_validate_json(path.read_text(encoding="utf-8"))
            mutate(task)
            task.updated_at = utc_now()
            self._write_atomic(path, task)
            return task

    async def mark_interrupted_failed(self) -> int:
        count = 0
        for path in sorted(self.root.glob("cmp_*.json")):
            try:
                task_id = path.stem
                existing = await self.get(task_id)
                if existing is None or existing.status in TERMINAL_STATUSES:
                    continue

                def interrupt(task: CompileTask) -> None:
                    nonlocal count
                    task.status = "failed"
                    task.error = CompileErrorInfo(
                        code="BOT_RESTARTED",
                        message="VikingBot restarted before the compile task completed.",
                    )
                    count += 1

                await self.update(task_id, interrupt)
            except (OSError, ValueError):
                continue
        return count

    async def prune_terminal(self, *, retention_seconds: float, max_records: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=retention_seconds)
        retained: list[tuple[datetime, str]] = []
        expired: list[str] = []
        for path in sorted(self.root.glob("cmp_*.json")):
            try:
                task = await self.get(path.stem)
                if task is None or task.status not in TERMINAL_STATUSES:
                    continue
                updated_at = datetime.fromisoformat(task.updated_at.replace("Z", "+00:00"))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                if updated_at < cutoff:
                    expired.append(task.task_id)
                else:
                    retained.append((updated_at, task.task_id))
            except (OSError, ValueError):
                continue

        retained.sort(reverse=True)
        task_ids = [*expired, *(task_id for _, task_id in retained[max_records:])]
        for task_id in task_ids:
            lock = await self._task_lock(task_id)
            async with lock:
                self._path(task_id).unlink(missing_ok=True)
            async with self._locks_guard:
                if self._locks.get(task_id) is lock and not lock.locked():
                    self._locks.pop(task_id, None)
        return len(task_ids)

    @staticmethod
    def _write_atomic(path: Path, task: CompileTask) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        data = task.model_dump(mode="json", by_alias=True, exclude_none=True)
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)


__all__ = ["CompileTaskStore"]
