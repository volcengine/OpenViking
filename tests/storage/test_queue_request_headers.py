# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.utils.request_headers import (
    MODEL_REQUEST_CONTEXT_KEY,
    bind_request_headers,
    get_request_headers_snapshot,
    resolve_extra_headers,
)


class _CapturingHandler(DequeueHandlerBase):
    def __init__(self) -> None:
        self.snapshot = None
        self.resolved = None
        self.data = None

    async def on_dequeue(self, data):
        self.snapshot = get_request_headers_snapshot()
        self.resolved = resolve_extra_headers(
            {
                "Authorization": "@request.header.Authorization",
                "X-Upstream-Tenant": "@request.header.X-Tenant",
            }
        )
        self.data = data
        self.report_success()
        return data


def _queue(
    name: str = "Semantic",
    handler: DequeueHandlerBase | None = None,
) -> NamedQueue:
    queue = NamedQueue(
        MagicMock(),
        "/queue",
        name,
        dequeue_handler=handler,
        propagate_model_request_context=True,
    )
    queue._initialized = True
    queue._async_agfs = MagicMock()
    queue._async_agfs.write = AsyncMock(return_value="queue-id")
    return queue


def _queued_context(headers: object) -> dict:
    return {
        "id": "queue-id",
        "data": json.dumps(
            {
                MODEL_REQUEST_CONTEXT_KEY: {
                    "version": 1,
                    "request_bound": True,
                    "headers": headers,
                }
            }
        ),
    }


async def test_model_queue_persists_only_filtered_request_headers() -> None:
    queue = _queue()

    with bind_request_headers(
        {
            "Authorization": "Bearer user-a",
            "X-Tenant": "tenant-a",
            "Cookie": "must-not-persist",
        },
        source_names={"Authorization", "X-Tenant"},
    ):
        await queue.enqueue({"uri": "viking://resources/repo"})

    payload = json.loads(queue._async_agfs.write.await_args.args[1].decode())
    assert payload[MODEL_REQUEST_CONTEXT_KEY] == {
        "version": 1,
        "request_bound": True,
        "headers": {
            "authorization": "Bearer user-a",
            "x-tenant": "tenant-a",
        },
    }
    assert "must-not-persist" not in json.dumps(payload)


async def test_model_queue_restores_context_and_strips_internal_payload() -> None:
    handler = _CapturingHandler()
    queue = _queue(handler=handler)
    queued = _queued_context(
        {"authorization": "Bearer user-a", "x-tenant": "tenant-a"}
    )
    payload = json.loads(queued["data"])
    payload["uri"] = "viking://resources/repo"
    queued["data"] = json.dumps(payload)

    queue._on_dequeue_start()
    await queue.process_dequeued(queued)

    assert handler.snapshot == {
        "authorization": "Bearer user-a",
        "x-tenant": "tenant-a",
    }
    assert handler.resolved == {
        "Authorization": "Bearer user-a",
        "X-Upstream-Tenant": "tenant-a",
    }
    assert MODEL_REQUEST_CONTEXT_KEY not in json.loads(handler.data["data"])
    assert get_request_headers_snapshot() is None


async def test_request_bound_empty_headers_do_not_use_no_context_fallback() -> None:
    handler = _CapturingHandler()
    queue = _queue("Embedding", handler)
    queued = _queued_context({})

    queue._on_dequeue_start()
    await queue.process_dequeued(queued)

    assert handler.snapshot == {}
    assert handler.resolved == {"Authorization": "", "X-Upstream-Tenant": ""}


async def test_invalid_persisted_context_fails_closed_without_blocking_message() -> None:
    handler = _CapturingHandler()
    queue = _queue("Embedding", handler)
    queued = _queued_context({"authorization": 123})

    queue._on_dequeue_start()
    await queue.process_dequeued(queued)

    assert handler.snapshot == {}
    assert handler.resolved == {"Authorization": "", "X-Upstream-Tenant": ""}
    assert MODEL_REQUEST_CONTEXT_KEY not in json.loads(handler.data["data"])


async def test_enqueue_replaces_forged_internal_context() -> None:
    queue = _queue()

    with bind_request_headers({"Authorization": "Bearer real"}):
        await queue.enqueue(
            {
                MODEL_REQUEST_CONTEXT_KEY: {
                    "version": 1,
                    "request_bound": True,
                    "headers": {"authorization": "Bearer forged"},
                }
            }
        )

    payload = json.loads(queue._async_agfs.write.await_args.args[1].decode())
    assert payload[MODEL_REQUEST_CONTEXT_KEY]["headers"] == {
        "authorization": "Bearer real"
    }


def test_queue_error_data_redacts_persisted_request_headers() -> None:
    queue = _queue()
    queued = _queued_context({"authorization": "Bearer secret"})

    queue._on_dequeue_start()
    queue._on_process_error("failed", queued)

    stored = queue._errors[0].data
    assert stored is not None
    assert "Bearer secret" not in json.dumps(stored)
    assert MODEL_REQUEST_CONTEXT_KEY not in json.dumps(stored)


async def test_peek_redacts_persisted_request_headers() -> None:
    queue = _queue()
    queued = _queued_context({"authorization": "Bearer secret"})
    payload = json.loads(queued["data"])
    payload["uri"] = "viking://resources/repo"
    queued["data"] = json.dumps(payload)
    queue._async_agfs.read = AsyncMock(
        return_value=json.dumps(queued).encode()
    )

    result = await queue.peek()

    assert result is not None
    assert "Bearer secret" not in json.dumps(result)
    assert MODEL_REQUEST_CONTEXT_KEY not in json.dumps(result)
