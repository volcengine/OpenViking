# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from openviking.storage.queuefs import semantic_msg
from openviking.storage.queuefs.semantic_msg import SemanticMsg


def test_new_message_timestamp_is_own_creation_time(monkeypatch):
    clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(semantic_msg, "datetime", SimpleNamespace(now=lambda: clock))
    fields = {"uri": "viking://resources/a", "context_type": "resource", "account_id": "default"}
    first = SemanticMsg(**fields).to_json()
    first_timestamp = int(clock.timestamp())

    clock += timedelta(minutes=5)
    second = SemanticMsg(**fields)
    assert second.timestamp == int(clock.timestamp())
    restored = SemanticMsg.from_json(first)
    assert restored is not None
    assert restored.timestamp == first_timestamp
    assert SemanticMsg.from_dict(fields).timestamp == second.timestamp
