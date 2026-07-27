from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import openviking.integrations.langchain.recording as recording_module
from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingChatMessageHistory,
    OpenVikingCommitPolicy,
    OpenVikingContextMiddleware,
    OpenVikingRecordResult,
    OpenVikingSessionRecorder,
)
from openviking.integrations.langchain.messages import OPENVIKING_CONTEXT_MARKER


class TrackingOpenVikingClient(InMemoryOpenVikingClient):
    def __init__(self):
        super().__init__()
        self.batch_sizes: list[int] = []
        self.commit_calls: list[str] = []
        self.closed = False

    def batch_add_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.batch_sizes.append(len(messages))
        return super().batch_add_messages(session_id, messages, **kwargs)

    def commit_session(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        self.commit_calls.append(session_id)
        return super().commit_session(session_id, **kwargs)

    def close(self) -> None:
        self.closed = True
        super().close()


def test_recorder_filters_framework_control_messages_and_preserves_tool_parts():
    client = TrackingOpenVikingClient()
    recorder = OpenVikingSessionRecorder(client=client)
    context_parts = [
        {
            "type": "context",
            "uri": "viking://user/memories/preference.md",
            "context_type": "memory",
            "abstract": "The user prefers azure.",
        }
    ]

    result = recorder.record(
        "recorder-filtering",
        [
            SystemMessage(content="Runtime policy must not be persisted."),
            HumanMessage(
                content="Here is a summary of the conversation to date.",
                additional_kwargs={"lc_source": "summarization"},
            ),
            HumanMessage(
                content=f"{OPENVIKING_CONTEXT_MARKER}\nInjected context\n</openviking_context>"
            ),
            AIMessage(content=""),
            HumanMessage(content="Find the deployment notes."),
            AIMessage(
                content="Searching.",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "viking_find",
                        "args": {"query": "deployment"},
                    }
                ],
            ),
            ToolMessage(
                content="Azure deployment notes found.",
                tool_call_id="call-1",
                name="viking_find",
            ),
        ],
        peer_id=" peer-recorder ",
        context_parts=context_parts,
    )

    assert result.messages_written == 3
    assert result.context_attached is True
    assert isinstance(result, OpenVikingRecordResult)
    assert client.batch_sizes == [3]
    stored = client.sessions["recorder-filtering"]
    assert [message["role"] for message in stored] == ["user", "assistant", "assistant"]
    assert [message.get("peer_id") for message in stored] == ["peer-recorder"] * 3
    assert [part["type"] for part in stored[1]["parts"]] == ["text", "tool", "context"]
    assert stored[2]["parts"][0]["tool_output"] == "Azure deployment notes found."
    assert context_parts == [
        {
            "type": "context",
            "uri": "viking://user/memories/preference.md",
            "context_type": "memory",
            "abstract": "The user prefers azure.",
        }
    ]


def test_recorder_chunks_writes_at_server_batch_limit():
    client = TrackingOpenVikingClient()
    recorder = OpenVikingSessionRecorder(client=client)

    result = recorder.record(
        "recorder-batches",
        [HumanMessage(content=f"Message {index}") for index in range(101)],
    )

    assert result.messages_written == 101
    assert client.batch_sizes == [100, 1]
    assert len(client.sessions["recorder-batches"]) == 101


def test_recorder_ignores_filtered_only_batch_without_initializing_client():
    client = TrackingOpenVikingClient()
    recorder = OpenVikingSessionRecorder(client=client)

    result = recorder.record(
        "recorder-filtered-only",
        [
            SystemMessage(content="Runtime policy."),
            AIMessage(content=""),
        ],
    )

    assert result.messages_written == 0
    assert result.context_attached is False
    assert client.batch_sizes == []
    assert client._initialized is False


def test_recorder_applies_commit_policy_once_after_all_batches():
    client = TrackingOpenVikingClient()
    recorder = OpenVikingSessionRecorder(
        client=client,
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    )

    recorder.record(
        "recorder-commit",
        [HumanMessage(content=f"Message {index}") for index in range(101)],
    )

    assert client.batch_sizes == [100, 1]
    assert client.commit_calls == ["recorder-commit"]
    assert client.sessions["recorder-commit"] == []
    assert len(client.archives["recorder-commit"][0]["messages"]) == 101


def test_recorder_does_not_apply_commit_policy_after_failed_batch():
    class FailSecondBatchClient(TrackingOpenVikingClient):
        def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            if len(self.batch_sizes) == 1:
                self.batch_sizes.append(len(messages))
                raise RuntimeError("second batch failed")
            return super().batch_add_messages(session_id, messages, **kwargs)

    client = FailSecondBatchClient()
    recorder = OpenVikingSessionRecorder(
        client=client,
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    )

    with pytest.raises(RuntimeError, match="second batch failed"):
        recorder.record(
            "recorder-failed-batch",
            [HumanMessage(content=f"Message {index}") for index in range(101)],
        )

    assert client.batch_sizes == [100, 1]
    assert client.commit_calls == []
    assert len(client.sessions["recorder-failed-batch"]) == 100


def test_recorder_flush_commits_only_pending_content():
    client = TrackingOpenVikingClient()
    recorder = OpenVikingSessionRecorder(client=client)

    assert recorder.flush("missing-session") is None
    recorder.record("recorder-flush", [HumanMessage(content="Pending content.")])

    result = recorder.flush("recorder-flush")

    assert result is not None
    assert result["archived"] is True
    assert recorder.flush("recorder-flush") is None
    assert client.commit_calls == ["recorder-flush"]


def test_recorder_flush_treats_missing_session_as_no_pending_content():
    class MissingSessionClient(TrackingOpenVikingClient):
        def get_session(self, session_id: str, auto_create: bool = False) -> dict[str, Any]:
            raise FileNotFoundError(session_id)

    recorder = OpenVikingSessionRecorder(client=MissingSessionClient())

    assert recorder.flush("missing-session") is None


def test_recorder_flush_accepts_openviking_not_found_code():
    class NotFoundError(RuntimeError):
        code = "NOT_FOUND"

    class MissingSessionClient(TrackingOpenVikingClient):
        def get_session(self, session_id: str, auto_create: bool = False) -> dict[str, Any]:
            raise NotFoundError(session_id)

    recorder = OpenVikingSessionRecorder(client=MissingSessionClient())

    assert recorder.flush("missing-session") is None


def test_recorder_flush_propagates_unexpected_lookup_failure():
    class FailingLookupClient(TrackingOpenVikingClient):
        def get_session(self, session_id: str, auto_create: bool = False) -> dict[str, Any]:
            raise RuntimeError(f"lookup failed for {session_id}")

    recorder = OpenVikingSessionRecorder(client=FailingLookupClient())

    with pytest.raises(RuntimeError, match="lookup failed"):
        recorder.flush("broken-session")


def test_recorder_close_does_not_close_injected_client():
    client = TrackingOpenVikingClient()
    recorder = OpenVikingSessionRecorder(client=client)
    assert recorder.client is client

    recorder.close()

    assert client.closed is False
    assert recorder.client is client


def test_recorder_close_closes_owned_client(monkeypatch: pytest.MonkeyPatch):
    client = TrackingOpenVikingClient()
    monkeypatch.setattr(recording_module, "ensure_client", lambda _connection: client)
    recorder = OpenVikingSessionRecorder(url="http://openviking.test")
    assert recorder.client is client

    recorder.close()

    assert client.closed is True


@pytest.mark.parametrize("batch_size", [0, 101])
def test_recorder_rejects_unsupported_batch_sizes(batch_size: int):
    with pytest.raises(ValueError, match="batch_size"):
        OpenVikingSessionRecorder(
            client=TrackingOpenVikingClient(),
            batch_size=batch_size,
        )


def test_chat_message_history_delegates_writes_to_recorder_batching():
    client = TrackingOpenVikingClient()
    history = OpenVikingChatMessageHistory(
        session_id="history-recorder",
        client=client,
    )

    history.add_messages(
        [
            HumanMessage(content="Remember azure."),
            AIMessage(content="Stored."),
            HumanMessage(content="What did I ask you to remember?"),
        ]
    )

    assert client.batch_sizes == [3]
    assert [message["role"] for message in client.sessions["history-recorder"]] == [
        "user",
        "assistant",
        "user",
    ]


def test_chat_message_history_uses_updated_commit_policy():
    client = TrackingOpenVikingClient()
    history = OpenVikingChatMessageHistory(
        session_id="history-dynamic-policy",
        client=client,
    )
    history.add_messages([HumanMessage(content="Pending before policy update.")])
    history.commit_policy = OpenVikingCommitPolicy(mode="always")

    history.add_messages([AIMessage(content="Commit after policy update.")])

    assert history.commit_policy == OpenVikingCommitPolicy(mode="always")
    assert client.sessions["history-dynamic-policy"] == []
    assert len(client.archives["history-dynamic-policy"][0]["messages"]) == 2


def test_middleware_resumes_after_last_successful_recorder_batch():
    class FailSecondBatchClient(TrackingOpenVikingClient):
        def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            if len(self.batch_sizes) == 1:
                self.batch_sizes.append(len(messages))
                raise RuntimeError("second batch failed")
            return super().batch_add_messages(session_id, messages, **kwargs)

    client = FailSecondBatchClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda _state, _runtime: "middleware-recorder-retry",
    )
    capture_key = ("middleware-recorder-retry", "")
    middleware._pending_context_parts[capture_key] = [
        {
            "type": "context",
            "uri": "viking://user/memories/retry.md",
            "context_type": "memory",
            "abstract": "Retry context.",
        }
    ]
    messages = [
        HumanMessage(content="First message."),
        AIMessage(content="Context holder."),
        *[HumanMessage(content=f"Message {index}") for index in range(98)],
        AIMessage(content="Retry only this message."),
    ]
    state = {"messages": messages}

    with pytest.raises(RuntimeError, match="second batch failed"):
        middleware.after_agent(state, runtime=None)
    middleware.after_agent(state, runtime=None)
    middleware.after_agent(state, runtime=None)

    assert client.batch_sizes == [100, 1, 1]
    stored = client.sessions["middleware-recorder-retry"]
    assert len(stored) == 101
    assert sum(part["type"] == "context" for message in stored for part in message["parts"]) == 1


def test_middleware_retains_pending_context_when_first_recorder_batch_fails():
    class FailFirstBatchClient(TrackingOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("first batch failed")
            return super().batch_add_messages(session_id, messages, **kwargs)

    client = FailFirstBatchClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda _state, _runtime: "middleware-first-batch-retry",
    )
    capture_key = ("middleware-first-batch-retry", "")
    middleware._pending_context_parts[capture_key] = [
        {
            "type": "context",
            "uri": "viking://user/memories/retry.md",
            "context_type": "memory",
            "abstract": "Retained retry context.",
        }
    ]
    state = {
        "messages": [
            HumanMessage(content="Remember the retained context."),
            AIMessage(content="Context holder."),
        ]
    }

    with pytest.raises(RuntimeError, match="first batch failed"):
        middleware.after_agent(state, runtime=None)
    middleware.after_agent(state, runtime=None)

    assert client.attempts == 2
    stored = client.sessions["middleware-first-batch-retry"]
    assert len(stored) == 2
    assert sum(part["type"] == "context" for message in stored for part in message["parts"]) == 1
