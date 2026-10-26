"""Offline contracts through the real MAF agent and session lifecycle."""

import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from agent_framework import (
    Agent,
    AgentLoopMiddleware,
    AgentSession,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
)
from openviking_sdk import AsyncHTTPClient

from openviking_agent_framework import OpenVikingContextProvider


class MemoryClient(AsyncHTTPClient):
    """Model the SDK boundary, including ambiguous append acknowledgements."""

    def __init__(self):
        super().__init__(url="http://openviking.test", api_key="test-user")
        self.rows = []
        self.sessions = []
        self.searches = []
        self.commits = []
        self.batches = 0
        self.fail_batch = None
        self.lose_ack = False
        self.recall_error = None
        self.metadata_error = None
        self.commit_error = None
        self.ack_override = None
        self.pending_tokens = {}
        self.commit_attempts = []
        self.commit_sizes = []
        self.rows_by_session = {}

    async def get_session(self, session_id, *, auto_create=False):
        if not auto_create and self.metadata_error:
            raise self.metadata_error
        self.sessions.append(session_id)
        return {"session_id": session_id, "pending_tokens": self.pending_tokens.get(session_id, 0)}

    async def search_context(self, **kwargs):
        if self.recall_error:
            raise self.recall_error
        self.searches.append(kwargs)
        return {"rendered": "Preferred format: JSON", "entries": [{"uri": "viking://memory"}]}

    async def batch_add_messages(self, session_id, messages):
        self.batches += 1
        if self.batches == self.fail_batch:
            raise ConnectionError("request not sent")
        self.rows.extend(deepcopy(messages))
        self.rows_by_session.setdefault(session_id, []).extend(deepcopy(messages))
        self.pending_tokens[session_id] = self.pending_tokens.get(session_id, 0) + 10 * len(
            messages
        )
        if self.lose_ack:
            raise ConnectionError("lost acknowledgement")
        return {"added": len(messages) if self.ack_override is None else self.ack_override}

    async def commit_session(self, session_id):
        self.commit_attempts.append(session_id)
        if self.commit_error:
            raise self.commit_error
        self.commits.append(session_id)
        self.commit_sizes.append(len(self.rows_by_session.get(session_id, [])))
        self.pending_tokens[session_id] = 0
        return {"task_id": "extraction-job"}


class ChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Deterministic model results; MAF still owns tools and streaming."""

    def __init__(self, responses=None):
        super().__init__()
        self.inputs = []
        self.responses = iter(responses) if responses is not None else None
        self.error = None

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.inputs.append(deepcopy(list(messages)))
        message = (
            deepcopy(next(self.responses)) if self.responses else Message("assistant", ["Answer"])
        )
        if stream:

            async def updates():
                if self.error:
                    raise self.error
                for content in message.contents:
                    yield ChatResponseUpdate(
                        role="assistant", contents=[content], message_id=message.message_id
                    )

            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

        async def response():
            if self.error:
                raise self.error
            return ChatResponse(messages=[message])

        return response()


def setup(responses=None, **options):
    client = MemoryClient()
    provider = OpenVikingContextProvider(client, **options)
    model = ChatClient(responses)
    agent = Agent(model, context_providers=[provider])
    return client, provider, model, agent, agent.create_session()


async def test_recall_capture_and_explicit_commit():
    client, provider, model, agent, session = setup()
    await agent.run(
        [Message("system", ["Private instructions"]), Message("user", ["My question"])],
        session=session,
    )
    assert "Preferred format: JSON" in json.dumps([m.to_dict() for m in model.inputs[0]])
    assert [row["role"] for row in client.rows] == ["user", "assistant"]
    assert "Preferred format" not in json.dumps(client.rows)
    assert "Private instructions" not in json.dumps(client.rows)
    assert client.searches[0]["options"] == {"max_tokens": 4000, "peer_scope": "all"}
    assert not client.commits
    assert await provider.commit(session) == {"task_id": "extraction-job"}
    assert client.commits == [session.state[provider.source_id]["session_id"]]


@pytest.mark.parametrize("commit_policy", ["never", "always", "pending_tokens"])
@pytest.mark.parametrize("stream", [False, True])
async def test_tools_keep_calls_and_results_paired(commit_policy, stream):
    def lookup_label():
        """Return the current label."""
        return "LABEL"

    client, _, _, agent, session = setup(
        [
            Message(
                "assistant",
                [Content("function_call", call_id="call-1", name="lookup_label", arguments={})],
            ),
            Message("assistant", ["LABEL"]),
        ],
        commit_policy=commit_policy,
        commit_token_threshold=1,
    )
    if stream:
        async with agent.run(
            "Read the label", tools=[lookup_label], session=session, stream=True
        ) as result:
            async for _ in result:
                assert not client.commits
    else:
        await agent.run("Read the label", tools=[lookup_label], session=session)
    parts = [part for row in client.rows for part in row["parts"] if part["type"] == "tool"]
    assert len(parts) == 2
    assert [part["tool_status"] for part in parts] == ["pending", "completed"]
    assert {part["tool_id"] for part in parts} == {"call-1"}
    assert {part["tool_name"] for part in parts} == {"lookup_label"}
    assert parts[1]["tool_output"] == "LABEL"
    assert len(client.commits) == (0 if commit_policy == "never" else 1)
    if client.commits:
        assert client.commit_sizes == [len(client.rows)]


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("commit_policy", ["never", "always", "pending_tokens"])
async def test_stream_capture_requires_complete_consumption(complete, commit_policy):
    client, _, _, agent, session = setup(
        [Message("assistant", ["First", "Second"])],
        commit_policy=commit_policy,
        commit_token_threshold=1,
    )
    async with agent.run("Question", session=session, stream=True) as stream:
        async for _ in stream:
            assert not client.commits
            if not complete:
                break
    assert len(client.rows) == (2 if complete else 0)
    assert len(client.commits) == (1 if complete and commit_policy != "never" else 0)


async def test_restoration_preserves_session_and_acknowledged_message_ids():
    client, provider, _, agent, session = setup()
    question = Message("user", ["Same text"], message_id="original")
    response = await agent.run(question, session=session)
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    await agent.run(
        [question, *response.messages, Message("user", ["Same text"], message_id="new")],
        session=restored,
    )
    assert len(client.rows) == 4
    ids = [row["source_message_ids"][0] for row in client.rows]
    assert len(ids) == len(set(ids))
    assert {"original", "new"}.issubset(ids)
    assert len(set(client.sessions)) == 1
    assert (
        restored.state[provider.source_id]["session_id"]
        == session.state[provider.source_id]["session_id"]
    )


@pytest.mark.parametrize("commit_policy", ["never", "always", "pending_tokens"])
async def test_partial_write_keeps_only_unacknowledged_messages_for_flush(commit_policy):
    client, provider, _, agent, session = setup(
        commit_policy=commit_policy, commit_token_threshold=1
    )
    client.fail_batch = 2
    messages = [Message("user", [f"Item {i}"], message_id=str(i)) for i in range(101)]
    with pytest.raises(Exception, match="request not sent"):
        await agent.run(messages, session=session)
    assert len(client.rows) == 100
    assert len(session.state[provider.source_id]["pending_messages"]) == 2
    assert not client.commit_attempts
    client.fail_batch = None
    await provider.flush(session)
    assert len(client.rows) == 102
    assert not session.state[provider.source_id]["pending_messages"]
    assert len(client.commits) == (0 if commit_policy == "never" else 1)
    await provider.flush(session)
    assert len(client.commits) == (0 if commit_policy == "never" else 1)


@pytest.mark.parametrize("commit_policy", ["never", "always", "pending_tokens"])
async def test_lost_ack_blocks_implicit_retry_and_commit(commit_policy):
    client, provider, model, agent, session = setup(
        commit_policy=commit_policy, commit_token_threshold=1
    )
    client.lose_ack = True
    with pytest.raises(Exception, match="lost acknowledgement"):
        await agent.run("Question", session=session)
    assert len(client.rows) == 2
    assert not client.commit_attempts
    assert len(session.state[provider.source_id]["pending_messages"]) == 2
    with pytest.raises(RuntimeError, match="unconfirmed"):
        await agent.run("Another question", session=session)
    with pytest.raises(RuntimeError, match="pending"):
        await provider.commit(session)
    assert len(model.inputs) == 1
    assert len(client.rows) == 2


async def test_recall_failure_stops_inference_and_remains_retryable():
    client, _, model, agent, session = setup()
    client.recall_error = ConnectionError("recall unavailable")
    with pytest.raises(Exception, match="recall unavailable"):
        await agent.run("Question", session=session)
    assert not model.inputs and not client.rows
    client.recall_error = None
    await agent.run("Question", session=session)
    assert len(client.rows) == 2


@pytest.mark.parametrize("error", [RuntimeError("model failed"), asyncio.CancelledError()])
@pytest.mark.parametrize("commit_policy", ["never", "always", "pending_tokens"])
@pytest.mark.parametrize("stream", [False, True])
async def test_failed_or_cancelled_inference_is_not_captured(error, commit_policy, stream):
    client, _, model, agent, session = setup(commit_policy=commit_policy, commit_token_threshold=1)
    model.error = error
    with pytest.raises(type(error)):
        if stream:
            async with agent.run("Question", session=session, stream=True) as result:
                async for _ in result:
                    pass
        else:
            await agent.run("Question", session=session)
    assert not client.rows
    assert not session.state["openviking"].get("pending_messages")
    assert not client.commit_attempts
    model.error = None
    await agent.run("Retry", session=session)
    assert len(client.rows) == 2


async def test_hidden_reasoning_media_and_other_provider_context_are_excluded():
    client, _, _, agent, session = setup(
        [
            Message(
                "assistant",
                [Content("text", text="Visible"), Content("text_reasoning", text="Hidden")],
            )
        ]
    )
    await agent.run(
        [
            Message("user", ["Question", Content("image", uri="https://example.test/private.png")]),
            Message(
                "user",
                ["Other memory"],
                additional_properties={"_attribution": {"source_id": "other"}},
            ),
        ],
        session=session,
    )
    captured = json.dumps(client.rows)
    assert "Question" in captured and "Visible" in captured
    assert all(value not in captured for value in ("Hidden", "private.png", "Other memory"))
    assert client.searches[0]["query"] == "Question"


async def test_shared_provider_keeps_session_state_separate():
    client, provider, _, agent, first = setup()
    second = agent.create_session()
    await asyncio.gather(agent.run("First", session=first), agent.run("Second", session=second))
    assert (
        first.state[provider.source_id]["session_id"]
        != second.state[provider.source_id]["session_id"]
    )
    assert len(client.rows) == 4
    assert not first.state[provider.source_id]["pending_messages"]
    assert not second.state[provider.source_id]["pending_messages"]


async def loop_capture(
    stream, *, fresh_context=False, return_final_only=False, tools=False, **options
):
    def lookup_label():
        """Return the current label."""
        return "TOOL_VALUE"

    responses = [
        Message("assistant", ["First draft"], message_id="draft"),
        Message("assistant", ["Final answer"], message_id="final"),
    ]
    if tools:
        responses.insert(
            0,
            Message(
                "assistant",
                [Content("function_call", call_id="loop-call", name="lookup_label", arguments={})],
                message_id="tool-call",
            ),
        )
    client, provider, model, agent, session = setup(responses, **options)
    middleware = [
        AgentLoopMiddleware(
            lambda *, iteration, **kwargs: iteration < 2,
            next_message=lambda **kwargs: "Please refine the draft",
            fresh_context=fresh_context,
            return_final_only=return_final_only,
        )
    ]
    # Per-call middleware exercises the public runtime override as well.
    kwargs = {"middleware": middleware, "tools": [lookup_label] if tools else []}
    if stream:
        async with agent.run("Question", session=session, stream=True, **kwargs) as response:
            async for _ in response:
                pass
    else:
        await agent.run("Question", session=session, **kwargs)
    return client, provider, model, session


@pytest.mark.parametrize("tools", [False, True])
@pytest.mark.parametrize("return_final_only", [False, True])
@pytest.mark.parametrize("commit_policy", ["never", "always", "pending_tokens"])
async def test_streamed_agent_loop_preserves_the_same_transcript(
    tools, return_final_only, commit_policy
):
    options = {"commit_policy": commit_policy, "commit_token_threshold": 1}
    ordinary, _, _, _ = await loop_capture(
        False, tools=tools, return_final_only=return_final_only, **options
    )
    streamed, _, _, _ = await loop_capture(
        True, tools=tools, return_final_only=return_final_only, **options
    )

    def transcript(client):
        return [{"role": row["role"], "parts": row["parts"]} for row in client.rows]

    assert transcript(streamed) == transcript(ordinary)
    assert "First draft" in json.dumps(streamed.rows)
    assert "Please refine the draft" in json.dumps(streamed.rows)
    assert "Final answer" in json.dumps(streamed.rows)
    assert "Preferred format" not in json.dumps(streamed.rows)
    assert len(set(streamed.sessions)) == 1
    assert len({row["source_message_ids"][0] for row in streamed.rows}) == len(streamed.rows)
    assert len(streamed.commits) == (0 if commit_policy == "never" else 2)
    assert streamed.commit_sizes == ordinary.commit_sizes
    if streamed.commits:
        assert streamed.commit_sizes == [4 if tools else 2, len(streamed.rows)]
    if tools:
        parts = [p for row in streamed.rows for p in row["parts"] if p["type"] == "tool"]
        assert [p["tool_status"] for p in parts] == ["pending", "completed"]
        assert {p["tool_id"] for p in parts} == {"loop-call"}
        assert parts[1]["tool_output"] == "TOOL_VALUE"


@pytest.mark.parametrize("stream", [False, True])
async def test_loop_cannot_silently_reset_capture_progress(stream):
    with pytest.raises(RuntimeError, match="state.*reset.*fresh_context=True"):
        await loop_capture(stream, fresh_context=True)


@pytest.mark.parametrize("commit_policy", ["never", "always", "pending_tokens"])
async def test_interrupted_streamed_loop_keeps_only_completed_iterations(commit_policy):
    client, _, _, agent, session = setup(
        [
            Message("assistant", ["First draft"], message_id="draft"),
            Message("assistant", ["Final answer"], message_id="final"),
        ],
        commit_policy=commit_policy,
        commit_token_threshold=1,
    )
    loop = AgentLoopMiddleware(lambda *, iteration, **kwargs: iteration < 2)
    async with agent.run("Question", session=session, middleware=[loop], stream=True) as response:
        async for update in response:
            if "Final answer" in update.text:
                break
    assert [p["text"] for row in client.rows for p in row["parts"]] == ["Question", "First draft"]
    assert not session.state["openviking"]["pending_messages"]
    assert len(client.commits) == (0 if commit_policy == "never" else 1)


async def test_state_rollback_blocks_flush_and_commit_but_equal_state_restoration_is_safe():
    client, provider, _, agent, session = setup()
    await agent.run("Question", session=session)
    saved = AgentSession.from_dict(json.loads(json.dumps(session.to_dict()))).state
    session.state = {}
    with pytest.raises(RuntimeError, match="state was reset"):
        await provider.flush(session)
    with pytest.raises(RuntimeError, match="state was reset"):
        await provider.commit(session)
    assert not client.commits
    session.state = saved
    await agent.run("Next", session=session)
    assert len(client.rows) == 4


@pytest.mark.parametrize("threshold,commits", [(19, 1), (20, 1), (21, 0)])
async def test_pending_token_policy_uses_post_capture_server_count(threshold, commits):
    client, _, _, agent, session = setup(
        commit_policy="pending_tokens", commit_token_threshold=threshold
    )
    await agent.run("Question", session=session)
    assert len(client.rows) == 2
    assert len(client.commits) == commits
    if commits:
        assert client.commit_sizes == [2]


async def test_threshold_counts_the_session_backlog_and_survives_restoration():
    client, _, _, agent, session = setup(commit_policy="pending_tokens", commit_token_threshold=30)
    await agent.run("First", session=session)
    assert not client.commits
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    await agent.run("Second", session=restored)
    assert client.commit_sizes == [4]
    await agent.run("Third", session=restored)
    assert client.commit_sizes == [4]


async def test_threshold_does_not_combine_different_sessions():
    client, _, _, agent, first = setup(commit_policy="pending_tokens", commit_token_threshold=30)
    second = agent.create_session()
    await asyncio.gather(agent.run("First", session=first), agent.run("Second", session=second))
    assert not client.commits
    await agent.run("More", session=first)
    assert client.commits == [first.state["openviking"]["session_id"]]
    assert client.pending_tokens[second.state["openviking"]["session_id"]] == 20


@pytest.mark.parametrize("commit_policy", ["never", "always", "pending_tokens"])
@pytest.mark.parametrize("stream", [False, True])
async def test_capture_disabled_keeps_recall_and_tools_without_automatic_writes(
    commit_policy, stream
):
    client, provider, model, session = await loop_capture(
        stream, tools=True, capture=False, commit_policy=commit_policy, commit_token_threshold=1
    )
    assert client.searches
    assert "Preferred format" in json.dumps([m.to_dict() for m in model.inputs[0]])
    assert len(model.inputs) == 3
    assert not client.rows and not client.commit_attempts
    assert not session.state[provider.source_id].get("recorded_message_ids")
    assert await provider.commit(session) == {"task_id": "extraction-job"}
    assert len(client.commits) == 1


@pytest.mark.parametrize("commit_policy", ["always", "pending_tokens"])
@pytest.mark.parametrize("stream", [False, True])
async def test_commit_failure_does_not_replay_acknowledged_capture(commit_policy, stream):
    reply = Message("assistant", ["Answer"], message_id="reply")
    question = Message("user", ["Question"], message_id="question")
    client, provider, _, agent, session = setup(
        [reply, reply], commit_policy=commit_policy, commit_token_threshold=1
    )
    client.commit_error = ConnectionError("commit unavailable")

    async def run():
        if stream:
            async with agent.run(question, session=session, stream=True) as result:
                async for _ in result:
                    pass
        else:
            await agent.run(question, session=session)

    with pytest.raises(Exception, match="commit unavailable"):
        await run()
    assert len(client.rows) == 2
    assert not session.state[provider.source_id]["pending_messages"]
    assert set(session.state[provider.source_id]["recorded_message_ids"]) == {"question", "reply"}
    client.commit_error = None
    await run()
    assert client.batches == 1 and len(client.rows) == 2
    assert len(client.commit_attempts) == 2 and len(client.commits) == 1


async def test_post_capture_metadata_failure_retains_acknowledged_progress():
    reply = Message("assistant", ["Answer"], message_id="reply")
    question = Message("user", ["Question"], message_id="question")
    client, _, _, agent, session = setup(
        [reply, reply], commit_policy="pending_tokens", commit_token_threshold=1
    )
    client.metadata_error = ConnectionError("metadata unavailable")
    with pytest.raises(Exception, match="metadata unavailable"):
        await agent.run(question, session=session)
    assert len(client.rows) == 2 and not client.commit_attempts
    client.metadata_error = None
    await agent.run(question, session=session)
    assert len(client.rows) == 2 and client.batches == 1
    assert len(client.commits) == 1


@pytest.mark.parametrize("commit_policy", ["always", "pending_tokens"])
async def test_incomplete_acknowledgement_never_triggers_commit(commit_policy):
    client, _, _, agent, session = setup(commit_policy=commit_policy, commit_token_threshold=1)
    client.ack_override = 1
    with pytest.raises(Exception, match="acknowledge the complete"):
        await agent.run("Question", session=session)
    assert not client.commit_attempts
    assert len(session.state["openviking"]["pending_messages"]) == 2
    assert not session.state["openviking"].get("recorded_message_ids")


@pytest.mark.parametrize(
    "options",
    [
        {"capture": "false"},
        {"commit_policy": "manual"},
        {"commit_token_threshold": 0},
        {"commit_token_threshold": True},
        {"commit_token_threshold": 1.5},
    ],
)
def test_invalid_capture_and_commit_options_are_rejected(options):
    with pytest.raises(ValueError):
        OpenVikingContextProvider(MemoryClient(), **options)


@pytest.mark.parametrize("commit_policy", ["always", "pending_tokens"])
async def test_disabling_capture_preserves_pending_recovery_without_automatic_commit(commit_policy):
    client, provider, _, agent, session = setup(
        commit_policy=commit_policy, commit_token_threshold=1
    )
    client.fail_batch = 1
    with pytest.raises(Exception, match="request not sent"):
        await agent.run("Question", session=session)
    provider.capture = False
    with pytest.raises(RuntimeError, match="unconfirmed"):
        await agent.run("Another", session=session)
    client.fail_batch = None
    await provider.flush(session)
    assert len(client.rows) == 2 and not client.commits
    await agent.run("Recall only", session=session)
    assert len(client.rows) == 2 and not client.commits
    await provider.commit(session)
    assert len(client.commits) == 1


@pytest.mark.parametrize("commit_policy", ["always", "pending_tokens"])
async def test_no_eligible_capture_does_not_create_or_commit_an_empty_session(commit_policy):
    client, _, _, agent, session = setup(
        [Message("assistant", [Content("text_reasoning", text="Hidden")])],
        commit_policy=commit_policy,
        commit_token_threshold=1,
    )
    await agent.run(Message("system", ["Instructions"]), session=session)
    assert not client.rows and not client.commit_attempts and not client.sessions


@pytest.mark.parametrize("stream", [False, True])
async def test_real_sdk_writes_tool_evidence_before_threshold_commit(monkeypatch, stream):
    rows = []
    writes = []
    pending = 0

    def handle(request):
        nonlocal pending
        assert request.headers["X-API-Key"] == "test-key"
        path = request.url.path
        if path == "/api/v1/search/search":
            result = {"rendered": "Preferred format: JSON", "entries": [{"uri": "viking://memory"}]}
        elif path.endswith("/messages/batch"):
            messages = json.loads(request.content)["messages"]
            rows.extend(messages)
            pending += 10 * len(messages)
            writes.append("capture")
            result = {"added": len(messages), "pending_tokens": pending}
        elif path.endswith("/commit"):
            assert len(rows) == 4
            assert json.loads(request.content)["keep_recent_count"] == 0
            writes.append("commit")
            pending = 0
            result = {"task_id": "extraction-job"}
        else:
            assert request.method == "GET" and path.startswith("/api/v1/sessions/")
            result = {"pending_tokens": pending}
        return httpx.Response(200, json={"status": "ok", "result": result})

    http_client = httpx.AsyncClient

    def create_http_client(**kwargs):
        return http_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", create_http_client)
    client = AsyncHTTPClient(url="http://openviking.test", api_key="test-key", auth_mode="api_key")
    await client.initialize()
    try:

        def lookup_label():
            """Return the current label."""
            return "LABEL"

        model = ChatClient(
            [
                Message(
                    "assistant",
                    [Content("function_call", call_id="call-1", name="lookup_label", arguments={})],
                ),
                Message("assistant", ["LABEL"]),
            ]
        )
        provider = OpenVikingContextProvider(
            client, commit_policy="pending_tokens", commit_token_threshold=40
        )
        agent = Agent(model, context_providers=[provider])
        session = agent.create_session()
        if stream:
            async with agent.run(
                "Question", tools=[lookup_label], session=session, stream=True
            ) as result:
                async for _ in result:
                    assert not writes
        else:
            await agent.run("Question", tools=[lookup_label], session=session)
        assert writes == ["capture", "commit"]
        parts = [p for row in rows for p in row["parts"] if p["type"] == "tool"]
        assert [p["tool_status"] for p in parts] == ["pending", "completed"]
        assert {p["tool_id"] for p in parts} == {"call-1"}
        assert parts[1]["tool_output"] == "LABEL"
        assert "Preferred format" not in json.dumps(rows)
    finally:
        await client.close()
