# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
from types import SimpleNamespace

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_queue import SemanticQueue


class FakeQueueAGFS:
    def __init__(self, fail_ack: bool = False):
        self._pending = []
        self._processing = {}
        self._dirs = set()
        self._next_id = 0
        self._fail_ack = fail_ack

    def mkdir(self, path):
        self._dirs.add(path)

    def write(self, path, content):
        if path.endswith("/enqueue"):
            self._next_id += 1
            msg_id = f"msg-{self._next_id}"
            payload = content.decode("utf-8") if isinstance(content, bytes) else str(content)
            self._pending.append({"id": msg_id, "data": payload})
            return msg_id
        if path.endswith("/ack"):
            if self._fail_ack:
                raise RuntimeError("ack failed")
            msg_id = content.decode("utf-8") if isinstance(content, bytes) else str(content)
            self._processing.pop(msg_id, None)
            return msg_id
        if path.endswith("/clear"):
            self._pending.clear()
            self._processing.clear()
            return "cleared"
        raise NotImplementedError(path)

    def read(self, path):
        if path.endswith("/size"):
            return str(len(self._pending)).encode("utf-8")
        if path.endswith("/peek"):
            if not self._pending:
                return b"{}"
            return json.dumps(self._pending[0]).encode("utf-8")
        if path.endswith("/dequeue"):
            if not self._pending:
                return b"{}"
            item = self._pending.pop(0)
            self._processing[item["id"]] = item
            return json.dumps(item).encode("utf-8")
        raise NotImplementedError(path)


def _semantic_config(cooldown_seconds: int):
    return SimpleNamespace(
        semantic=SimpleNamespace(summary_enqueue_cooldown_seconds=cooldown_seconds)
    )


async def test_semantic_queue_dedupes_same_request_while_active(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_queue.get_openviking_config",
        lambda: _semantic_config(300),
    )
    queue = SemanticQueue(FakeQueueAGFS(), "/queue", "Semantic")

    first_msg = SemanticMsg(uri="viking://user/default/memories/entities", context_type="memory")
    second_msg = SemanticMsg(uri="viking://user/default/memories/entities", context_type="memory")

    first_id = await queue.enqueue(first_msg)
    second_id = await queue.enqueue(second_msg)

    assert first_id == second_id
    assert await queue.size() == 1

    raw = await queue.dequeue_raw()
    assert raw is not None
    queue._on_dequeue_start()
    await queue.ack(raw["id"])

    assert await queue.size() == 0


async def test_semantic_queue_coalesces_changed_memory_updates(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_queue.get_openviking_config",
        lambda: _semantic_config(300),
    )
    queue = SemanticQueue(FakeQueueAGFS(), "/queue", "Semantic")

    first = SemanticMsg(
        uri="viking://user/default/memories/entities",
        context_type="memory",
        changes={"added": ["a.md"], "modified": [], "deleted": []},
    )
    second = SemanticMsg(
        uri="viking://user/default/memories/entities",
        context_type="memory",
        changes={"added": [], "modified": ["b.md"], "deleted": []},
    )

    first_id = await queue.enqueue(first)
    second_id = await queue.enqueue(second)
    third_id = await queue.enqueue(second)

    assert first_id == second_id
    assert second_id == third_id
    assert await queue.size() == 1

    raw = await queue.dequeue_raw()
    assert raw is not None
    queue._on_dequeue_start()
    await queue.ack(raw["id"])

    assert await queue.size() == 1

    follow_up = await queue.dequeue_raw()
    assert follow_up is not None
    payload = SemanticMsg.from_json(follow_up["data"])
    assert payload.changes == {
        "added": ["a.md"],
        "modified": ["b.md"],
        "deleted": [],
    }


async def test_semantic_queue_applies_completion_cooldown(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_queue.get_openviking_config",
        lambda: _semantic_config(60),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_queue.time.monotonic",
        lambda: clock["now"],
    )

    queue = SemanticQueue(FakeQueueAGFS(), "/queue", "Semantic")
    msg = SemanticMsg(uri="viking://user/default/memories/entities", context_type="memory")

    await queue.enqueue(msg)
    raw = await queue.dequeue_raw()
    assert raw is not None
    queue._on_dequeue_start()
    await queue.ack(raw["id"])

    second_id = await queue.enqueue(msg)
    assert await queue.size() == 0

    clock["now"] += 61
    third_id = await queue.enqueue(msg)

    assert third_id != second_id
    assert await queue.size() == 1


async def test_semantic_queue_preserves_same_message_retry_while_active(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_queue.get_openviking_config",
        lambda: _semantic_config(300),
    )
    queue = SemanticQueue(FakeQueueAGFS(), "/queue", "Semantic")

    msg = SemanticMsg(uri="viking://user/default/memories/entities", context_type="memory")

    first_id = await queue.enqueue(msg)
    second_id = await queue.enqueue(msg)

    assert first_id == second_id
    assert await queue.size() == 1

    raw = await queue.dequeue_raw()
    assert raw is not None
    queue._on_dequeue_start()
    await queue.ack(raw["id"])

    assert await queue.size() == 1

    follow_up = await queue.dequeue_raw()
    assert follow_up is not None
    payload = SemanticMsg.from_json(follow_up["data"])
    assert payload.id == msg.id


async def test_semantic_queue_does_not_release_follow_up_when_ack_fails(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_queue.get_openviking_config",
        lambda: _semantic_config(300),
    )
    queue = SemanticQueue(FakeQueueAGFS(fail_ack=True), "/queue", "Semantic")

    first = SemanticMsg(
        uri="viking://user/default/memories/entities",
        context_type="memory",
        changes={"added": ["a.md"], "modified": [], "deleted": []},
    )
    second = SemanticMsg(
        uri="viking://user/default/memories/entities",
        context_type="memory",
        changes={"added": [], "modified": ["b.md"], "deleted": []},
    )

    await queue.enqueue(first)
    await queue.enqueue(second)

    raw = await queue.dequeue_raw()
    assert raw is not None
    queue._on_dequeue_start()
    await queue.ack(raw["id"])

    assert await queue.size() == 0
    assert raw["id"] in queue._queue_id_to_key
    assert queue._tracked_by_key


async def test_semantic_queue_keeps_distinct_targets_separate(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_queue.get_openviking_config",
        lambda: _semantic_config(300),
    )
    queue = SemanticQueue(FakeQueueAGFS(), "/queue", "Semantic")

    session_msg = SemanticMsg(
        uri="viking://session/default/abc",
        context_type="session",
        target_uri="viking://user/default/memories/entities",
    )
    memory_msg = SemanticMsg(
        uri="viking://session/default/abc",
        context_type="session",
        target_uri="viking://user/default/memories/patterns",
    )

    first_id = await queue.enqueue(session_msg)
    second_id = await queue.enqueue(memory_msg)

    assert first_id != second_id
    assert await queue.size() == 2


async def test_semantic_queue_keeps_distinct_telemetry_and_lock_keys_separate(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_queue.get_openviking_config",
        lambda: _semantic_config(300),
    )
    queue = SemanticQueue(FakeQueueAGFS(), "/queue", "Semantic")

    first = SemanticMsg(
        uri="viking://session/default/abc",
        context_type="session",
        telemetry_id="telemetry-a",
        lifecycle_lock_handle_id="lock-a",
    )
    second = SemanticMsg(
        uri="viking://session/default/abc",
        context_type="session",
        telemetry_id="telemetry-b",
        lifecycle_lock_handle_id="lock-b",
    )

    first_id = await queue.enqueue(first)
    second_id = await queue.enqueue(second)

    assert first_id != second_id
    assert await queue.size() == 2
