from __future__ import annotations

import asyncio
import copy
import sys
import threading
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openviking import (
    InMemoryOpenVikingClient,
    OpenVikingCancellationProgress,
    OpenVikingChatMessageHistory,
    OpenVikingCommitPolicy,
    OpenVikingContextMiddleware,
    OpenVikingPartialWriteError,
    OpenVikingRetriever,
    OpenVikingSessionContextAssembler,
    OpenVikingSessionRecorder,
    get_openviking_cancellation_progress,
)
from langchain_openviking.client import (
    OpenVikingAsyncClientHandle,
    OpenVikingConnection,
    acall_openviking,
    ensure_async_client,
)


class AsyncInMemoryOpenVikingClient:
    """Async facade that records whether adapters use native coroutine methods."""

    def __init__(self, backing: InMemoryOpenVikingClient | None = None):
        self.backing = backing or InMemoryOpenVikingClient()
        self.calls: list[str] = []
        self.call_thread_ids: list[int] = []
        self._initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self._initialized = True

    async def close(self) -> None:
        self.closed = True
        self._initialized = False

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.backing, name)
        if not callable(method):
            return method

        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            self.call_thread_ids.append(threading.get_ident())
            return method(*args, **kwargs)

        return call


def test_connection_handle_and_retriever_deepcopy_preserve_injected_clients():
    class NonCopyableClient:
        def __deepcopy__(self, _memo: dict[int, Any]) -> None:
            raise AssertionError("live clients must not be deep-copied")

    client = NonCopyableClient()
    async_client = NonCopyableClient()
    connection = OpenVikingConnection(
        client=client,
        async_client=async_client,
        url="http://localhost:1933",
        extra_headers={"X-Tenant": "tenant-a"},
    )

    copied_connection = copy.deepcopy(connection)

    assert copied_connection is not connection
    assert copied_connection.client is client
    assert copied_connection.async_client is async_client
    assert copied_connection.extra_headers == connection.extra_headers
    assert copied_connection.extra_headers is not connection.extra_headers

    async_handle = OpenVikingAsyncClientHandle(connection)
    async_handle._client = async_client
    copied_async_handle = copy.deepcopy(async_handle)

    assert copied_async_handle is not async_handle
    assert copied_async_handle._connection.async_client is async_client
    assert copied_async_handle._client is None

    retriever = OpenVikingRetriever(
        client=client,
        async_client=async_client,
        extra_headers={"X-Tenant": "tenant-a"},
        target_uri=["viking://resources"],
        filter={"category": ["guide"]},
        tags=["stable"],
    )
    copied_retriever = copy.deepcopy(retriever)

    assert copied_retriever is not retriever
    assert copied_retriever.client is client
    assert copied_retriever.async_client is async_client
    assert copied_retriever.extra_headers == retriever.extra_headers
    assert copied_retriever.extra_headers is not retriever.extra_headers
    assert copied_retriever.target_uri == retriever.target_uri
    assert copied_retriever.target_uri is not retriever.target_uri
    assert copied_retriever.filter == retriever.filter
    assert copied_retriever.filter is not retriever.filter
    assert copied_retriever.tags == retriever.tags
    assert copied_retriever.tags is not retriever.tags


def test_retriever_deepcopy_discards_owned_sync_client(monkeypatch):
    instances: list[Any] = []

    class NonCopyableSyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        def __deepcopy__(self, _memo: dict[int, Any]) -> None:
            raise AssertionError("owned live clients must not be deep-copied")

        def initialize(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        def find(self, **_kwargs: Any) -> dict[str, Any]:
            return {"memories": [], "resources": [], "skills": []}

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", NonCopyableSyncHTTPClient)
    retriever = OpenVikingRetriever(url="http://localhost:1933")

    assert retriever.invoke("original") == []
    copied = copy.deepcopy(retriever)
    assert copied.invoke("copied") == []

    assert len(instances) == 2
    asyncio.run(copied.aclose())
    asyncio.run(retriever.aclose())
    assert all(client.closed for client in instances)


@pytest.mark.asyncio
async def test_ensure_async_client_defaults_to_native_http_client(monkeypatch):
    created: dict[str, Any] = {}

    class FakeAsyncHTTPClient:
        def __init__(self, **kwargs: Any):
            created.update(kwargs)
            self.initialized = False

        async def initialize(self) -> None:
            self.initialized = True

        async def find(self, query: str) -> dict[str, str]:
            return {"query": query}

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FakeAsyncHTTPClient)

    client = await ensure_async_client(
        OpenVikingConnection(api_key="test-key", user_id="test-user")
    )

    assert await acall_openviking(
        client,
        "find",
        query="async",
        unsupported="ignored",
    ) == {"query": "async"}
    assert created["api_key"] == "test-key"
    assert created["user_id"] == "test-user"
    assert created["url"] is None
    assert (await client.get()).initialized is True


@pytest.mark.asyncio
async def test_injected_async_client_is_initialized_only_once_across_adapters():
    class AsyncClientWithoutInitializedFlag:
        def __init__(self):
            self.initialize_calls = 0

        async def initialize(self) -> None:
            await asyncio.sleep(0.01)
            self.initialize_calls += 1

    client = AsyncClientWithoutInitializedFlag()
    connection = OpenVikingConnection(async_client=client)

    clients = await asyncio.gather(*(ensure_async_client(connection) for _ in range(5)))

    assert all(resolved is client for resolved in clients)
    assert client.initialize_calls == 1


@pytest.mark.asyncio
async def test_shared_injected_async_client_initializes_once_across_real_adapters():
    class SharedAsyncClient:
        def __init__(self):
            self.initialize_calls = 0
            self.closed = False

        async def initialize(self) -> None:
            await asyncio.sleep(0.01)
            self.initialize_calls += 1

        async def close(self) -> None:
            self.closed = True

    client = SharedAsyncClient()
    recorder = OpenVikingSessionRecorder(async_client=client)
    retriever = OpenVikingRetriever(async_client=client)

    resolved = await asyncio.gather(
        recorder.get_async_client(),
        retriever.get_async_client(),
    )

    assert resolved == [client, client]
    assert client.initialize_calls == 1

    await recorder.aclose()
    await retriever.aclose()
    assert client.closed is False


@pytest.mark.asyncio
async def test_injected_async_client_respects_disabled_auto_initialize():
    class AsyncClient:
        def __init__(self):
            self.initialize_calls = 0

        async def initialize(self) -> None:
            self.initialize_calls += 1

    client = AsyncClient()

    assert (
        await ensure_async_client(
            OpenVikingConnection(
                async_client=client,
                auto_initialize=False,
            )
        )
        is client
    )
    assert client.initialize_calls == 0


@pytest.mark.asyncio
async def test_async_client_handle_initializes_once_under_concurrency(monkeypatch):
    instances: list[Any] = []

    class SlowAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        async def initialize(self) -> None:
            await asyncio.sleep(0.01)

        async def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", SlowAsyncHTTPClient)
    handle = OpenVikingAsyncClientHandle(OpenVikingConnection(url="http://localhost:1933"))

    clients = await asyncio.gather(*(handle.get() for _ in range(5)))

    assert len(instances) == 1
    assert all(client is clients[0] for client in clients)
    await handle.close()
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_async_client_initialization_failure_closes_candidate(monkeypatch):
    instances: list[Any] = []

    class FailingAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        async def initialize(self) -> None:
            raise RuntimeError("initialization failed")

        async def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FailingAsyncHTTPClient)

    with pytest.raises(RuntimeError, match="initialization failed"):
        await ensure_async_client(OpenVikingConnection(url="http://localhost:1933"))

    assert len(instances) == 1
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_async_adapter_client_caches_initialize_once_under_concurrency(monkeypatch):
    instances: list[Any] = []

    class SlowAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        async def initialize(self) -> None:
            await asyncio.sleep(0.01)

        async def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", SlowAsyncHTTPClient)
    recorder = OpenVikingSessionRecorder(url="http://localhost:1933")
    assembler = OpenVikingSessionContextAssembler(
        url="http://localhost:1933",
        include_recall=False,
    )
    retriever = OpenVikingRetriever(url="http://localhost:1933")

    recorder_clients = await asyncio.gather(*(recorder.get_async_client() for _ in range(5)))
    assembler_clients = await asyncio.gather(*(assembler.get_async_client() for _ in range(5)))
    retriever_clients = await asyncio.gather(*(retriever.get_async_client() for _ in range(5)))

    assert len(instances) == 3
    assert all(client is recorder_clients[0] for client in recorder_clients)
    assert all(client is assembler_clients[0] for client in assembler_clients)
    assert all(client is retriever_clients[0] for client in retriever_clients)

    await assembler.retriever.get_async_client()
    assert len(instances) == 4

    await recorder.aclose()
    await assembler.aclose()
    await retriever.aclose()
    assert all(client.closed for client in instances)


def test_async_adapter_clients_are_scoped_per_event_loop(monkeypatch):
    instances: list[Any] = []

    class LoopBoundAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.loop: asyncio.AbstractEventLoop | None = None
            self.closed = False
            self.writes = 0
            instances.append(self)

        async def initialize(self) -> None:
            await asyncio.sleep(0.01)
            self.loop = asyncio.get_running_loop()

        async def batch_add_messages(self, **_kwargs: Any) -> dict[str, Any]:
            if asyncio.get_running_loop() is not self.loop:
                raise RuntimeError("client used from a different event loop")
            self.writes += 1
            return {}

        async def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", LoopBoundAsyncHTTPClient)
    recorder = OpenVikingSessionRecorder(url="http://localhost:1933")

    async def use_recorder(session_id: str) -> tuple[Any, Any]:
        handles = await asyncio.gather(*(recorder.get_async_client() for _ in range(8)))
        assert all(handle is handles[0] for handle in handles)
        client = await handles[0].get()
        await recorder.arecord(session_id, [HumanMessage(content=session_id)])
        return handles[0], client

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:
        handle_a, client_a = loop_a.run_until_complete(use_recorder("loop-a"))
        handle_b, client_b = loop_b.run_until_complete(use_recorder("loop-b"))

        assert handle_a is not handle_b
        assert client_a is not client_b
        assert len(instances) == 2
        assert [client.writes for client in instances] == [1, 1]

        loop_b.run_until_complete(recorder.aclose())
        assert all(client.closed for client in instances)
    finally:
        loop_a.close()
        loop_b.close()


def test_async_adapter_aclose_after_originating_loop_ends_is_best_effort(monkeypatch):
    class LoopBoundAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            pass

        async def initialize(self) -> None:
            await asyncio.sleep(0.01)

        async def close(self) -> None:
            raise RuntimeError("event loop is closed")

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", LoopBoundAsyncHTTPClient)
    recorder = OpenVikingSessionRecorder(url="http://localhost:1933")

    async def initialize_with_contention() -> None:
        await asyncio.gather(*(recorder.get_async_client() for _ in range(4)))

    asyncio.run(initialize_with_contention())
    asyncio.run(recorder.aclose())
    assert recorder._closed is True


def test_async_adapter_aclose_routes_to_live_originating_loop(monkeypatch):
    instances: list[Any] = []

    class LoopBoundAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.initialized_loop: asyncio.AbstractEventLoop | None = None
            self.closed_loop: asyncio.AbstractEventLoop | None = None
            instances.append(self)

        async def initialize(self) -> None:
            self.initialized_loop = asyncio.get_running_loop()

        async def close(self) -> None:
            self.closed_loop = asyncio.get_running_loop()

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", LoopBoundAsyncHTTPClient)
    recorder = OpenVikingSessionRecorder(url="http://localhost:1933")
    worker_loop = asyncio.new_event_loop()
    ready = threading.Event()
    worker_errors: list[BaseException] = []

    async def initialize() -> None:
        await recorder.get_async_client()

    def run_worker_loop() -> None:
        asyncio.set_event_loop(worker_loop)
        try:
            try:
                worker_loop.run_until_complete(initialize())
            except BaseException as exc:
                worker_errors.append(exc)
                return
            ready.set()
            worker_loop.run_forever()
        finally:
            ready.set()
            asyncio.set_event_loop(None)

    thread = threading.Thread(target=run_worker_loop)
    thread.start()
    assert ready.wait(timeout=2)
    try:
        assert not worker_errors
        asyncio.run(recorder.aclose())
    finally:
        worker_loop.call_soon_threadsafe(worker_loop.stop)
        thread.join(timeout=2)
        worker_loop.close()

    assert not thread.is_alive()
    assert len(instances) == 1
    assert instances[0].initialized_loop is worker_loop
    assert instances[0].closed_loop is worker_loop


@pytest.mark.asyncio
async def test_async_handle_copy_and_missing_attribute_behavior(monkeypatch):
    class FakeAsyncHTTPClient:
        marker = "async-client"

        def __init__(self, **_kwargs: Any):
            self.closed = False

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FakeAsyncHTTPClient)
    retriever = OpenVikingRetriever(url="http://localhost:1933")
    handle = await retriever.get_async_client()

    copied = copy.deepcopy(retriever)
    assert not copied._async_clients.has_clients()
    copied_handle = await copied.get_async_client()

    assert isinstance(copied_handle, OpenVikingAsyncClientHandle)
    assert copied_handle is not handle
    assert (await copied_handle.get()).marker == "async-client"
    assert handle.marker == "async-client"
    assert not hasattr(handle, "definitely_not_an_openviking_client_attribute")
    await copied.aclose()
    await retriever.aclose()


@pytest.mark.asyncio
async def test_async_handle_method_lookup_survives_recovery_reset(monkeypatch):
    instances: list[Any] = []
    reset_started = asyncio.Event()
    release_close = asyncio.Event()

    class RecoveringAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.index = len(instances)
            instances.append(self)

        async def initialize(self) -> None:
            return None

        async def find(self, **kwargs: Any) -> dict[str, Any]:
            if self.index == 0:
                raise ConnectionError("replace this client")
            return {"query": kwargs["query"], "client": self.index}

        async def close(self) -> None:
            if self.index == 0:
                reset_started.set()
                await release_close.wait()

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", RecoveringAsyncHTTPClient)
    handle = OpenVikingAsyncClientHandle(OpenVikingConnection(url="http://localhost:1933"))
    await handle.initialize()

    recovering_call = asyncio.create_task(handle.find(query="first"))
    await asyncio.wait_for(reset_started.wait(), timeout=1)

    sibling_method = handle.find
    sibling_call = asyncio.create_task(sibling_method(query="second"))
    release_close.set()

    first_result, second_result = await asyncio.gather(recovering_call, sibling_call)

    assert {first_result["query"], second_result["query"]} == {"first", "second"}
    assert first_result["client"] == second_result["client"] == 1
    assert len(instances) == 2
    await handle.close()


@pytest.mark.asyncio
async def test_async_handle_stale_failure_does_not_reset_replacement(monkeypatch):
    instances: list[Any] = []
    stale_call_started = asyncio.Event()
    release_stale_failure = asyncio.Event()
    reset_started = asyncio.Event()
    release_old_close = asyncio.Event()

    class RecoveringAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.index = len(instances)
            self.closed = False
            instances.append(self)

        async def initialize(self) -> None:
            return None

        async def find(self, *, query: str) -> dict[str, Any]:
            if self.index != 0:
                return {"query": query, "client": self.index}
            if query == "stale":
                stale_call_started.set()
                await release_stale_failure.wait()
            raise ConnectionError(f"{query} failed on the old client")

        async def close(self) -> None:
            self.closed = True
            if self.index == 0:
                reset_started.set()
                await release_old_close.wait()

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", RecoveringAsyncHTTPClient)
    handle = OpenVikingAsyncClientHandle(OpenVikingConnection(url="http://localhost:1933"))
    await handle.initialize()

    stale_call = asyncio.create_task(handle.find(query="stale"))
    await asyncio.wait_for(stale_call_started.wait(), timeout=1)

    recovering_call = asyncio.create_task(handle.find(query="first"))
    await asyncio.wait_for(reset_started.wait(), timeout=1)

    fresh_result = await handle.find(query="fresh")
    release_stale_failure.set()
    stale_result = await stale_call
    release_old_close.set()
    recovering_result = await recovering_call

    assert fresh_result["client"] == stale_result["client"] == recovering_result["client"] == 1
    assert len(instances) == 2
    assert instances[0].closed is True
    assert instances[1].closed is False
    await handle.close()


@pytest.mark.asyncio
async def test_async_handle_missing_method_fails_when_called(monkeypatch):
    class FakeAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            pass

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            return None

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FakeAsyncHTTPClient)
    handle = OpenVikingAsyncClientHandle(OpenVikingConnection(url="http://localhost:1933"))

    missing_method = handle.definitely_not_an_openviking_client_attribute
    with pytest.raises(AttributeError, match="definitely_not_an_openviking_client_attribute"):
        await missing_method()

    await handle.close()


@pytest.mark.asyncio
async def test_sync_close_after_async_use_remains_recoverable(monkeypatch):
    class FakeAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

    class FakeSyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            self._initialized = False

        def initialize(self) -> None:
            self._initialized = True

        def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FakeAsyncHTTPClient)
    monkeypatch.setattr(client_module, "SyncHTTPClient", FakeSyncHTTPClient)
    recorder = OpenVikingSessionRecorder(url="http://localhost:1933")
    sync_handle = recorder.client
    async_handle = await recorder.get_async_client()
    sync_client = sync_handle.get()
    async_client = await async_handle.get()

    with pytest.raises(RuntimeError, match=r"await recorder\.aclose"):
        recorder.close()

    assert recorder._closed is False
    assert sync_client.closed is False
    assert async_client.closed is False

    await recorder.aclose()

    assert recorder._closed is True
    assert sync_client.closed is True
    assert async_client.closed is True


@pytest.mark.asyncio
async def test_async_middleware_closes_all_internally_owned_clients(monkeypatch):
    instances: list[Any] = []

    class FakeAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FakeAsyncHTTPClient)
    middleware = OpenVikingContextMiddleware(url="http://localhost:1933")

    await middleware.recorder.get_async_client()
    await middleware.assembler.get_async_client()
    await middleware.retriever.get_async_client()
    assert len(instances) == 3

    await middleware.aclose()

    assert all(client.closed for client in instances)


@pytest.mark.asyncio
async def test_async_middleware_does_not_close_injected_client():
    client = AsyncInMemoryOpenVikingClient()
    middleware = OpenVikingContextMiddleware(async_client=client)

    await middleware.recorder.get_async_client()
    await middleware.assembler.get_async_client()
    await middleware.retriever.get_async_client()
    await middleware.aclose()

    assert client.closed is False


@pytest.mark.asyncio
async def test_async_middleware_does_not_close_injected_retriever():
    client = AsyncInMemoryOpenVikingClient()
    retriever = OpenVikingRetriever(async_client=client)
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        retriever=retriever,
    )

    await middleware.aclose()

    assert retriever._closed is False
    assert await retriever.ainvoke("still usable") == []
    assert client.closed is False
    await retriever.aclose()


@pytest.mark.asyncio
async def test_async_client_retries_safe_read_with_fresh_client(monkeypatch):
    instances: list[Any] = []

    class FlakyAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.index = len(instances)
            self.closed = False
            instances.append(self)

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

        async def find(self, **_kwargs: Any) -> dict[str, Any]:
            if self.index == 0:
                raise ConnectionError("OpenViking server was not ready")
            return {"memories": [], "resources": [], "skills": []}

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FlakyAsyncHTTPClient)

    client = await ensure_async_client(OpenVikingConnection(url="http://localhost:1933"))
    result = await acall_openviking(client, "find", query="recover")

    assert result == {"memories": [], "resources": [], "skills": []}
    assert len(instances) == 2
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_async_client_evicts_without_replaying_mutating_call(monkeypatch):
    instances: list[Any] = []

    class FlakyAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

        async def batch_add_messages(self, **_kwargs: Any) -> dict[str, Any]:
            raise ConnectionError("OpenViking connection dropped during write")

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FlakyAsyncHTTPClient)

    client = await ensure_async_client(OpenVikingConnection(url="http://localhost:1933"))
    with pytest.raises(ConnectionError):
        await acall_openviking(
            client,
            "batch_add_messages",
            session_id="async-mutation",
            messages=[{"role": "user", "content": "hello"}],
        )

    assert len(instances) == 1
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_sync_client_async_fallback_runs_outside_event_loop_thread():
    main_thread_id = threading.get_ident()
    call_thread_ids: list[int] = []

    class SyncClient:
        def find(self, query: str) -> dict[str, Any]:
            call_thread_ids.append(threading.get_ident())
            return {"memories": [], "resources": [], "skills": [], "query": query}

    result = await acall_openviking(SyncClient(), "find", query="fallback")

    assert result["query"] == "fallback"
    assert call_thread_ids
    assert call_thread_ids[0] != main_thread_id


@pytest.mark.asyncio
async def test_async_retriever_uses_native_client_for_search_and_read():
    backing = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/async.md": "Native async retrieval."}
    )
    client = AsyncInMemoryOpenVikingClient(backing)
    main_thread_id = threading.get_ident()
    retriever = OpenVikingRetriever(
        async_client=client,
        target_uri="viking://resources",
    )

    documents = await retriever.ainvoke("async retrieval")

    assert [document.page_content for document in documents] == ["Native async retrieval."]
    assert client.calls == ["find", "read"]
    assert set(client.call_thread_ids) == {main_thread_id}


@pytest.mark.asyncio
async def test_async_recorder_preserves_batch_and_commit_semantics():
    class BatchTrackingClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.batch_sizes: list[int] = []

        def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.batch_sizes.append(len(messages))
            return super().batch_add_messages(session_id, messages, **kwargs)

    backing = BatchTrackingClient()
    client = AsyncInMemoryOpenVikingClient(backing)
    recorder = OpenVikingSessionRecorder(
        async_client=client,
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    )

    result = await recorder.arecord(
        "async-recorder",
        [HumanMessage(content=f"Message {index}") for index in range(205)],
    )

    assert result.messages_written == 205
    assert result.input_messages_consumed == 205
    assert backing.batch_sizes == [100, 100, 5]
    assert backing.sessions["async-recorder"] == []
    assert len(backing.archives["async-recorder"][0]["messages"]) == 205


@pytest.mark.asyncio
async def test_async_recorder_retries_only_pending_commit_without_duplicate_writes():
    class FailFirstCommitClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.batch_calls = 0
            self.commit_calls = 0

        def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.batch_calls += 1
            return super().batch_add_messages(session_id, messages, **kwargs)

        def commit_session(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise RuntimeError("commit failed")
            return super().commit_session(session_id, **kwargs)

    backing = FailFirstCommitClient()
    recorder = OpenVikingSessionRecorder(
        async_client=AsyncInMemoryOpenVikingClient(backing),
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    )

    with pytest.raises(OpenVikingPartialWriteError) as captured:
        await recorder.arecord(
            "async-commit-retry",
            [HumanMessage(content="Persist once.")],
        )
    result = await recorder.arecord("async-commit-retry", ())

    assert captured.value.commit_pending is True
    assert result.messages_written == 0
    assert backing.batch_calls == 1
    assert backing.commit_calls == 2
    assert len(backing.archives["async-commit-retry"][0]["messages"]) == 1


@pytest.mark.asyncio
async def test_async_recorder_reports_confirmed_prefix_for_partial_batch_retry():
    class FailSecondBatchClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.batch_sizes: list[int] = []

        def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.batch_sizes.append(len(messages))
            if len(self.batch_sizes) == 2:
                raise RuntimeError("second batch failed")
            return super().batch_add_messages(session_id, messages, **kwargs)

    backing = FailSecondBatchClient()
    recorder = OpenVikingSessionRecorder(async_client=AsyncInMemoryOpenVikingClient(backing))
    messages = [HumanMessage(content=f"Message {index}") for index in range(101)]

    with pytest.raises(OpenVikingPartialWriteError) as captured:
        await recorder.arecord("async-partial-retry", messages)
    await recorder.arecord(
        "async-partial-retry",
        messages[captured.value.input_messages_consumed :],
    )

    assert captured.value.messages_written == 100
    assert captured.value.input_messages_consumed == 100
    assert backing.batch_sizes == [100, 1, 1]
    assert len(backing.sessions["async-partial-retry"]) == 101


@pytest.mark.asyncio
async def test_async_middleware_preserves_partial_progress_when_recording_is_cancelled():
    class CancelSecondBatchClient(AsyncInMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.batch_calls = 0
            self.cancellation = asyncio.CancelledError("cancel second batch")

        async def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.batch_calls += 1
            if self.batch_calls == 2:
                raise self.cancellation
            return self.backing.batch_add_messages(session_id, messages, **kwargs)

    client = CancelSecondBatchClient()
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        session_id_resolver=lambda _state, _runtime: "async-cancelled-batch",
    )
    messages = [HumanMessage(content=f"Message {index}") for index in range(150)]
    state = {"messages": messages}

    recording = asyncio.create_task(middleware.aafter_agent(state, runtime=None))
    with pytest.raises(asyncio.CancelledError) as captured:
        await recording
    await middleware.aafter_agent(state, runtime=None)

    progress = get_openviking_cancellation_progress(captured.value)
    assert recording.cancelled() is True
    if sys.version_info >= (3, 11):
        # Python 3.10 rebuilds CancelledError at task boundaries via
        # Future._make_cancelled_error(), preserving the original in __context__.
        assert captured.value is client.cancellation
    assert not isinstance(captured.value, Exception)
    assert isinstance(progress, OpenVikingCancellationProgress)
    assert progress.messages_written == 100
    assert progress.input_messages_consumed == 100
    assert client.batch_calls == 3
    assert len(client.backing.sessions["async-cancelled-batch"]) == 150


@pytest.mark.asyncio
async def test_async_middleware_retries_cancelled_commit_without_duplicate_writes():
    class CancelFirstCommitClient(AsyncInMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.batch_calls = 0
            self.commit_calls = 0
            self.cancellation = asyncio.CancelledError("cancel first commit")

        async def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.batch_calls += 1
            return self.backing.batch_add_messages(session_id, messages, **kwargs)

        async def commit_session(
            self,
            session_id: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise self.cancellation
            return self.backing.commit_session(session_id, **kwargs)

    client = CancelFirstCommitClient()
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        session_id_resolver=lambda _state, _runtime: "async-cancelled-commit",
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    )
    state = {
        "messages": [
            HumanMessage(content="Persist once."),
            AIMessage(content="Commit once."),
        ]
    }

    with pytest.raises(asyncio.CancelledError) as captured:
        await middleware.aafter_agent(state, runtime=None)
    await middleware.aafter_agent(state, runtime=None)

    progress = get_openviking_cancellation_progress(captured.value)
    assert captured.value is client.cancellation
    assert isinstance(progress, OpenVikingCancellationProgress)
    assert progress.commit_pending is True
    assert progress.input_messages_consumed == 2
    assert client.batch_calls == 1
    assert client.commit_calls == 2
    assert len(client.backing.archives["async-cancelled-commit"][0]["messages"]) == 2


@pytest.mark.parametrize(
    "timeout_api",
    [
        "wait_for",
        pytest.param(
            "timeout",
            marks=pytest.mark.skipif(
                not hasattr(asyncio, "timeout"),
                reason="asyncio.timeout requires Python 3.11+",
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_async_recording_cancellation_preserves_timeout_contract_and_retry(
    timeout_api: str,
):
    class BlockSecondBatchClient(AsyncInMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.batch_calls = 0

        async def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.batch_calls += 1
            if self.batch_calls == 2:
                await asyncio.Event().wait()
            return self.backing.batch_add_messages(session_id, messages, **kwargs)

    client = BlockSecondBatchClient()
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        session_id_resolver=lambda _state, _runtime: f"async-{timeout_api}",
    )
    state = {"messages": [HumanMessage(content=f"Message {index}") for index in range(150)]}

    with pytest.raises(asyncio.TimeoutError) as captured:
        if timeout_api == "wait_for":
            await asyncio.wait_for(middleware.aafter_agent(state, runtime=None), timeout=0.05)
        else:
            async with asyncio.timeout(0.05):
                await middleware.aafter_agent(state, runtime=None)

    progress = get_openviking_cancellation_progress(captured.value)
    assert isinstance(progress, OpenVikingCancellationProgress)
    assert progress.input_messages_consumed == 100

    await middleware.aafter_agent(state, runtime=None)

    assert client.batch_calls == 3
    assert len(client.backing.sessions[f"async-{timeout_api}"]) == 150


@pytest.mark.asyncio
async def test_async_recorder_closes_only_internally_created_client(monkeypatch):
    instances: list[Any] = []

    class FakeAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        async def initialize(self) -> None:
            return None

        async def batch_add_messages(self, **_kwargs: Any) -> dict[str, Any]:
            return {"added": 1}

        async def close(self) -> None:
            self.closed = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", FakeAsyncHTTPClient)
    owned_recorder = OpenVikingSessionRecorder(url="http://localhost:1933")

    await owned_recorder.arecord(
        "async-owned-client",
        [HumanMessage(content="Close owned client.")],
    )
    await owned_recorder.aclose()
    await owned_recorder.aclose()

    assert instances[0].closed is True
    with pytest.raises(RuntimeError, match="closed"):
        await owned_recorder.arecord(
            "async-owned-client",
            [HumanMessage(content="Rejected.")],
        )

    injected_client = AsyncInMemoryOpenVikingClient()
    injected_recorder = OpenVikingSessionRecorder(async_client=injected_client)
    await injected_recorder.arecord(
        "async-injected-client",
        [HumanMessage(content="Keep injected client open.")],
    )
    await injected_recorder.aclose()

    assert injected_client.closed is False


@pytest.mark.asyncio
async def test_async_chat_history_reads_writes_and_clears_natively():
    backing = InMemoryOpenVikingClient()
    client = AsyncInMemoryOpenVikingClient(backing)
    history = OpenVikingChatMessageHistory(
        session_id="async-history",
        async_client=client,
    )

    await history.aadd_messages(
        [
            HumanMessage(content="Remember async history."),
            AIMessage(content="Stored asynchronously."),
        ]
    )
    messages = await history.aget_messages()
    await history.aclear()

    assert [message.content for message in messages] == [
        "Remember async history.",
        "Stored asynchronously.",
    ]
    assert backing.sessions["async-history"] == []
    assert client.calls == [
        "batch_add_messages",
        "get_session_context",
        "delete_session",
        "create_session",
    ]


@pytest.mark.asyncio
async def test_async_context_assembler_combines_session_and_recall():
    backing = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/async.md": "Async context is azure."}
    )
    backing.add_message("async-assembler", "user", content="Active async turn.")
    client = AsyncInMemoryOpenVikingClient(backing)
    assembler = OpenVikingSessionContextAssembler(
        async_client=client,
        target_uri="viking://resources",
    )

    assembled = await assembler.aassemble(
        session_id="async-assembler",
        query="azure",
    )

    assert "Active async turn." in assembled.block
    assert "Async context is azure." in assembled.block
    assert assembled.context_parts[0]["uri"] == "viking://resources/runbooks/async.md"


@pytest.mark.asyncio
async def test_async_assemble_skips_create_for_existing_session():
    """The async path must get the same per-turn call reduction as the sync one."""
    backing = InMemoryOpenVikingClient()
    backing.add_message("async-existing", "user", content="Existing async turn.")
    client = AsyncInMemoryOpenVikingClient(backing)
    assembler = OpenVikingSessionContextAssembler(
        async_client=client,
        target_uri="viking://resources",
        include_recall=False,
    )

    await assembler.aassemble(session_id="async-existing")
    await assembler.aassemble(session_id="async-existing")

    assert client.calls == ["get_session_context", "get_session_context"]


@pytest.mark.asyncio
async def test_async_assemble_creates_session_only_on_not_found():
    """A missing session is still materialized, without a second context read."""
    backing = InMemoryOpenVikingClient()
    client = AsyncInMemoryOpenVikingClient(backing)
    assembler = OpenVikingSessionContextAssembler(
        async_client=client,
        target_uri="viking://resources",
        include_recall=False,
    )

    await assembler.aassemble(session_id="async-missing")

    assert client.calls == ["get_session_context", "create_session"]
    assert "async-missing" in backing.sessions


@pytest.mark.asyncio
async def test_async_history_does_not_create_session_on_non_not_found_error():
    """Only NOT_FOUND may trigger a create, matching the sync error semantics."""
    backing = InMemoryOpenVikingClient()
    backing.add_message("async-flaky", "user", content="Existing turn.")
    client = AsyncInMemoryOpenVikingClient(backing)

    async def failing_get_session_context(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        client.calls.append("get_session_context")
        raise RuntimeError("upstream 503")

    client.get_session_context = failing_get_session_context
    history = OpenVikingChatMessageHistory(
        session_id="async-flaky",
        async_client=client,
    )

    assert await history.aget_messages() == []
    assert "create_session" not in client.calls


@pytest.mark.asyncio
async def test_async_middleware_injects_and_captures_context():
    backing = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "Async middleware prefers teal."}
    )
    client = AsyncInMemoryOpenVikingClient(backing)
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        target_uri="viking://user/memories",
        session_id_resolver=lambda _state, _runtime: "async-middleware",
    )
    captured_request: dict[str, Any] = {}

    class Request:
        state: dict[str, Any] = {}
        runtime = None
        messages = [HumanMessage(content="What teal preference?")]
        system_message = None

        def override(self, **overrides: Any) -> Request:
            request = Request()
            request.system_message = overrides.get("system_message", self.system_message)
            return request

    async def handler(request: Any) -> AIMessage:
        captured_request["request"] = request
        return AIMessage(content="teal")

    await middleware.awrap_model_call(Request(), handler)
    await middleware.aafter_agent(
        {
            "messages": [
                HumanMessage(content="What color?"),
                AIMessage(content="teal"),
            ]
        },
        runtime=None,
    )

    assert "Async middleware prefers teal." in (captured_request["request"].system_message.content)
    assistant_parts = backing.sessions["async-middleware"][1]["parts"]
    assert any(part["type"] == "context" for part in assistant_parts)


@pytest.mark.asyncio
async def test_async_middleware_clears_pending_context_when_model_call_is_cancelled():
    backing = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "Cancelled context must not be reused."}
    )
    client = AsyncInMemoryOpenVikingClient(backing)
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        target_uri="viking://user/memories",
        session_id_resolver=lambda _state, _runtime: "async-cancelled-middleware",
    )
    handler_started = asyncio.Event()

    class Request:
        state: dict[str, Any] = {}
        runtime = None
        messages = [HumanMessage(content="Find the cancelled context.")]
        system_message = None

        def override(self, **overrides: Any) -> Request:
            request = Request()
            request.system_message = overrides.get("system_message", self.system_message)
            return request

    async def handler(_request: Any) -> AIMessage:
        handler_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    model_call = asyncio.create_task(middleware.awrap_model_call(Request(), handler))
    await asyncio.wait_for(handler_started.wait(), timeout=1)
    model_call.cancel()

    with pytest.raises(asyncio.CancelledError):
        await model_call

    assert middleware._pending_context_parts == {}
