# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Lock handoff regressions for enqueuing an entire Skill package."""

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import TaskWorkRejected
from openviking.telemetry import OperationTelemetry, bind_telemetry, unregister_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.session.user_id import UserIdentifier


class FakePathLock:
    def __init__(self):
        self.events = []
        self.owned_ref = "producer-ref"
        self.ready = False
        self.released = False

    async def pathlock_to_handoff(self, lease):
        assert lease["lease_ref"] == self.owned_ref
        return {"handoff_id": "package-handoff"}

    async def pathlock_handoff(self, lease):
        assert lease["lease_ref"] == self.owned_ref
        self.events.append("handoff")
        self.owned_ref = None
        self.ready = True

    async def pathlock_adopt(self, handoff):
        assert handoff == {"handoff_id": "package-handoff"}
        assert self.ready, "Consumers can only adopt a ready handoff"
        self.events.append("adopt")
        self.ready = False
        self.owned_ref = "adopted-ref"
        return {"lease_ref": self.owned_ref, "owned": True}

    async def pathlock_release(self, lease):
        assert lease == {"lease_ref": self.owned_ref, "owned": True}
        assert self.owned_ref is not None, "The old producer reference cannot release a handoff"
        self.events.append("release")
        self.released = True
        self.owned_ref = None


class ImmediateConsumerQueue:
    SEMANTIC = "Semantic"

    def __init__(self, agfs, *, reject=False):
        self.agfs = agfs
        self.reject = reject
        self.messages = []
        self.consumer_lease = None

    def get_queue(self, name, *, allow_create):
        assert name == self.SEMANTIC
        assert allow_create
        return self

    async def enqueue(self, msg):
        self.agfs.events.append("enqueue")
        assert self.agfs.ready, "A consumer may start as soon as enqueue makes the message visible"
        self.messages.append(msg)
        if self.reject:
            raise TaskWorkRejected("Skill task is cancelling")
        self.consumer_lease = await self.agfs.pathlock_adopt(msg.lock_handoff)
        return msg.id


async def test_skill_package_handoff_is_ready_before_consumer_can_start(monkeypatch):
    agfs = FakePathLock()
    queue = ImmediateConsumerQueue(agfs)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    telemetry = OperationTelemetry("test.skill.enqueue")
    tracker = get_request_wait_tracker()
    lease = {"lease_ref": "producer-ref", "owned": True}
    try:
        with bind_telemetry(telemetry):
            await SkillProcessor(vikingdb=None)._enqueue_skill_package(
                "viking://agent/skills/demo",
                SimpleNamespace(_async_agfs=agfs),
                RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER),
                lease,
            )

        assert agfs.events == ["handoff", "enqueue", "adopt"]
        assert not agfs.released
        assert queue.consumer_lease == {"lease_ref": "adopted-ref", "owned": True}
        assert not tracker.is_complete(telemetry.telemetry_id)
        await agfs.pathlock_release(queue.consumer_lease)
        tracker.mark_semantic_done(telemetry.telemetry_id, queue.messages[0].id)
        assert tracker.is_complete(telemetry.telemetry_id)
    finally:
        for msg in queue.messages:
            tracker.mark_semantic_done(telemetry.telemetry_id, msg.id, processed_delta=0)
        tracker.cleanup(telemetry.telemetry_id)
        unregister_telemetry(telemetry.telemetry_id)


async def test_rejected_skill_enqueue_returns_reclaimed_lease_to_caller_and_settles_waiter(
    monkeypatch,
):
    agfs = FakePathLock()
    queue = ImmediateConsumerQueue(agfs, reject=True)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    telemetry = OperationTelemetry("test.skill.rejected_enqueue")
    tracker = get_request_wait_tracker()
    lease = {"lease_ref": "producer-ref", "owned": True}
    caller_lease = lease
    try:
        with bind_telemetry(telemetry):
            with pytest.raises(TaskWorkRejected, match="Skill task is cancelling"):
                try:
                    await SkillProcessor(vikingdb=None)._enqueue_skill_package(
                        "viking://agent/skills/demo",
                        SimpleNamespace(_async_agfs=agfs),
                        RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER),
                        lease,
                    )
                finally:
                    # The real caller only retains its original dictionary for cleanup.
                    await agfs.pathlock_release(caller_lease)

        assert lease is caller_lease
        assert lease == {"lease_ref": "adopted-ref", "owned": True}
        assert agfs.events == ["handoff", "enqueue", "adopt", "release"]
        assert agfs.released
        assert tracker.is_complete(telemetry.telemetry_id)
        status = tracker.build_queue_status(telemetry.telemetry_id)["Semantic"]
        assert status["processed"] == 0
        assert status["error_count"] == 1
        assert status["errors"] == [{"message": "Skill task is cancelling"}]
        tracker.cleanup(telemetry.telemetry_id)
        assert not tracker.has_request(telemetry.telemetry_id), (
            "Rejected work must not retain state"
        )
    finally:
        tracker.cleanup(telemetry.telemetry_id)
        unregister_telemetry(telemetry.telemetry_id)
