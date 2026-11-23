"""OpenViking sessions across requests, branches and resets: creation, budget and replay."""

import asyncio

import pytest
from test_review_regressions import history, prepare

from openviking_gateway.capture import reset_capture
from openviking_gateway.client import CALL, CallContext, VikingClient, VikingError, without_deadline
from openviking_gateway.kernel import THINKING
from openviking_gateway.models import Policy
from openviking_gateway.storage import digest
from openviking_gateway.tool_protocols import ResponseCapture

REPLY = ResponseCapture("chat", {"role": "assistant", "content": "hello"}, complete=True)


@pytest.mark.parametrize("failure", [True, False])
async def test_existing_replacement_survives_a_failed_save_or_plugin(
    setup_kernel, credential, policy, failure
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    messages = [*history(2), {"role": "user", "content": "next"}]
    first = await prepare(kernel, credential, policy, messages)
    await store.replay.put(
        first.scope,
        first.session,
        "replacement",
        first.chain[1],
        {"source": "compaction", "text": "verified summary", "tokens": 4},
    )
    if failure:
        viking.write_failure = VikingError("openviking_http_503", 503)
        with pytest.raises(VikingError):
            await kernel.save(first, credential, REPLY)
    else:
        await store.replay.put(
            first.scope, first.session, "disabled", "", {"reason": "plugin_present"}
        )
    again = await prepare(kernel, credential, policy, messages)
    assert "verified summary" in again.body["messages"][0]["content"]
    assert messages[0] not in again.body["messages"]


async def test_reset_during_a_save_leaves_the_new_session_to_the_next_turn(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    messages = [*history(1), {"role": "user", "content": "next"}]
    first = await prepare(kernel, credential, policy, messages)
    entered, released = asyncio.Event(), asyncio.Event()
    write = viking.write

    async def slow(*args):
        entered.set()
        await released.wait()
        return await write(*args)

    viking.write = slow
    task = asyncio.create_task(kernel.save(first, credential, REPLY))
    await asyncio.wait_for(entered.wait(), 2)
    route = await reset_capture(store.capture, first.scope, first.session, "manual_reset")
    released.set()
    assert await task == 4
    viking.write = write
    state = (await store.capture.get(first.scope, first.session)).value
    assert state["ov_session"] == route["ov_session"] and not state["delivered"]
    messages = [*messages, {"role": "assistant", "content": "hello"}]
    later = await prepare(
        kernel, credential, policy, [*messages, {"role": "user", "content": "more"}]
    )
    assert await kernel.save(later, credential, REPLY) == 6
    assert viking.write_sessions[1] == route["ov_session"] != viking.write_sessions[0]


async def test_anonymous_continuation_reuses_observed_reply_only(setup_kernel, credential, policy):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)

    async def anonymous(messages):
        return await kernel.prepare(
            {"messages": messages}, "chat", {}, credential, {"id": "upstream"}, policy
        )

    def target(request):
        return request.capture.value["ov_session"]

    opening = [{"role": "user", "content": "hello"}]
    first, independent = await asyncio.gather(anonymous(opening), anonymous(opening))
    assert first.session != independent.session
    reply = {"role": "assistant", "content": "hello back", "reasoning_content": "opaque"}
    visible = {"role": "assistant", "content": "hello back"}
    await kernel.completed(first, credential, ResponseCapture("chat", reply, complete=True))
    continued = await anonymous([*opening, visible, {"role": "user", "content": "next"}])
    assert continued.session == first.session
    assert target(continued) == target(first)
    # A second observed identical conversation makes ownership ambiguous.
    await kernel.completed(independent, credential, ResponseCapture("chat", reply, complete=True))
    ambiguous = await anonymous([*opening, visible, {"role": "user", "content": "different"}])
    assert ambiguous.session not in {first.session, independent.session}
    assert target(ambiguous) != target(first)


@pytest.mark.parametrize("resent", ["text", "dropped", "thinking"])
async def test_unsigned_thinking_resent_in_any_form_matches_the_reply(
    setup_kernel, credential, policy, resent
):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False)

    async def anonymous(messages):
        return await kernel.prepare(
            {"messages": messages}, "anthropic", {}, credential, {"id": "upstream"}, policy
        )

    opening = [{"role": "user", "content": "Check the deploy, unsigned"}]
    first = await anonymous(opening)
    thinking = {"type": "thinking", "thinking": "Look at the blue cluster first."}
    reply = {"role": "assistant", "content": [thinking, {"type": "text", "text": "Blue is up."}]}
    await kernel.completed(first, credential, ResponseCapture("anthropic", reply, complete=True))
    # Clients such as pi turn unsigned thinking into text; others drop or keep it.
    resend = {
        "text": [{"type": "text", "text": thinking["thinking"]}],
        "dropped": [],
        "thinking": [thinking],
    }[resent]
    visible = {"role": "assistant", "content": [*resend, {"type": "text", "text": "Blue is up."}]}
    continued = await anonymous([*opening, visible, {"role": "user", "content": "And green?"}])
    assert continued.session == first.session
    assert continued.capture.value["ov_session"] == first.capture.value["ov_session"]
    assert continued.metrics["capture_reason"] == ""
    # The upstream still gets the history exactly as the client sent it.
    assert continued.body["messages"][1] == visible


async def test_signed_thinking_text_is_not_mistaken_for_a_resent_block(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    signed = {"type": "thinking", "thinking": "Same words", "signature": "sig"}
    reply = {"role": "assistant", "content": [signed, {"type": "text", "text": "Done."}]}
    first = await kernel.prepare(
        {"messages": [{"role": "user", "content": "Go"}]},
        "anthropic",
        {"x-openviking-session": "signed"},
        credential,
        {"id": "upstream"},
        policy,
    )
    await kernel.completed(first, credential, ResponseCapture("anthropic", reply, complete=True))
    assert not await store.state.read(first.scope, [THINKING + digest("Same words")])
    said = {"role": "assistant", "content": [{"type": "text", "text": "Same words"}]}
    history = [{"role": "user", "content": "Go"}, said]
    assert await kernel.sent_history(first.scope, history, "anthropic") == (history, [])


def fake_openviking(client, version, calls, conflict=()):
    """Answer the client's calls as OpenViking ``version`` would, recording each but /health."""

    async def request(method, path, key, body=None, timeout=30, peer=None):
        if path == "/health":
            return {"version": version, "role": "user", "account_id": "tenant", "user_id": "a"}
        calls.append((method, path, body, CALL.get()))
        if method == "POST" and body.get("session_id") in conflict:
            raise VikingError("openviking_http_409", 409)
        return {}

    client.request = request


@pytest.mark.parametrize("version", ["0.4.23", "0.5.0"])
async def test_session_is_created_for_whoever_commits_it(version):
    client, calls = VikingClient(None, "http://unused", "0.4.16"), []
    fake_openviking(client, version, calls, conflict={"old"})
    await client.create_session("key", "gateway", Policy(commit_tokens=5000))
    await client.create_session(
        "key",
        "new",
        Policy(commit_tokens=5000, keep_recent_messages=4, idle_seconds=60),
        auto_commit=True,
    )
    await client.create_session(
        "key",
        "old",
        Policy(commit_tokens=2_000_000, idle_seconds=900.4, capture_memory_types=[]),
        auto_commit=True,
    )
    (_, _, gateway, _), (_, _, new, _), (_, _, old, _), (method, path, patch, _) = calls
    # The gateway commits this one, so OpenViking's automatic commits are off.
    assert gateway["auto_commit_policy"] == {
        "pending_token_threshold": 0,
        "message_count_threshold": 0,
        "idle_timeout_seconds": 0,
    }
    assert new["auto_commit_policy"] == {
        "pending_token_threshold": 5000,
        "message_count_threshold": 0,
        "idle_timeout_seconds": 300,
        "keep_recent_count": 4,
    }
    assert old["auto_commit_policy"] == {
        "pending_token_threshold": 1_000_000,
        "message_count_threshold": 0,
        "idle_timeout_seconds": 900,
        "keep_recent_count": 10,
    }
    # A session that exists already gets the same automatic commits.
    assert (method, path, patch) == (
        "PATCH",
        "/api/v1/sessions/old/config",
        {"auto_commit_policy": old["auto_commit_policy"]},
    )
    if version == "0.4.23":
        assert new["memory_policy"] == {"working_memory": {"enabled": False}}
        assert old["memory_policy"] == {"working_memory": {"enabled": False}, "memory_types": []}
    else:
        # An absent session policy lets OpenViking resolve the user's policy.
        assert "memory_policy" not in new
        assert old["memory_policy"] == {"memory_types": []}

    async def refused(*args, **kwargs):
        raise VikingError("openviking_http_403", 403)

    client.request = refused
    with pytest.raises(VikingError):
        await client.create_session("key", "denied", Policy())


async def test_save_outlives_the_request_budget(setup_kernel, credential, policy):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    request = await prepare(kernel, credential, policy, [{"role": "user", "content": "first"}])
    client, calls = VikingClient(None, "http://unused", "0.4.16"), []
    fake_openviking(client, "0.5.0", calls)
    kernel.capture.viking = client
    bound = CALL.set(CallContext(request_id="request-1", circuit="tenant", deadline=0))
    try:
        with pytest.raises(VikingError, match="openviking_budget_exhausted"):
            client.budget(5)
        with without_deadline():
            assert client.budget(5) == 5
        assert await kernel.save(request, credential, REPLY) == 2
        assert CALL.get().deadline == 0
    finally:
        CALL.reset(bound)
    session = (await store.capture.get(request.scope, request.session)).value["ov_session"]
    assert [path for _, path, _, _ in calls] == [
        "/api/v1/sessions",
        f"/api/v1/sessions/{session}/messages/batch",
    ]
    assert all(c == CallContext("request-1", "tenant", None) for *_, c in calls)
