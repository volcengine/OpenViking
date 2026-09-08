# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Offline integration coverage for the Session-to-Memory pipeline."""

import asyncio

from openviking.message import TextPart
from openviking.service.task_tracker import get_task_tracker
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = await tracker.get(task_id)
        if task and task.status.value in {"completed", "failed"}:
            return task.to_dict()
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


async def test_local_commit_writes_and_recalls_preference_memory(
    client,
    service,
    request_context,
    monkeypatch,
):
    memory_uri = "viking://user/default/memories/preferences/default/python_code_style.md"
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_fields={
                    "user": "default",
                    "topic": "python_code_style",
                    "content": "- Prefers double quotes in Python code.",
                },
                memory_type="preferences",
                uris=[memory_uri],
            )
        ],
        delete_file_contents=[],
        errors=[],
    )

    class FixedOrchestrator:
        def __init__(self, isolation_handler):
            assert isolation_handler.allowed_memory_types == {"preferences"}

        async def run(self):
            return operations, []

    session = client(session_id="local-memory-pipeline")
    await session.ensure_exists()
    memory_policy = {
        "peer": {"enabled": False},
        "working_memory": {"enabled": False},
        "memory_types": ["preferences"],
    }
    monkeypatch.setattr(
        session._session_compressor,
        "_get_or_create_react",
        lambda **kwargs: FixedOrchestrator(kwargs["isolation_handler"]),
    )

    session.add_message(
        "user",
        [TextPart("When you generate Python code, use double quotes.")],
    )

    commit = await session.commit_async(memory_policy=memory_policy)
    task = await _wait_for_task(commit["task_id"])

    assert task["status"] == "completed", task.get("error")
    queue_status = await service.resources.wait_processed(timeout=30.0)
    assert all(status["error_count"] == 0 for status in queue_status.values()), queue_status
    memory = await service.viking_fs.read_file(memory_uri, ctx=request_context)
    assert "Prefers double quotes in Python code." in memory

    result = await service.search.find(
        query="What Python code style does the user prefer?",
        ctx=request_context,
        target_uri="viking://user/default/memories",
        score_threshold=-1.0,
        level=[2],
    )

    assert memory_uri in {item.uri for item in result.memories}
