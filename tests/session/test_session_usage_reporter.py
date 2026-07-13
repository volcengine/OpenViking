# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

from openviking.message import ToolPart
from openviking.service.task_tracker import get_task_tracker
from openviking.usage_reporter import FileJsonlUsageSink, MemoryUsageExtractor, UsageReporter


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = await tracker.get(task_id)
        if task and task.status.value in ("completed", "failed"):
            return task.to_dict()
        import asyncio

        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


@pytest.mark.asyncio
async def test_commit_reports_memory_usage_events_to_sink(session, tmp_path):
    events_path = tmp_path / "usage-events.jsonl"
    experience_uri = "viking://user/default/memories/experiences/no-order-exchange.md"
    session._usage_reporter = UsageReporter(
        extractors=[MemoryUsageExtractor()],
        sinks=[FileJsonlUsageSink(path=str(events_path))],
    )
    session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call-read",
                tool_name="read_experience",
                tool_status="completed",
                tool_input={"uri": experience_uri},
            )
        ],
    )

    result = await session.commit_async(
        memory_policy={
            "self": {"enabled": False},
            "peer": {"enabled": False},
            "working_memory": {"enabled": False},
        }
    )
    task = await _wait_for_task(result["task_id"])

    assert task["status"] == "completed"
    payloads = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(payloads) == 1
    assert payloads[0]["event_type"] == "memory.injected"
    assert payloads[0]["memory_uri"] == experience_uri
    assert payloads[0]["session_id"] == session.session_id
    assert payloads[0]["archive_uri"] == result["archive_uri"]
    assert payloads[0]["task_id"] == result["task_id"]
