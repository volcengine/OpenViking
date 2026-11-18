"""A failed native envelope decode does not create an unowned processing delivery."""

import sqlite3

import pytest

from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["not json", '{"id":"old","data":[256]}'])
async def test_named_queue_preserves_pending_until_envelope_can_be_decoded(tmp_path, invalid):
    database = tmp_path / "queue.db"
    native = create_agfs_client(
        RagfsBindingConfig(
            agfs=AGFSConfig(
                path=str(tmp_path / "workspace"),
                backend="local",
                queuefs={"backend": "sqlite", "db_path": str(database)},
            )
        )
    )
    try:
        queue = NamedQueue(native, "/queue", "Synthetic")
        message_id = await queue.enqueue({"synthetic": True}, serial_key="same-subject")
        with sqlite3.connect(database) as db:
            original = db.execute("SELECT data FROM queue_messages").fetchone()[0]
            db.execute("UPDATE queue_messages SET data = ?", (invalid,))
        # The public Python queue owner handles the native serialization error as
        # an empty read; it must not leave a delivery that no handler received.
        assert await queue.dequeue_raw() is None
        status = await queue.get_status()
        assert status.pending == 1 and status.in_progress == 0
        with sqlite3.connect(database) as db:
            assert (
                db.execute("SELECT processing_started_at FROM queue_messages").fetchone()[0] is None
            )
            db.execute("UPDATE queue_messages SET data = ?", (original,))
        delivery = await queue.dequeue_raw()
        assert delivery["id"] == str(message_id)
        await queue.ack(delivery["id"])
        status = await queue.get_status()
        assert status.pending == status.in_progress == 0
    finally:
        native.close()
