import json

import pytest

from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue


class _MemoryQueueAGFS:
    def __init__(self):
        self.messages = []
        self.acked = []
        self.created = set()

    def mkdir(self, path, **kwargs):
        self.created.add(path)
        return {"path": path}

    def write(self, path, data, **kwargs):
        if path.endswith("/enqueue"):
            msg_id = f"msg-{len(self.messages) + 1}"
            payload = json.loads(data.decode("utf-8"))
            payload["id"] = msg_id
            self.messages.append(payload)
            return msg_id
        if path.endswith("/ack"):
            self.acked.append(data.decode("utf-8"))
            return "ok"
        return "ok"

    def read(self, path, **kwargs):
        if path.endswith("/dequeue"):
            if not self.messages:
                return b"{}"
            return json.dumps(self.messages.pop(0)).encode("utf-8")
        if path.endswith("/size"):
            return str(len(self.messages)).encode("utf-8")
        raise FileNotFoundError(path)


class _RaisingHandler(DequeueHandlerBase):
    async def on_dequeue(self, data):
        raise RuntimeError("retry later")


@pytest.mark.asyncio
async def test_dequeue_handler_exception_rolls_back_in_progress_without_ack():
    agfs = _MemoryQueueAGFS()
    queue = NamedQueue(
        agfs,
        "/queue",
        "Retry",
        dequeue_handler=_RaisingHandler(),
    )
    msg_id = await queue.enqueue({"value": 1})

    result = await queue.dequeue()

    assert result is None
    assert agfs.acked == []
    status = await queue.get_status()
    assert status.in_progress == 0
    assert status.error_count == 1
    assert status.errors[-1].message == "retry later"
    assert status.errors[-1].data["id"] == msg_id
