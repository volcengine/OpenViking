# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import contextvars
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from unittest.mock import Mock

import openviking_sdk
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


@pytest.mark.parametrize("operation", ["get", "aget"])
@pytest.mark.parametrize("close_method", ["close", "aclose"])
def test_store_rejects_close_from_its_backend_call(close_method, operation):
    code = """
import asyncio
import sys
from langchain_openviking.store import OpenVikingStore
from langchain_openviking.testing import InMemoryOpenVikingClient

class Client(InMemoryOpenVikingClient):
    reenter = False

    def read(self, uri, offset=0, limit=-1):
        if self.reenter:
            self.reenter = False
            print("entered reentrant read", flush=True)
            if sys.argv[1] == "aclose":
                asyncio.run(store.aclose())
            else:
                store.close()
        return super().read(uri, offset=offset, limit=limit)

client = Client()
store = OpenVikingStore(client=client, index=False)
store.put(("memory",), "note", {"text": "stored"})
client.reenter = True
try:
    if sys.argv[2] == "aget":
        asyncio.run(store.aget(("memory",), "note"))
    else:
        store.get(("memory",), "note")
except RuntimeError as error:
    assert "Cannot close OpenVikingStore from inside an active Store call" in str(error)
else:
    raise AssertionError("reentrant shutdown must fail promptly")

assert store.get(("memory",), "note").value == {"text": "stored"}
store.close()
try:
    store.get(("memory",), "note")
except RuntimeError as error:
    assert "OpenVikingStore is closed" in str(error)
else:
    raise AssertionError("normal shutdown must remain terminal")
print("reentrant shutdown rejected; later use and normal shutdown succeeded")
"""
    source_roots = {
        str(Path(sys.modules[OpenVikingStore.__module__].__file__).resolve().parent.parent),
        str(Path(openviking_sdk.__file__).resolve().parent.parent),
    }
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
        if key in os.environ
    }
    env["PYTHONPATH"] = os.pathsep.join(sorted(source_roots))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, close_method, operation],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        if isinstance(output, bytes):
            output = output.decode()
        assert "entered reentrant read" in output, "child did not reach the callback"
        pytest.fail(f"{close_method} blocked inside the {operation} backend call", pytrace=False)
    assert result.returncode == 0, result.stderr
    assert "later use and normal shutdown succeeded" in result.stdout


def test_store_can_close_from_a_completed_calls_copied_context():
    contexts = []
    read_started = threading.Event()
    release_read = threading.Event()

    class Client(InMemoryOpenVikingClient):
        capture_next = False
        pause_next = False

        def read(self, uri, offset=0, limit=-1):
            if self.capture_next:
                self.capture_next = False
                contexts.append(contextvars.copy_context())
            if self.pause_next:
                self.pause_next = False
                read_started.set()
                assert release_read.wait(timeout=5), "read was not released"
            return super().read(uri, offset=offset, limit=limit)

    client = Client()
    store = OpenVikingStore(client=client, index=False)
    store.put(("memory",), "note", {"text": "stored"})
    client.capture_next = True
    assert store.get(("memory",), "note").value == {"text": "stored"}
    client.pause_next = True
    with ThreadPoolExecutor(max_workers=2) as executor:
        read = executor.submit(store.get, ("memory",), "note")
        try:
            assert read_started.wait(timeout=2)
            closing = executor.submit(contexts[0].run, store.close)
            with pytest.raises(FutureTimeoutError):
                closing.result(timeout=0.1)
        finally:
            release_read.set()
        assert read.result(timeout=2).value == {"text": "stored"}
        assert closing.result(timeout=2) is None
    with pytest.raises(RuntimeError, match="OpenVikingStore is closed"):
        store.get(("memory",), "note")


def test_store_backend_call_can_close_a_different_store():
    other = OpenVikingStore(client=InMemoryOpenVikingClient())

    class Client(InMemoryOpenVikingClient):
        close_other = False

        def read(self, uri, offset=0, limit=-1):
            if self.close_other:
                self.close_other = False
                other.close()
            return super().read(uri, offset=offset, limit=limit)

    client = Client()
    store = OpenVikingStore(client=client, index=False)
    store.put(("memory",), "note", {"text": "stored"})
    client.close_other = True
    assert store.get(("memory",), "note").value == {"text": "stored"}
    with pytest.raises(RuntimeError, match="OpenVikingStore is closed"):
        other.get(("memory",), "note")
    store.close()


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
        assert type(caught.value) is type(expected_error)
        assert caught.value.args == expected_error.args
        assert str(caught.value) == str(expected_error)
        assert getattr(caught.value, "code", None) == getattr(expected_error, "code", None)
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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation", ["search", "list_namespaces"])
@pytest.mark.parametrize("failed_pattern", ["*.json", "**/*.json"])
@pytest.mark.parametrize(
    "error",
    [UnavailableError("test listing"), PermissionDeniedError("test listing"), TimeoutError()],
)
def test_store_propagates_listing_errors(
    monkeypatch, operation, error, failed_pattern, asynchronous
):
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client)
    store.put(("memory",), "note", {"version": 1})
    original_records = dict(client.records)
    original_glob = client.glob

    def glob(pattern, uri):
        if pattern == failed_pattern:
            raise error
        return original_glob(pattern, uri)

    monkeypatch.setattr(client, "glob", glob)
    method = getattr(store, ("a" if asynchronous else "") + operation)
    args = (("memory",),) if operation == "search" else ()
    with pytest.raises(type(error)) as caught:
        result = method(*args)
        if asynchronous:
            asyncio.run(result)
    assert caught.value.args == error.args
    assert client.records == original_records


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation", ["search", "list_namespaces"])
@pytest.mark.parametrize(
    "error", [FileNotFoundError("missing root"), NotFoundError("missing root")]
)
def test_store_returns_empty_for_missing_listing_root(monkeypatch, operation, error, asynchronous):
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client)
    monkeypatch.setattr(client, "glob", Mock(side_effect=error))
    method = getattr(store, ("a" if asynchronous else "") + operation)
    result = method(("memory",)) if operation == "search" else method()
    assert (asyncio.run(result) if asynchronous else result) == []


NAMESPACES = (
    ("users", "ada", "prefs"),
    ("users", "grace", "prefs"),
    ("users", "ada", "events"),
    ("teams", "ops", "prefs"),
    ("users",),
    ("users", "ada", "prefs", "v2"),
    ("users", "a*b", "prefs"),
    ("用户", "小王", "prefs"),
    ("teams", "ops/platform", "prefs"),
    ("teams", r"ops\platform", "prefs"),
    ("teams", "ops!splatform", "prefs"),
    ("teams", "ops!bplatform", "prefs"),
    ("teams", "ops", "platform", "prefs"),
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


@pytest.mark.parametrize("operation", ["put", "aput", "batch", "abatch"])
@pytest.mark.parametrize("use_default", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_store_keeps_namespace_validation_and_persisted_values(
    stores, operation, use_default, existing
):
    store, _ = stores
    with pytest.raises(InvalidNamespaceError, match="Namespace cannot be empty"):
        store.put((), "root", {"number": -1})
    assert store.list_namespaces() == sorted(NAMESPACES)
    for number, namespace in enumerate(NAMESPACES):
        assert store.get(namespace, "item").value == {"number": number}

    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client, index=[] if use_default else None)
    namespace, key = ("memory",), "excluded-note"
    if existing:
        store.put(namespace, key, {"text": "old"}, index=["text"])
    value = {"text": "still stored"}
    index: list[str] | None = None if use_default else []
    if operation == "put":
        store.put(namespace, key, value, index=index)
    elif operation == "aput":
        asyncio.run(store.aput(namespace, key, value, index=index))
    else:
        ops = [PutOp(namespace, key, value, index=index)]
        asyncio.run(store.abatch(ops)) if operation == "abatch" else store.batch(ops)

    assert store.get(namespace, key).value == value
    assert [item.key for item in store.search(namespace)] == [key]
    assert not any("/index/" in uri for uri in client.records)
    assert store.search(namespace, query=key) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("initialized", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("context_manager", [False, True])
async def test_store_closes_only_owned_clients(
    monkeypatch, owned, initialized, asynchronous, context_manager
):
    instances = []

    class Client(InMemoryOpenVikingClient):
        def __init__(self, **kwargs):
            super().__init__()
            self.close_count = 0
            instances.append(self)

        def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
            try:
                return super().read(uri, offset=offset, limit=limit)
            except FileNotFoundError:
                raise NotFoundError(uri)

        def close(self):
            self.close_count += 1
            super().close()

    monkeypatch.setattr(openviking_sdk, "SyncHTTPClient", Client)
    store = OpenVikingStore(url="http://127.0.0.1:1933", client=None if owned else Client())

    async def use_store():
        if initialized:
            if asynchronous:
                await store.aput(("memory",), "note", {"text": "stored"})
            else:
                store.put(("memory",), "note", {"text": "stored"})

    if context_manager:
        if asynchronous:
            async with store:
                await use_store()
        else:
            with store:
                await use_store()
    else:
        await use_store()
        if asynchronous:
            await store.aclose()
        else:
            store.close()

    await store.aclose()
    store.close()
    assert len(instances) == (1 if initialized or not owned else 0)
    assert all(client.close_count == (1 if owned else 0) for client in instances)
    for operation in (
        lambda: store.get(("memory",), "note"),
        lambda: store.put(("memory",), "note", {"text": "after close"}),
        lambda: store.delete(("memory",), "note"),
        lambda: store.search(("memory",)),
        lambda: store.search(("memory",), query="note"),
        store.list_namespaces,
        lambda: store.batch([]),
    ):
        with pytest.raises(RuntimeError, match="OpenVikingStore is closed"):
            operation()
    with pytest.raises(RuntimeError, match="OpenVikingStore is closed"):
        await store.aget(("memory",), "note")


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_point", ["initialize", "safe-read-retry"])
async def test_store_async_context_drains_cancelled_client_operations(
    monkeypatch, tmp_path, pause_point
):
    loop = asyncio.get_running_loop()
    operation_paused = asyncio.Event()
    cleanup_started = asyncio.Event()
    read_finished = asyncio.Event()
    resume_operation = threading.Event()
    context_exited = threading.Event()
    instances = []
    reads_after_exit = []
    sdk_client = openviking_sdk.SyncHTTPClient
    config = tmp_path / "ovcli.conf"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config))

    class PausedClient(sdk_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.close_count = 0
            self.transport = None
            self.index = len(instances)
            instances.append(self)

        def initialize(self):
            super().initialize()
            self.transport = self._async_client._http
            if pause_point == "initialize":
                loop.call_soon_threadsafe(operation_paused.set)
                assert resume_operation.wait(timeout=5), "initialization was not released"

        def read(self, *args, **kwargs):
            if pause_point == "safe-read-retry" and self.index == 0:
                loop.call_soon_threadsafe(operation_paused.set)
                assert resume_operation.wait(timeout=5), "read was not released"
                raise ConnectionError("synthetic retryable read failure")
            try:
                if context_exited.is_set():
                    reads_after_exit.append(args)
                return json.dumps(
                    {
                        "namespace": ["memory"],
                        "key": "note",
                        "value": {"text": "stored"},
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                )
            finally:
                loop.call_soon_threadsafe(read_finished.set)

        def close(self):
            self.close_count += 1
            super().close()

    monkeypatch.setattr(openviking_sdk, "SyncHTTPClient", PausedClient)
    store = OpenVikingStore(
        url="http://127.0.0.1:1933", api_key="synthetic-owner-key", auth_mode="api_key"
    )

    async def use_store():
        async with store:
            request = asyncio.create_task(store.aget(("memory",), "note"))
            await asyncio.wait_for(operation_paused.wait(), timeout=5)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            cleanup_started.set()
        context_exited.set()

    context = asyncio.create_task(use_store())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=5)
        # The cancelled awaiter leaves the SDK operation running in its worker.
        completed, _ = await asyncio.wait({context}, timeout=0.1)
        resume_operation.set()
        await asyncio.wait_for(asyncio.shield(context), timeout=5)
        if completed:
            await asyncio.wait_for(read_finished.wait(), timeout=5)
        await store.aclose()
        store.close()

        assert len(instances) == (1 if pause_point == "initialize" else 2)
        assert all(client.transport is not None for client in instances)
        assert (
            tuple(client.close_count for client in instances),
            tuple(client.transport.is_closed for client in instances),
            bool(completed),
        ) == ((1,) * len(instances), (True,) * len(instances), False)
        assert reads_after_exit == []
        with pytest.raises(RuntimeError, match="OpenVikingStore is closed"):
            await store.aget(("memory",), "note")
    finally:
        resume_operation.set()
        await asyncio.gather(context, return_exceptions=True)
        for client in instances:
            if client.transport is not None and not client.transport.is_closed:
                await asyncio.to_thread(sdk_client.close, client)
