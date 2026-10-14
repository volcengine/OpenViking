# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Request-scoped waits must not depend on unrelated queue work."""

import asyncio
from types import SimpleNamespace

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.telemetry.context import bind_telemetry
from openviking.telemetry.operation import OperationTelemetry
from openviking.telemetry.request_wait_tracker import RequestWaitTracker
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


async def test_add_skill_wait_uses_request_tracker_when_telemetry_disabled(monkeypatch):
    tracker = RequestWaitTracker()
    telemetry = OperationTelemetry(operation="resources.add_skill", enabled=False)
    tracker.register_request("unrelated")
    tracker.register_embedding_root("unrelated", "pending-embedding")
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    root_uri = "viking://user/default/skills/demo"

    async def process_skill(**kwargs):
        tracker.register_embedding_root(telemetry.telemetry_id, "skill-embedding")
        tracker.mark_embedding_done(telemetry.telemetry_id, "skill-embedding")
        return {"status": "success", "uri": root_uri, "name": "demo"}

    service = ResourceService(
        resource_processor=object(),
        skill_processor=SimpleNamespace(process_skill=process_skill),
        viking_fs=object(),
    )
    monkeypatch.setattr(
        "openviking.service.resource_service.get_request_wait_tracker", lambda: tracker
    )
    task_tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: task_tracker)

    with bind_telemetry(telemetry):
        result = await asyncio.wait_for(
            service.add_skill(
                data={"name": "demo", "content": "# Demo"},
                ctx=ctx,
                wait=True,
                target_uri="viking://user/default/skills",
            ),
            1,
        )

    assert result["root_uri"] == root_uri
    assert result["queue_status"]["Embedding"]["processed"] == 1
    assert not tracker.has_request(telemetry.telemetry_id)
    assert not tracker.is_complete("unrelated")
