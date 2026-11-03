# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
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
from openviking_sdk.errors import (
    AlreadyExistsError,
    NotFoundError,
    PermissionDeniedError,
    UnavailableError,
)

from langchain_openviking.store import OpenVikingStore
from langchain_openviking.testing import InMemoryOpenVikingClient


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "create_error,replace_error",
    [
        (None, None),
        (FileExistsError("index exists"), None),
        (AlreadyExistsError("index"), None),
        (UnavailableError("index writer"), None),
        (PermissionDeniedError("index denied"), None),
        (TimeoutError("index timed out"), None),
        (FileExistsError("index exists"), PermissionDeniedError("replace denied")),
        (AlreadyExistsError("index"), UnavailableError("replace writer")),
    ],
)
def test_store_preserves_created_at_and_reports_index_write_errors(
    create_error, replace_error, asynchronous
):
    class Client(InMemoryOpenVikingClient):
        fail = False

        def write(
            self, uri: str, content: str, mode: str = "replace", wait: bool = False, **kwargs
        ):
            if self.fail and "/index/" in uri:
                error = create_error if mode == "create" else replace_error
                if error is not None:
                    raise error
            if mode == "create" and uri in self.records:
                mode = "replace"  # Current server create behavior is an upsert.
            return super().write(uri, content, mode=mode, wait=wait, **kwargs)

    client = Client()
    store = OpenVikingStore(client=client)
    store.put(("memory",), "note", {"version": 1})
    before = store.get(("memory",), "note")
    old_index = {uri: text for uri, text in client.records.items() if "/index/" in uri}
    client.fail = True
    conflict = isinstance(create_error, FileExistsError) or isinstance(
        create_error, AlreadyExistsError
    )
    expected_error = replace_error if conflict else create_error

    def update():
        if asynchronous:
            asyncio.run(store.aput(("memory",), "note", {"version": 2}))
        else:
            store.put(("memory",), "note", {"version": 2})

    if expected_error is None:
        update()
    else:
        with pytest.raises(type(expected_error)) as caught:
            update()
        assert caught.value is expected_error
        assert {uri: text for uri, text in client.records.items() if "/index/" in uri} == old_index
    after = store.get(("memory",), "note")
    assert after.value == {"version": 2}
    assert after.created_at == before.created_at
    if expected_error is None:
        assert any(
            '"version": 2' in text for uri, text in client.records.items() if "/index/" in uri
        )
        assert [item.key for item in store.search(("memory",), query='"version": 2')] == ["note"]


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
