# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded subprocess probe used only by Watch persistence tests."""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_runtime import WatchRuntime
from openviking.resource.watch_store_file import FileWatchStore
from openviking.resource.watch_store_mysql import MySQLWatchStore
from openviking.resource.watch_types import WatchTask
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from tests.utils.watch_fixtures import local_viking_fs

TARGET = "viking://resources/probe"
OWNER = {"account_id": "default", "user_id": "default"}


async def main():
    mode, material = sys.argv[1:3]
    if mode == "mysql":
        config = json.loads(material)
        store = MySQLWatchStore(service_name=config["service_name"], params=config["params"])
        await store.initialize()
        try:
            candidate = await store.get_candidate(config["watch_id"])
            run = await store.begin_run(candidate) if candidate else None
            print(json.dumps({"won": run is not None}))
        finally:
            await store.close()
        return

    root = Path(material)
    fs = local_viking_fs(root / "agfs")
    tracker = TaskTracker(PersistentTaskStore(fs._async_agfs))
    seen = []

    async def refresh(**kwargs):
        task = await tracker.create("add_resource", resource_id=TARGET, **OWNER)
        seen.append(task.task_id)
        await kwargs["on_task_created"](task)
        if mode == "run":
            await tracker.complete(task.task_id, {"root_uri": TARGET}, **OWNER)
        return {"status": "success", "task_id": task.task_id}

    service = WatchManager(
        FileWatchStore(fs),
        viking_fs=fs,
        tracker=tracker,
        resource_service=SimpleNamespace(refresh_resource=refresh, _connector=None),
    )
    await service.initialize()
    if mode == "seed":
        await fs.mkdir(TARGET, exist_ok=True)
        task = WatchTask(
            path="https://example.org/probe",
            to_uri=TARGET,
            watch_interval=1,
            created_at=datetime.now() - timedelta(minutes=5),
        )
        task.next_execution_time = task.calculate_next_execution_time()
        await service.store.create_task(task)
        receipt = await service.trigger(task.task_id)
        print(json.dumps({"watch_id": task.task_id, "task_id": receipt.task_id}))
    else:
        tasks = await service.get_all_tasks("default", "default", "root")
        state = await service.store.get_state(tasks[0].task_id)
        assert state.current_run_id is None
        runtime = WatchRuntime(service, leader_enabled=False)
        await runtime.start()
        try:
            await runtime.sweep()
            await runtime.dispatch_due()
            await asyncio.gather(*list(runtime._submissions.values()))
            await runtime.sweep()
            tasks = await service.get_all_tasks("default", "default", "root")
            state = await service.store.get_state(tasks[0].task_id)
            print(
                json.dumps(
                    {
                        "submitted": seen,
                        "current_run_id": state.current_run_id,
                        "last_task_id": state.task.last_task_id,
                        "status": state.task.last_status,
                    }
                )
            )
        finally:
            await runtime.stop()
    await service.close()


if __name__ == "__main__":
    asyncio.run(main())
