from types import SimpleNamespace

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.reindex_processor import ReindexProcessor


@pytest.mark.asyncio
async def test_malformed_durable_message_fails_delivery_instead_of_raising():
    processor = ReindexProcessor(SimpleNamespace())

    # Missing required fields: must fail the delivery, not raise out of
    # on_dequeue - NamedQueue never ACKs deliveries whose handler raises,
    # so an exception here would re-deliver the same poison message forever.
    result = await processor.on_dequeue({"data": {"task_id": "t1"}})
    assert result.outcome is ProcessOutcome.FAILED
    assert result.error

    # Truncated JSON payload.
    result = await processor.on_dequeue({"data": '{"task_id": '})
    assert result.outcome is ProcessOutcome.FAILED

    # Payload that is not an object at all.
    result = await processor.on_dequeue({"data": 42})
    assert result.outcome is ProcessOutcome.FAILED
