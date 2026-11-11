# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Measure real Task/Work waits without changing their clocks or polling intervals.

Run on the baseline and implementation checkouts with identical arguments:
    uv run python test_scripts/repro_task_wait_latency.py --waiters 1 10 100
Use --backend mysql with OV_TEST_MYSQL for isolated, temporary MySQL tables.
File measurements replace only AGFS I/O with an in-memory implementation.
"""

import argparse
import asyncio
import json
import math
import os
import platform
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker

OWNER: dict[str, Any] = {"account_id": "account", "user_id": "user"}


class MemoryAgfs:
    def __init__(self, counts):
        self.files = {}
        self.counts = counts

    def mkdir(self, path, **kwargs):
        self.counts["agfs_mkdir"] += 1
        return {}

    def read(self, path, **kwargs):
        self.counts["agfs_read"] += 1
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write(self, path, data, **kwargs):
        self.counts["agfs_write"] += 1
        self.files[path] = data
        return "OK"


@asynccontextmanager
async def measured_store(backend, counts):
    if backend == "file":
        yield PersistentTaskStore(MemoryAgfs(counts))
        return
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine

    from openviking.service.task_store_mysql import MySQLTaskWorkStore

    params = {**json.loads(os.environ["OV_TEST_MYSQL"]), "table": f"ov_wait_{uuid4().hex[:12]}"}
    store = MySQLTaskWorkStore(params=params)
    admin = create_async_engine(store._url, **store._engine_options)
    sql_dir = Path(__file__).resolve().parents[1] / "openviking/service/sql"
    try:
        async with admin.begin() as connection:
            for path in [
                sql_dir / "task_store_init.sql",
                sql_dir / "changes/0001_task_store_service_name.sql",
            ]:
                for statement in path.read_text().replace("ov_tasks", params["table"]).split(";"):
                    if statement.strip():
                        await connection.exec_driver_sql(statement)
        await store.load()

        def executed(connection, cursor, statement, parameters, context, executemany):
            counts["sql_total"] += 1
            if statement.lstrip().upper().startswith("SELECT"):
                counts["sql_select"] += 1

        assert store._engine is not None
        event.listen(store._engine.sync_engine, "before_cursor_execute", executed)
        yield store
    finally:
        await store.close()
        async with admin.begin() as connection:
            await connection.run_sync(store._models.Base.metadata.drop_all)
        await admin.dispose()


async def measure(args, count, scenario, entry):
    counts: Counter[str] = Counter()
    async with measured_store(args.backend, counts) as store:
        tracker = TaskTracker(store)
        await tracker.create("add_resource", task_id="task", **OWNER)
        await tracker.start("task", **OWNER)
        work_ids = [str(index) for index in range(20 if scenario == "work-churn" else 1)]
        if entry != "task" or scenario in ("work-finalization", "work-churn"):
            for work_id in work_ids:
                await tracker.register_work("task", work_id, "Embedding", **OWNER)
            if entry != "task" and scenario != "work-finalization":
                # Work waiters can finish while unrelated Work keeps the Task active.
                await tracker.register_work("task", "root", "Inline", **OWNER)
            else:
                await tracker.complete("task", {"ok": True}, **OWNER)
        if scenario == "unrelated":
            await tracker.create("add_resource", task_id="other", **OWNER)

        # Count logical reads at the Store boundary, separately from physical I/O.
        for name in ("get_task_status", "get_task", "get_works", "has_open_work"):
            original = getattr(store, name)

            async def read(*a, _name=name, _original=original, **kw):
                counts[_name] += 1
                return await _original(*a, **kw)

            setattr(store, name, read)
        counts.clear()

        async def observe():
            if entry == "task":
                result = await tracker.wait("task", timeout=args.delay + 60, **OWNER)
                assert result.status is TaskStatus.COMPLETED
            elif entry == "work":
                await tracker.wait_for_work("task", work_ids, timeout=args.delay + 60, **OWNER)
            else:
                await tracker.wait_for_task_work(
                    "task", exclude_work_id="root", timeout=args.delay + 60, **OWNER
                )
            return time.perf_counter()

        waiters = [asyncio.create_task(observe()) for _ in range(count)]
        started = time.perf_counter()
        # Deliberately allow waits to reach their natural long polling interval.
        await asyncio.sleep(args.delay)
        if scenario == "idle":
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            return {
                "waiters": count,
                "scenario": scenario,
                "entry": entry,
                "seconds": time.perf_counter() - started,
                "reads": dict(counts),
            }
        if scenario == "unrelated":
            await tracker.complete("other", {}, **OWNER)
        if scenario == "duplicates":
            for _ in range(20):
                await tracker.create("add_resource", task_id="task", **OWNER)
        if entry != "task" or scenario in ("work-finalization", "work-churn"):
            for work_id in work_ids:
                await tracker.mark_work_done("task", work_id, **OWNER)
        else:
            await tracker.complete("task", {"ok": True}, **OWNER)
        # Conservative timestamp: writer returned after successful publication.
        committed = time.perf_counter()
        returned = await asyncio.gather(*waiters)
        lags = sorted(max(0, at - committed) * 1000 for at in returned)
        return {
            "waiters": count,
            "scenario": scenario,
            "entry": entry,
            "p50_ms": lags[math.ceil(count * 0.5) - 1],
            "p95_ms": lags[math.ceil(count * 0.95) - 1],
            "max_ms": lags[-1],
            "reads": dict(counts),
        }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["file", "mysql"], default="file")
    parser.add_argument("--waiters", nargs="+", type=int, default=[1, 10, 100])
    parser.add_argument("--delay", type=float, default=13)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--entries",
        nargs="+",
        choices=["task", "work", "task-work"],
        default=["task", "work", "task-work"],
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=[
            "completion",
            "work-finalization",
            "idle",
            "unrelated",
            "duplicates",
            "work-churn",
        ],
        default=["completion", "work-finalization"],
    )
    args = parser.parse_args()
    if min(args.waiters) < 1 or args.delay < 0 or args.repeat < 1:
        parser.error("waiters/repeat must be positive and delay nonnegative")
    print(
        json.dumps(
            {"python": platform.python_version(), "platform": platform.platform(), **vars(args)}
        ),
        flush=True,
    )
    for entry in args.entries:
        for scenario in args.scenarios:
            for count in args.waiters:
                for repeat in range(args.repeat):
                    result = await measure(args, count, scenario, entry)
                    print(json.dumps({"repeat": repeat, **result}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
