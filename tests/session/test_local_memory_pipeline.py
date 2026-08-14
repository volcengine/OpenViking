# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Offline integration coverage for the Session-to-Memory pipeline."""

import asyncio

from openviking.message import TextPart
from openviking.service.task_tracker import get_task_tracker
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_type_registry import create_default_registry
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdater
from openviking.utils.time_utils import get_current_timestamp


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

    async def extract_fixed_preference(*, messages, ctx, **_kwargs):
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
        updater = MemoryUpdater(
            registry=create_default_registry(),
            vikingdb=None,
        )
        await updater.apply_operations(
            operations,
            ctx,
            extract_context=ExtractContext(messages),
        )
        memory = await service.viking_fs.read_file(memory_uri, ctx=ctx)
        vector = service.vikingdb_manager.get_embedder().embed(memory).dense_vector
        timestamp = get_current_timestamp()
        await service.vikingdb_manager.upsert(
            {
                "uri": memory_uri,
                "parent_uri": "viking://user/default/memories/preferences/default",
                "is_leaf": True,
                "abstract": "Prefers double quotes in Python code.",
                "context_type": "memory",
                "category": "preferences",
                "created_at": timestamp,
                "updated_at": timestamp,
                "active_count": 0,
                "vector": vector,
                "meta": {},
                "related_uri": [],
                "account_id": ctx.account_id,
                "owner_space": ctx.user.user_id,
                "level": 2,
            },
            ctx=ctx,
        )
        return {"contexts": [], "session_skills": []}

    session = client(session_id="local-memory-pipeline")
    await session.ensure_exists()
    session.meta.memory_policy = {
        "peer": {"enabled": False},
        "working_memory": {"enabled": False},
        "memory_types": ["preferences"],
    }
    monkeypatch.setattr(
        session._session_compressor,
        "extract_long_term_memories",
        extract_fixed_preference,
    )

    session.add_message(
        "user",
        [TextPart("When you generate Python code, use double quotes.")],
    )

    commit = await session.commit_async()
    task = await _wait_for_task(commit["task_id"])

    assert task["status"] == "completed", task.get("error")
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
