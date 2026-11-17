# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Isolated adapter of the file owner's Task/Work transition rules.

A snapshot is disposable: no filesystem, cache, lock or authority survives a
transaction. The Redis owner persists both Task and Work after a successful CAS.
"""

from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

from openviking.service.task_domain import WorkRecord, WorkState
from openviking.service.task_store import (
    PersistentTaskStore,
    _task_from_payload,
    _task_to_payload,
    _WorkSet,
)


class _TaskWorkSnapshot(PersistentTaskStore):
    def __init__(self, payloads):
        super().__init__(SimpleNamespace())
        for payload in payloads:
            task = _task_from_payload(payload)
            if not task.account_id or not task.user_id:
                raise ValueError("Task snapshot requires an owner")
            owner = (task.account_id, task.user_id, task.task_id)
            self._task_cache[owner] = task
            works = {}
            for value in payload.get("work", []):
                data = dict(value)
                data["state"] = WorkState(data["state"])
                work = WorkRecord(**data)
                works[work.work_id] = work
            self._work_sets[owner] = _WorkSet(works, sum(w.is_open() for w in works.values()))

    async def _load_task(self, task_id, account_id, user_id):
        owner = self._find_cached_owner(task_id, account_id, user_id)
        return self._task_cache.get(owner) if owner is not None else None

    async def get_durable_task(self, task_id, *, account_id, user_id):
        return deepcopy(await self._load_task(task_id, account_id, user_id))

    async def _write_task(self, task):
        # Publishing remains owned by PersistentTaskStore after this returns.
        pass

    async def _owner_users(self, account_id):
        return sorted({u for a, u, _ in self._task_cache if a == account_id})

    async def _list_owner_payloads(self, account_id, user_id):
        return [
            _task_to_payload(t)
            for (a, u, _), t in self._task_cache.items()
            if (a, u) == (account_id, user_id)
        ]

    async def _list_owner_page(self, account_id, *, limit, **kwargs):
        return (await self.list(account_id, limit=None, **kwargs))[:limit]

    async def list_durable(self, account_id, *, task_type=None):
        return [
            deepcopy(t)
            for (a, _, _), t in self._task_cache.items()
            if a == account_id and (task_type is None or t.task_type == task_type)
        ]

    async def _delete_task_unlocked(self, owner, task_exists):
        self._task_cache.pop(owner, None)
        self._work_sets.pop(owner, None)
        return task_exists

    def payloads(self):
        result = {}
        for owner, task in self._task_cache.items():
            payload = _task_to_payload(task)
            payload["work"] = [asdict(w) for w in self._work_sets[owner].works.values()]
            result[owner] = payload
        return result
