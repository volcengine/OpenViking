# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timezone
from unittest.mock import Mock

from langchain_openviking.store import OpenVikingStore


def test_write_record_preserves_created_at_when_create_is_an_upsert_alias():
    store = object.__new__(OpenVikingStore)
    created_at = "2026-01-01T00:00:00+00:00"
    store._read_record = Mock(return_value={"created_at": created_at})
    store._write_create = Mock()
    store._write_replace = Mock()

    record = store._write_record(
        "viking://user/default/langgraph/data/ns/key.json",
        ("ns",),
        "key",
        {"version": 2},
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert record["created_at"] == created_at
    assert record["updated_at"] == "2026-01-02T00:00:00+00:00"
    store._write_create.assert_not_called()
    store._write_replace.assert_called_once()
