# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.message import ToolPart
from openviking.server.dependencies import get_service
from openviking.service.task_tracker import get_task_tracker
from openviking.usage_reporter import MemoryUsageExtractor, UsageReporter


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
async def test_commit_reports_memory_usage_events_to_sink(session):
    reported_events = []

    class RecordingSink:
        async def write(self, *, events):
            reported_events.extend(events)

    experience_uri = "viking://user/default/memories/experiences/no-order-exchange.md"
    reporter = UsageReporter(
        extractors=[MemoryUsageExtractor()],
        sinks=[RecordingSink()],
    )
    get_service().sessions.set_usage_reporter(reporter)
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
    assert task["result"]["usage_events_extracted"] == 1
    assert "usage_events_reported" not in task["result"]
    assert len(reported_events) == 1
    assert reported_events[0].event_type == "memory.injected"
    assert reported_events[0].resource_uri == experience_uri
    assert reported_events[0].resource_type == "experience"
    assert reported_events[0].session_id == session.session_id
    assert reported_events[0].evidence["archive_uri"] == result["archive_uri"]
    assert reported_events[0].task_id == result["task_id"]
