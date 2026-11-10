from __future__ import annotations

import asyncio
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_openviking")

from langchain_openviking import (
    OpenVikingCommitPolicy,
    OpenVikingRetriever,
    OpenVikingSessionContextAssembler,
    OpenVikingSessionRecorder,
)
from langchain_openviking.client import (
    OpenVikingClientHandle,
    OpenVikingConnection,
)


def test_sync_client_handle_initializes_once_under_concurrency(monkeypatch):
    instances: list[Any] = []

    class SlowSyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        def initialize(self) -> None:
            time.sleep(0.01)

        def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", SlowSyncHTTPClient)
    handle = OpenVikingClientHandle(OpenVikingConnection(url="http://localhost:1933"))

    with ThreadPoolExecutor(max_workers=8) as executor:
        clients = list(executor.map(lambda _index: handle.get(), range(8)))

    assert len(instances) == 1
    assert all(client is instances[0] for client in clients)
    assert copy.copy(handle) is handle
    copied = copy.deepcopy(handle)
    assert copied is not handle
    assert copied.get() is instances[1]
    handle.close()
    copied.close()
    assert all(client.closed for client in instances)


def test_sync_client_handle_recovery_does_not_discard_fresh_client(monkeypatch):
    instances: list[Any] = []
    first_client_calls = threading.Barrier(2)

    class RecoveringSyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.index = len(instances)
            self.closed = False
            instances.append(self)

        def initialize(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        def find(self, **_kwargs: Any) -> dict[str, int]:
            if self.index == 0:
                first_client_calls.wait(timeout=1)
                raise ConnectionError("replace the first client")
            return {"client": self.index}

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", RecoveringSyncHTTPClient)
    handle = OpenVikingClientHandle(OpenVikingConnection(url="http://localhost:1933"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: handle.find(query="recover"), range(2)))

    assert results == [{"client": 1}, {"client": 1}]
    assert len(instances) == 2
    assert instances[0].closed is True
    assert instances[1].closed is False
    handle.close()
    assert instances[1].closed is True


def test_session_scoped_retriever_copies_share_owned_sync_client(monkeypatch):
    instances: list[Any] = []

    class TrackingSyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        def initialize(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        def find(self, **_kwargs: Any) -> dict[str, Any]:
            return {"memories": [], "resources": [], "skills": []}

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", TrackingSyncHTTPClient)
    retriever = OpenVikingRetriever(url="http://localhost:1933")
    session_a = retriever.model_copy(update={"session_id": "session-a"})
    session_b = retriever.model_copy(update={"session_id": "session-b"})

    assert session_a.invoke("first") == []
    assert session_b.invoke("second") == []
    assert len(instances) == 1

    independent = copy.deepcopy(retriever)
    assert independent.invoke("deep-copy") == []
    assert len(instances) == 2

    asyncio.run(retriever.aclose())
    asyncio.run(independent.aclose())

    assert all(client.closed for client in instances)


def test_recorder_deepcopy_preserves_configuration_with_fresh_runtime_state():
    class NonCopyableClient:
        def __deepcopy__(self, _memo: dict[int, Any]) -> None:
            raise AssertionError("injected clients must not be deep-copied")

    client = NonCopyableClient()
    recorder = OpenVikingSessionRecorder(
        client=client,
        extra_headers={"X-Tenant": "tenant-a"},
        commit_policy=OpenVikingCommitPolicy(mode="always"),
        batch_size=25,
    )
    assert recorder.client is client
    recorder._mark_commit_pending("pending-session")
    recorder._async_clients.get(object)

    copied = copy.deepcopy(recorder)

    assert copied is not recorder
    assert copied._connection.client is client
    assert copied._connection.extra_headers == recorder._connection.extra_headers
    assert copied._connection.extra_headers is not recorder._connection.extra_headers
    assert copied.commit_policy == recorder.commit_policy
    assert copied.commit_policy is not recorder.commit_policy
    assert copied.batch_size == 25
    assert copied._pending_commit_sessions == {"pending-session"}
    assert copied._pending_commit_sessions is not recorder._pending_commit_sessions
    assert copied._client_cache is None
    assert copied._client_cache_lock is not recorder._client_cache_lock
    assert copied._pending_commit_lock is not recorder._pending_commit_lock
    assert recorder._async_clients.has_clients()
    assert not copied._async_clients.has_clients()
    assert copied.client is client

    recorder._closed = True
    assert copy.deepcopy(recorder)._closed is True


def test_assembler_deepcopy_preserves_retriever_ownership_with_fresh_runtime_state():
    class NonCopyableClient:
        def __deepcopy__(self, _memo: dict[int, Any]) -> None:
            raise AssertionError("injected clients must not be deep-copied")

    client = NonCopyableClient()
    assembler = OpenVikingSessionContextAssembler(
        client=client,
        target_uri=["viking://resources"],
        token_budget=4096,
    )
    assert assembler._get_client() is client
    assembler._async_clients.get(object)

    copied = copy.deepcopy(assembler)

    assert copied is not assembler
    assert copied._connection.client is client
    assert copied._client_cache is None
    assert copied._client_cache_lock is not assembler._client_cache_lock
    assert assembler._async_clients.has_clients()
    assert not copied._async_clients.has_clients()
    assert copied.token_budget == 4096
    assert copied._owns_retriever is True
    assert copied.retriever is not assembler.retriever
    assert copied.retriever.client is client
    assert copied.retriever.target_uri == ["viking://resources"]
    assert copied.retriever.target_uri is not assembler.retriever.target_uri

    injected_retriever = OpenVikingRetriever(client=client)
    with_injected_retriever = OpenVikingSessionContextAssembler(
        client=client,
        retriever=injected_retriever,
    )
    copied_with_injected_retriever = copy.deepcopy(with_injected_retriever)

    assert copied_with_injected_retriever.retriever is injected_retriever
    assert copied_with_injected_retriever._owns_retriever is False

    assembler._closed = True
    assert copy.deepcopy(assembler)._closed is True


def test_recorder_and_assembler_deepcopy_do_not_share_owned_sync_clients(monkeypatch):
    instances: list[Any] = []

    class TrackingSyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        def initialize(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", TrackingSyncHTTPClient)
    recorder = OpenVikingSessionRecorder(url="http://localhost:1933")
    assembler = OpenVikingSessionContextAssembler(
        url="http://localhost:1933",
        include_recall=False,
    )
    assert recorder.client.get() is instances[0]
    assert assembler._get_client().get() is instances[1]

    copied_recorder = copy.deepcopy(recorder)
    copied_assembler = copy.deepcopy(assembler)
    assert copied_recorder.client.get() is instances[2]
    assert copied_assembler._get_client().get() is instances[3]

    recorder.close()
    asyncio.run(assembler.aclose())
    copied_recorder.close()
    asyncio.run(copied_assembler.aclose())

    assert len(instances) == 4
    assert all(client.closed for client in instances)
