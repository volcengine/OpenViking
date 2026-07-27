from __future__ import annotations

import threading
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage

from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingChatMessageHistory,
    OpenVikingCommitPolicy,
    OpenVikingContextMiddleware,
    OpenVikingPartialWriteError,
    OpenVikingRetriever,
    OpenVikingSessionContextAssembler,
    OpenVikingSessionRecorder,
)
from openviking.integrations.langchain.client import (
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

    import openviking.client as client_module

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
            self.initialize_calls += 1

    client = AsyncClientWithoutInitializedFlag()
    connection = OpenVikingConnection(async_client=client)

    assert await ensure_async_client(connection) is client
    assert await ensure_async_client(connection) is client
    assert client.initialize_calls == 1


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

    import openviking.client as client_module

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

    import openviking.client as client_module

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

    import openviking.client as client_module

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
