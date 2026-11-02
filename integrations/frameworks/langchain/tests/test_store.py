# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from langgraph.store.base import (
    GetOp,
    InvalidNamespaceError,
    ListNamespacesOp,
    MatchCondition,
    PutOp,
)
from langgraph.store.memory import InMemoryStore
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


async def _read_or_update(store, operation, asynchronous):
    namespace, key = ("memory",), "note"
    if operation in {"batch-get", "batch-put"}:
        op = (
            GetOp(namespace, key)
            if operation == "batch-get"
            else PutOp(namespace, key, {"version": 2})
        )
        return (await store.abatch([op]) if asynchronous else store.batch([op]))[0]
    if operation == "put":
        return (
            await store.aput(namespace, key, {"version": 2})
            if asynchronous
            else store.put(namespace, key, {"version": 2})
        )
    if operation in {"search", "semantic"}:
        kwargs = {"query": "version"} if operation == "semantic" else {}
        return (
            await store.asearch(namespace, **kwargs)
            if asynchronous
            else store.search(namespace, **kwargs)
        )
    return await store.aget(namespace, key) if asynchronous else store.get(namespace, key)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "operation", ["get", "put", "batch-get", "batch-put", "search", "semantic"]
)
@pytest.mark.parametrize(
    "error",
    [
        UnavailableError("test backend"),
        PermissionDeniedError("test read denied"),
        json.JSONDecodeError("invalid record", "{", 1),
        OSError("test storage read failed"),
    ],
)
def test_store_propagates_record_read_errors_without_writing(
    monkeypatch, operation, error, asynchronous
):
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client)
    store.put(("memory",), "note", {"version": 1})
    original_records = dict(client.records)
    monkeypatch.setattr(client, "read", Mock(side_effect=error))

    with pytest.raises(type(error)) as caught:
        asyncio.run(_read_or_update(store, operation, asynchronous))

    assert caught.value is error
    assert client.records == original_records
    monkeypatch.undo()
    assert store.get(("memory",), "note").value == {"version": 1}


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "operation", ["get", "put", "batch-get", "batch-put", "search", "semantic"]
)
@pytest.mark.parametrize("error", [FileNotFoundError("test missing"), NotFoundError("test")])
def test_store_retains_explicit_missing_record_behavior(
    monkeypatch, operation, error, asynchronous
):
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client)
    if operation not in {"put", "batch-put"}:
        store.put(("memory",), "note", {"version": 1})
    monkeypatch.setattr(client, "read", Mock(side_effect=error))

    result = asyncio.run(_read_or_update(store, operation, asynchronous))
    if operation in {"put", "batch-put"}:
        monkeypatch.undo()
        assert store.get(("memory",), "note").value == {"version": 2}
    elif operation in {"search", "semantic"}:
        assert result == []
    else:
        assert result is None


NAMESPACES = (
    ("users", "ada", "prefs"),
    ("users", "grace", "prefs"),
    ("users", "ada", "events"),
    ("teams", "ops", "prefs"),
    ("users",),
    ("users", "ada", "prefs", "v2"),
    ("users", "a*b", "prefs"),
    ("用户", "小王", "prefs"),
)


@pytest.fixture
def stores():
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    reference = InMemoryStore()
    for number, namespace in enumerate(NAMESPACES):
        value = {"number": number}
        store.put(namespace, "item", value)
        reference.put(namespace, "item", value)
    return store, reference


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "query",
    [
        {},
        {"prefix": ("users",)},
        {"suffix": ("prefs",)},
        {"prefix": ("users", "ada")},
        {"prefix": ("users", "*")},
        {"prefix": ("*", "ada")},
        {"suffix": ("*", "prefs")},
        {"suffix": ("users", "*", "prefs")},
        {"prefix": ("users", "*"), "suffix": ("prefs",)},
        {"prefix": ("users", "*", "prefs"), "max_depth": 2},
        {"prefix": ("users", "*"), "limit": 2, "offset": 1},
        {"prefix": ("users", "a*b")},
        {"prefix": ("users", "a*")},
        {"prefix": ("users", "**")},
        {"prefix": (), "suffix": ()},
        {"prefix": ("用户",)},
        {"prefix": ("用户", "*")},
    ],
)
def test_store_namespace_queries_match_langgraph(stores, query, asynchronous):
    store, reference = stores
    expected = reference.list_namespaces(**query)
    actual = (
        asyncio.run(store.alist_namespaces(**query))
        if asynchronous
        else store.list_namespaces(**query)
    )
    assert actual == expected


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "conditions",
    [
        (MatchCondition("prefix", ("users", "*")), MatchCondition("prefix", ("users", "ada"))),
        (MatchCondition("prefix", ("teams",)), MatchCondition("prefix", ("users",))),
        (MatchCondition("suffix", ("events",)), MatchCondition("suffix", ("prefs",))),
        (MatchCondition("prefix", ("users", "*")), MatchCondition("suffix", ("*", "prefs"))),
    ],
)
def test_store_namespace_batch_applies_all_conditions(stores, conditions, asynchronous):
    store, reference = stores
    op = ListNamespacesOp(match_conditions=conditions)
    expected = reference.batch([op])
    actual = asyncio.run(store.abatch([op])) if asynchronous else store.batch([op])
    assert actual == expected


def test_store_namespace_batch_rejects_unknown_match_type(stores):
    store, reference = stores
    op = ListNamespacesOp(match_conditions=(MatchCondition("unknown", ("users",)),))
    for implementation in (reference, store):
        with pytest.raises(ValueError, match="Unsupported match type: unknown"):
            implementation.batch([op])


@pytest.mark.parametrize("max_depth", [0, -1])
def test_store_keeps_existing_nonpositive_namespace_depth_behavior(stores, max_depth):
    store, _ = stores
    assert store.list_namespaces(max_depth=max_depth) == sorted(
        {namespace[:max_depth] for namespace in NAMESPACES}
    )


def test_store_keeps_namespace_validation_and_persisted_values(stores):
    store, _ = stores
    with pytest.raises(InvalidNamespaceError, match="Namespace cannot be empty"):
        store.put((), "root", {"number": -1})
    assert store.list_namespaces() == sorted(NAMESPACES)
    for number, namespace in enumerate(NAMESPACES):
        assert store.get(namespace, "item").value == {"number": number}
