from __future__ import annotations

import asyncio
import copy
import threading
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory

from openviking.integrations.langchain import (
    OpenVikingCommitPolicy,
    OpenVikingContextRunnable,
    with_openviking_context,
)


def _empty_session_context() -> dict[str, Any]:
    return {
        "messages": [],
        "latest_archive_overview": "",
        "pre_archive_abstracts": [],
    }


def _sync_http_client_type(instances: list[Any]) -> type[Any]:
    class TrackingSyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.closed = False
            instances.append(self)

        def initialize(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        def create_session(self, **_kwargs: Any) -> dict[str, Any]:
            return {}

        def get_session_context(self, **_kwargs: Any) -> dict[str, Any]:
            return _empty_session_context()

        def search(self, **_kwargs: Any) -> dict[str, Any]:
            return {"memories": [], "resources": [], "skills": []}

        def batch_add_messages(self, **_kwargs: Any) -> dict[str, Any]:
            return {"added": 2}

    return TrackingSyncHTTPClient


def _async_http_client_type(instances: list[Any]) -> type[Any]:
    class TrackingAsyncHTTPClient:
        def __init__(self, **_kwargs: Any):
            self.loop: asyncio.AbstractEventLoop | None = None
            self.close_calls = 0
            self.close_started = asyncio.Event()
            self.block_close = False
            instances.append(self)

        async def initialize(self) -> None:
            self.loop = asyncio.get_running_loop()

        def _check_loop(self) -> None:
            if asyncio.get_running_loop() is not self.loop:
                raise RuntimeError("client used from a different event loop")

        async def close(self) -> None:
            self.close_calls += 1
            self.close_started.set()
            if self.block_close:
                await asyncio.Event().wait()

        async def create_session(self, **_kwargs: Any) -> dict[str, Any]:
            self._check_loop()
            return {}

        async def get_session_context(self, **_kwargs: Any) -> dict[str, Any]:
            self._check_loop()
            return _empty_session_context()

        async def search(self, **_kwargs: Any) -> dict[str, Any]:
            self._check_loop()
            return {"memories": [], "resources": [], "skills": []}

        async def batch_add_messages(self, **_kwargs: Any) -> dict[str, Any]:
            self._check_loop()
            return {"added": 2}

    return TrackingAsyncHTTPClient


def _sync_answer(messages: list[BaseMessage]) -> AIMessage:
    latest = next(
        message.content for message in reversed(messages) if isinstance(message, HumanMessage)
    )
    return AIMessage(content=f"answer:{latest}")


async def _async_answer(messages: list[BaseMessage]) -> AIMessage:
    await asyncio.sleep(0)
    return _sync_answer(messages)


def test_context_wrapper_reuses_and_closes_owned_sync_clients(monkeypatch):
    instances: list[Any] = []
    import openviking.client as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", _sync_http_client_type(instances))

    with with_openviking_context(
        RunnableLambda(_sync_answer),
        url="http://localhost:1933",
        session_id="sync-lifecycle",
    ) as app:
        assert isinstance(app, OpenVikingContextRunnable)
        assert isinstance(app, RunnableWithMessageHistory)
        assert app.invoke([HumanMessage(content="one")]).content == "answer:one"
        assert app.invoke([HumanMessage(content="two")]).content == "answer:two"
        assert len(instances) == 3
        assert not any(client.closed for client in instances)

    assert all(client.closed for client in instances)
    app.close()
    with pytest.raises(RuntimeError, match="OpenVikingContextRunnable is closed"):
        app.invoke([HumanMessage(content="after-close")])


def test_context_wrapper_reuses_sync_clients_across_batch_threads(monkeypatch):
    instances: list[Any] = []
    instances_lock = threading.Lock()
    client_type = _sync_http_client_type(instances)
    original_init = client_type.__init__

    def synchronized_init(self: Any, **kwargs: Any) -> None:
        with instances_lock:
            original_init(self, **kwargs)

    monkeypatch.setattr(client_type, "__init__", synchronized_init)
    import openviking.client as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", client_type)
    app = with_openviking_context(
        RunnableLambda(_sync_answer),
        url="http://localhost:1933",
        inject_context=False,
    )
    configs = [{"configurable": {"session_id": f"sync-thread-{index}"}} for index in range(8)]

    results = app.batch(
        [[HumanMessage(content=f"message-{index}")] for index in range(8)],
        config=configs,
    )

    assert [result.content for result in results] == [
        f"answer:message-{index}" for index in range(8)
    ]
    assert len(instances) == 1
    app.close()
    assert instances[0].closed is True
    with pytest.raises(RuntimeError, match="OpenVikingContextRunnable is closed"):
        app.invoke([HumanMessage(content="after-close")], config=configs[0])


def test_context_wrapper_copies_share_one_lifecycle_owner():
    app = with_openviking_context(
        RunnableLambda(_sync_answer),
        async_client=object(),
        session_id="copied-lifecycle",
    )

    copied = copy.deepcopy(app)
    model_copied = app.model_copy(deep=True)

    assert copied is not app
    assert model_copied is not app
    assert copied._resources is app._resources
    assert model_copied._resources is app._resources

    copied.close()
    with pytest.raises(RuntimeError, match="OpenVikingContextRunnable is closed"):
        app.invoke([HumanMessage(content="after-copy-close")])


def test_composed_runnable_uses_retained_context_wrapper_lifecycle():
    app = with_openviking_context(
        RunnableLambda(_sync_answer),
        async_client=object(),
        session_id="composed-lifecycle",
        inject_context=False,
    )
    composed = app | RunnableLambda(lambda message: message)

    assert not hasattr(composed, "close")
    app.close()

    with pytest.raises(RuntimeError, match="OpenVikingContextRunnable is closed"):
        composed.invoke([HumanMessage(content="after-close")])


def test_context_wrapper_does_not_close_injected_sync_client():
    class InjectedSyncClient:
        def __init__(self):
            self._initialized = False
            self.initialize_calls = 0
            self.close_calls = 0

        def initialize(self) -> None:
            self._initialized = True
            self.initialize_calls += 1

        def close(self) -> None:
            self.close_calls += 1

        def create_session(self, **_kwargs: Any) -> dict[str, Any]:
            return {}

        def get_session_context(self, **_kwargs: Any) -> dict[str, Any]:
            return _empty_session_context()

        def search(self, **_kwargs: Any) -> dict[str, Any]:
            return {"memories": [], "resources": [], "skills": []}

        def batch_add_messages(self, **_kwargs: Any) -> dict[str, Any]:
            return {"added": 2}

    client = InjectedSyncClient()
    with with_openviking_context(
        RunnableLambda(_sync_answer),
        client=client,
        session_id="injected-sync-lifecycle",
    ) as app:
        assert app.invoke([HumanMessage(content="injected")]).content == "answer:injected"

    assert client.initialize_calls == 1
    assert client.close_calls == 0
    client.close()


@pytest.mark.asyncio
async def test_context_wrapper_reuses_and_closes_owned_async_clients(monkeypatch):
    instances: list[Any] = []
    import openviking.client as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", _async_http_client_type(instances))

    async with with_openviking_context(
        RunnableLambda(_async_answer),
        url="http://localhost:1933",
        session_id="async-lifecycle",
    ) as app:
        assert (await app.ainvoke([HumanMessage(content="one")])).content == "answer:one"
        assert (await app.ainvoke([HumanMessage(content="two")])).content == "answer:two"
        assert len(instances) == 3
        assert not any(client.close_calls for client in instances)

    assert [client.close_calls for client in instances] == [1, 1, 1]
    await app.aclose()
    with pytest.raises(RuntimeError, match="OpenVikingContextRunnable is closed"):
        await app.ainvoke([HumanMessage(content="after-close")])


def test_context_wrapper_keeps_owned_async_clients_loop_scoped(monkeypatch):
    instances: list[Any] = []
    import openviking.client as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", _async_http_client_type(instances))
    app = with_openviking_context(
        RunnableLambda(_async_answer),
        url="http://localhost:1933",
        session_id="cross-loop-lifecycle",
    )

    async def invoke(label: str) -> str:
        result = await app.ainvoke([HumanMessage(content=label)])
        return str(result.content)

    assert asyncio.run(invoke("loop-a")) == "answer:loop-a"
    assert asyncio.run(invoke("loop-b")) == "answer:loop-b"
    assert len(instances) == 6
    assert len({client.loop for client in instances}) == 2

    asyncio.run(app.aclose())

    assert all(client.close_calls == 1 for client in instances)


@pytest.mark.asyncio
async def test_context_wrapper_does_not_close_injected_async_client():
    class InjectedAsyncClient:
        def __init__(self):
            self.initialize_calls = 0
            self.close_calls = 0

        async def initialize(self) -> None:
            self.initialize_calls += 1

        async def close(self) -> None:
            self.close_calls += 1

        async def create_session(self, **_kwargs: Any) -> dict[str, Any]:
            return {}

        async def get_session_context(self, **_kwargs: Any) -> dict[str, Any]:
            return _empty_session_context()

        async def search(self, **_kwargs: Any) -> dict[str, Any]:
            return {"memories": [], "resources": [], "skills": []}

        async def batch_add_messages(self, **_kwargs: Any) -> dict[str, Any]:
            return {"added": 2}

    client = InjectedAsyncClient()
    app = with_openviking_context(
        RunnableLambda(_async_answer),
        async_client=client,
        session_id="injected-lifecycle",
    )

    await app.ainvoke([HumanMessage(content="injected")])
    await app.aclose()

    assert client.initialize_calls == 1
    assert client.close_calls == 0
    await client.close()


@pytest.mark.asyncio
async def test_context_wrapper_close_inside_event_loop_remains_recoverable():
    app = with_openviking_context(
        RunnableLambda(_async_answer),
        async_client=object(),
        session_id="active-loop-close",
        inject_context=False,
    )

    with pytest.raises(RuntimeError, match=r"await runnable\.aclose"):
        app.close()

    app._resources.raise_if_closed()
    await app.aclose()
    with pytest.raises(RuntimeError, match="closed"):
        app._resources.raise_if_closed()


@pytest.mark.asyncio
async def test_context_wrapper_finishes_cleanup_when_aclose_is_cancelled(monkeypatch):
    instances: list[Any] = []
    import openviking.client as client_module

    monkeypatch.setattr(client_module, "AsyncHTTPClient", _async_http_client_type(instances))
    app = with_openviking_context(
        RunnableLambda(_async_answer),
        url="http://localhost:1933",
        session_id="cancelled-close",
    )
    await app.ainvoke([HumanMessage(content="initialize")])
    assert len(instances) == 3
    instances[0].block_close = True

    closing = asyncio.create_task(app.aclose())
    await asyncio.wait_for(instances[0].close_started.wait(), timeout=1)
    closing.cancel()

    with pytest.raises(asyncio.CancelledError):
        await closing

    assert [client.close_calls for client in instances] == [1, 1, 1]
    await app.aclose()


@pytest.mark.asyncio
async def test_context_wrapper_retries_pending_commit_across_invocations():
    class FailFirstCommitClient:
        def __init__(self):
            self.events: list[str] = []
            self.commit_calls = 0

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def get_session_context(self, **_kwargs: Any) -> dict[str, Any]:
            self.events.append("get_session_context")
            return _empty_session_context()

        async def get_session(self, **_kwargs: Any) -> dict[str, Any]:
            self.events.append("get_session")
            return {"pending_tokens": 1}

        async def batch_add_messages(self, **_kwargs: Any) -> dict[str, Any]:
            self.events.append("batch_add_messages")
            return {"added": 2}

        async def commit_session(self, **_kwargs: Any) -> dict[str, Any]:
            self.events.append("commit_session")
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise RuntimeError("first commit failed")
            return {}

    client = FailFirstCommitClient()
    app = with_openviking_context(
        RunnableLambda(_async_answer),
        async_client=client,
        session_id="pending-commit-lifecycle",
        inject_context=False,
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    )

    first = await app.ainvoke([HumanMessage(content="first")])
    result = await app.ainvoke([HumanMessage(content="second")])

    assert first.content == "answer:first"
    assert result.content == "answer:second"
    assert client.events == [
        "get_session_context",
        "batch_add_messages",
        "commit_session",
        "get_session_context",
        "get_session",
        "commit_session",
        "batch_add_messages",
        "commit_session",
    ]
    await app.aclose()
