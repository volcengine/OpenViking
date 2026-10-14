from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

from langchain_openviking import InMemoryOpenVikingClient, OpenVikingStore
from langgraph.store.base import ListNamespacesOp, MatchCondition
from langgraph.store.memory import InMemoryStore

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


def test_store_keeps_empty_namespace_and_persisted_values(stores):
    store, _ = stores
    store.put((), "root", {"number": -1})
    assert store.list_namespaces() == sorted([(), *NAMESPACES])
    for number, namespace in enumerate(NAMESPACES):
        assert store.get(namespace, "item").value == {"number": number}
    assert store.get((), "root").value == {"number": -1}
