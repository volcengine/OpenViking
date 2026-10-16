# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from openviking_sdk.errors import NotFoundError, PermissionDeniedError, UnavailableError

from langchain_openviking.store import OpenVikingStore
from langchain_openviking.testing import InMemoryOpenVikingClient


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


@pytest.mark.parametrize("operation", ["get", "aget", "put"])
@pytest.mark.parametrize(
    "error",
    [
        UnavailableError("test backend"),
        PermissionDeniedError("test read denied"),
        json.JSONDecodeError("invalid record", "{", 1),
        OSError("test storage read failed"),
    ],
)
def test_store_propagates_record_read_errors_without_writing(monkeypatch, operation, error):
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client)
    store.put(("memory",), "note", {"version": 1})
    original_records = dict(client.records)
    monkeypatch.setattr(client, "read", Mock(side_effect=error))

    with pytest.raises(type(error)) as caught:
        if operation == "put":
            store.put(("memory",), "note", {"version": 2})
        elif operation == "aget":
            asyncio.run(store.aget(("memory",), "note"))
        else:
            store.get(("memory",), "note")

    assert caught.value is error
    assert client.records == original_records


@pytest.mark.parametrize("operation", ["get", "aget", "put"])
@pytest.mark.parametrize("error", [FileNotFoundError("test missing"), NotFoundError("test")])
def test_store_retains_explicit_missing_record_behavior(monkeypatch, operation, error):
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client)
    monkeypatch.setattr(client, "read", Mock(side_effect=error))

    if operation == "put":
        store.put(("memory",), "note", {"version": 1})
        monkeypatch.undo()
        assert store.get(("memory",), "note").value == {"version": 1}
    elif operation == "aget":
        assert asyncio.run(store.aget(("memory",), "note")) is None
    else:
        assert store.get(("memory",), "note") is None
