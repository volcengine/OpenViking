# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""New-process restart and actual multiprocess MySQL claim checks."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from tests.resource.test_watch_store import (
    ENCRYPTION_KEY,
    MYSQL_PARAMS,
    create,
    make_store,  # noqa: F401
)

ROOT = Path(__file__).parents[2]


async def probe(mode, material):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.utils.watch_process_probe",
        mode,
        material,
        cwd=ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    assert process.returncode == 0, stderr.decode()
    return json.loads(stdout.decode().splitlines()[-1])


@pytest.mark.asyncio
async def test_file_restart_retries_due_watch_without_persisting_occupancy(tmp_path):
    receipt = await probe("seed", str(tmp_path))
    result = await probe("run", str(tmp_path))
    assert receipt["task_id"]
    assert len(result["submitted"]) == 1
    assert result["submitted"][0] != receipt["task_id"]
    assert result["current_run_id"] is None
    assert result["last_task_id"] == result["submitted"][0]
    assert result["status"] == "completed"
    restarted = await probe("run", str(tmp_path))
    assert restarted["submitted"] == []


@pytest.mark.asyncio
async def test_mysql_start_is_exclusive_across_processes(make_store):  # noqa: F811
    store = await make_store()
    if store.provider != "mysql":
        pytest.skip("multiprocess claims require MySQL; File is single-process")
    task = await create(store, due=True)
    config = {
        "service_name": store._service_name,
        "params": {
            **json.loads(MYSQL_PARAMS),
            "table": store._table_name,
            "credential_encryption_key": ENCRYPTION_KEY,
        },
        "watch_id": task.task_id,
        "target": task.to_uri,
    }
    results = await asyncio.gather(
        *[
            probe("mysql", json.dumps({**config, "attempt": f"process-{index}"}))
            for index in range(4)
        ]
    )
    assert sum(result["won"] for result in results) == 1
    state = await store.get_state(task.task_id)
    assert state.current_run_id is None
    assert state.run_started_at is not None
