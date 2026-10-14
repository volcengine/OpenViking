"""The public Store.put(None) contract removes records rather than storing null."""

import asyncio

import pytest
from langchain_openviking import InMemoryOpenVikingClient, OpenVikingStore
from langgraph.store.base import PutOp


@pytest.mark.parametrize("method", ["put", "aput", "batch", "abatch", "delete"])
@pytest.mark.parametrize("existing", [False, True])
def test_none_deletes_through_public_store_methods(method: str, existing: bool) -> None:
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    namespace = ("users", "ada", "memories")
    other_namespace = ("users", "grace", "memories")
    store.put(other_namespace, "keep", {"text": "independent memory"})
    if existing:
        store.put(namespace, "item", {"text": "memory to remove"})

    if method == "put":
        store.put(namespace, "item", None)
    elif method == "aput":
        asyncio.run(store.aput(namespace, "item", None))
    elif method == "batch":
        store.batch([PutOp(namespace, "item", None)])
    elif method == "abatch":
        asyncio.run(store.abatch([PutOp(namespace, "item", None)]))
    else:
        store.delete(namespace, "item")

    assert store.get(namespace, "item") is None
    assert store.search(namespace) == []
    assert store.search(namespace, query="memory to remove") == []
    assert store.list_namespaces() == [other_namespace]
    assert store.get(other_namespace, "keep").value == {"text": "independent memory"}


@pytest.mark.parametrize("index", [False, ["text"]])
@pytest.mark.parametrize("existing", [False, True])
def test_put_none_deletes_with_index_overrides(index: bool | list[str], existing: bool) -> None:
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    namespace = ("users", "ada")
    if existing:
        store.put(namespace, "item", {"text": "memory to remove"})

    store.put(namespace, "item", None, index=index)

    assert store.get(namespace, "item") is None
    assert store.search(namespace) == []
    assert store.search(namespace, query="memory to remove") == []
    assert store.list_namespaces() == []


@pytest.mark.parametrize("method", ["put", "aput"])
@pytest.mark.parametrize("value", [{}, {"number": 0}, {"text": "saved memory"}])
def test_dictionary_values_remain_stored(method: str, value: dict) -> None:
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    namespace = ("users", "ada")
    if method == "put":
        store.put(namespace, "item", value)
    else:
        asyncio.run(store.aput(namespace, "item", value))

    assert store.get(namespace, "item").value == value
    assert store.search(namespace)[0].value == value
    assert store.list_namespaces() == [namespace]


@pytest.mark.parametrize("method", ["put", "aput"])
@pytest.mark.parametrize("value", [None, {"text": "replacement"}])
def test_unsupported_ttl_still_rejects_before_mutation(method: str, value: dict | None) -> None:
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    namespace = ("users", "ada")
    original = {"text": "original memory"}
    store.put(namespace, "item", original)

    with pytest.raises(NotImplementedError, match="TTL"):
        if method == "put":
            store.put(namespace, "item", value, ttl=60)
        else:
            asyncio.run(store.aput(namespace, "item", value, ttl=60))

    assert store.get(namespace, "item").value == original
