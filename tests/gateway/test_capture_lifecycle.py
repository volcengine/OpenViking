"""Capture recovery and archive lifetime across requests, branches and restarts."""

import asyncio
import copy
import time

import pytest
from conftest import make_due, replay_records, update_capture
from test_review_regressions import history, prepare, worker_for

from context_gateway.capture import reset_capture
from context_gateway.capture_store import Document
from context_gateway.client import VikingClient, VikingError
from context_gateway.kernel import THINKING
from context_gateway.storage import digest
from context_gateway.tool_protocols import ResponseCapture


@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_terminal_archive_releases_commits(setup_kernel, credential, policy, terminal):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1, keep_recent_messages=0)
    viking.archive_status = terminal
    worker = await worker_for(store, encryption, credential, viking)
    for turns in (1, 2, 3):
        request = await prepare(
            kernel, credential, policy, [*history(turns), {"role": "user", "content": "next"}]
        )
        await worker.once()
    assert len(viking.commits) == 3
    records = await replay_records(store, request, "replacement")
    assert not records


async def test_pending_archive_has_bounded_lifetime_and_shared_polling(
    setup_kernel, credential, policy
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1, keep_recent_messages=0)
    p = await prepare(
        kernel, credential, policy, [*history(1), {"role": "user", "content": "next"}]
    )
    worker = await worker_for(store, encryption, credential, viking)
    await worker.once()
    state = (await store.capture.get(p.scope, p.session)).value
    await update_capture(store, p, archive={**state["archive"], "created": time.time() - 1000})
    calls = []

    async def pending(*args):
        calls.append(1)
        await asyncio.sleep(0.01)
        return "pending"

    viking.archive_state = pending
    await asyncio.gather(*(worker.once() for _ in range(12)))
    state = (await store.capture.get(p.scope, p.session)).value
    assert state["archive"]["status"] == "abandoned"
    assert len(calls) == 1
    assert not await worker.once()


@pytest.mark.parametrize("failure", [True, False])
async def test_existing_replacement_survives_capture_pause_or_plugin(
    setup_kernel, credential, policy, failure
):
    kernel, store, _, _ = setup_kernel
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
        await update_capture(
            store, first, error={"reason": "outage", "attempts": 5, "retry_at": time.time() + 300}
        )
    else:
        await store.replay.put(
            first.scope, first.session, "disabled", "", {"reason": "plugin_present"}
        )
    again = await prepare(kernel, credential, policy, messages)
    assert "verified summary" in again.body["messages"][0]["content"]
    assert messages[0] not in again.body["messages"]


@pytest.mark.parametrize("change", ["edit", "compact"])
async def test_changed_history_resyncs_to_new_session(setup_kernel, credential, policy, change):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False)
    messages = [*history(2), {"role": "user", "content": "next"}]
    first = await prepare(kernel, credential, policy, messages)
    worker = await worker_for(store, encryption, credential, viking)
    await worker.once()
    await worker.once()
    changed = copy.deepcopy(messages)
    if change == "edit":
        changed[1]["content"] = "a corrected answer"
    else:
        changed = [
            {"role": "user", "content": "compact summary"},
            {"role": "assistant", "content": "continue"},
            {"role": "user", "content": "next"},
        ]
    revised = await prepare(kernel, credential, policy, changed)
    assert revised.capture_target != first.capture_target
    assert revised.metrics["capture_status"] == "active"
    await worker.once()
    assert viking.write_sessions[-1] != viking.write_sessions[0]
    assert changed[0]["content"] in str(
        [
            m
            for m, sid in zip(viking.writes, viking.write_sessions, strict=True)
            if sid == revised.capture_target
        ]
    )


async def test_reset_during_delivery_cannot_publish_an_old_archive(
    setup_kernel, credential, policy
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1, keep_recent_messages=0)
    first = await prepare(
        kernel, credential, policy, [*history(1), {"role": "user", "content": "next"}]
    )
    worker = await worker_for(store, encryption, credential, viking)
    entered, released = asyncio.Event(), asyncio.Event()
    original = viking.write

    async def slow(*args):
        entered.set()
        await released.wait()
        return await original(*args)

    viking.write = slow
    task = asyncio.create_task(worker.once())
    await entered.wait()
    route = await reset_capture(store.capture, first.scope, first.session, "manual_reset")
    released.set()
    await task
    assert not (await store.capture.get(first.scope, first.session)).value["archive"]
    revised = await prepare(
        kernel, credential, policy, [*history(1), {"role": "user", "content": "next"}]
    )
    assert revised.capture_target == route["ov_session"]
    await worker.once()
    assert viking.write_sessions[0] != viking.write_sessions[1]
    assert (await store.capture.get(first.scope, first.session)).value["archive"]


async def test_anonymous_continuation_reuses_observed_reply_only(setup_kernel, credential, policy):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)

    async def anonymous(messages):
        return await kernel.prepare(
            {"messages": messages}, "chat", {}, credential, {"id": "upstream"}, policy
        )

    opening = [{"role": "user", "content": "hello"}]
    first, independent = await asyncio.gather(anonymous(opening), anonymous(opening))
    assert first.session != independent.session
    reply = {"role": "assistant", "content": "hello back", "reasoning_content": "opaque"}
    visible = {"role": "assistant", "content": "hello back"}
    await kernel.completed(first, credential, ResponseCapture("chat", reply, complete=True))
    continued = await anonymous([*opening, visible, {"role": "user", "content": "next"}])
    assert continued.session == first.session
    assert continued.capture_target == first.capture_target
    # A second observed identical conversation makes ownership ambiguous.
    await kernel.completed(independent, credential, ResponseCapture("chat", reply, complete=True))
    ambiguous = await anonymous([*opening, visible, {"role": "user", "content": "different"}])
    assert ambiguous.session not in {first.session, independent.session}
    assert ambiguous.capture_target != first.capture_target


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
    assert continued.capture_target == first.capture_target
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


@pytest.mark.parametrize(
    "marker, expected", [(".done", "completed"), (".failed.json", "failed"), ("", "pending")]
)
async def test_archive_state_uses_server_terminal_markers(marker, expected):
    client = VikingClient(None, "http://unused", "0.4.16")
    paths = []

    async def request(method, path, key, **kwargs):
        paths.append(path)
        if marker and path.split("&")[0].endswith(marker):
            return "{}"
        raise VikingError("openviking_http_404", 404)

    client.request = request
    assert (
        await client.archive_state("synthetic", "viking://user/a/sessions/s/history/archive_001")
        == expected
    )
    assert all("/api/v1/content/read?uri=viking%3A" in path for path in paths)


def test_missing_optional_dependencies_explain_install(monkeypatch):
    from context_gateway import cli

    monkeypatch.setattr("sys.argv", ["openviking-context-gateway"])
    monkeypatch.setattr(cli, "find_spec", lambda _: None)
    with pytest.raises(SystemExit, match=r"openviking\[context-gateway\]"):
        cli.main()


async def test_capture_mailboxes_are_independent_and_lease_safe(setup_kernel):
    _, store, _, _ = setup_kernel
    await store.capture.swap("scope", "one", Document(), {"anchor": "same"}, 0)
    leased = await store.capture.claim()
    await store.capture.swap("scope", "two", Document(), {"anchor": "same"}, 0)
    other = await store.capture.claim()
    assert other["session"] == "two"
    assert not await store.capture.claim()
    await store.capture.release({**leased, "owner": "stale"})
    assert not await store.capture.claim()
    for item in (leased, other):
        await store.capture.swap("scope", item["session"], item["document"], {"done": True}, None)
        await store.capture.release(item)
    assert not await store.capture.claim()


@pytest.mark.parametrize("partial_batch", [False, True])
async def test_failed_delivery_sends_the_turn_again(
    setup_kernel, credential, policy, partial_batch
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    assistants = 205 if partial_batch else 1
    messages = [
        {"role": "user", "content": "start"},
        *[{"role": "assistant", "content": f"part {i}"} for i in range(assistants)],
        {"role": "user", "content": "next"},
    ]
    p = await prepare(kernel, credential, policy, messages)
    worker = await worker_for(store, encryption, credential, viking)
    write = viking.write
    calls = 0

    async def interrupted(*args):
        nonlocal calls
        calls += 1
        if partial_batch and calls == 2:
            raise VikingError("connection_lost")
        result = await write(*args)
        if not partial_batch and calls == 1:
            raise VikingError("response_lost_after_append")
        return result

    viking.write = interrupted
    await worker.once()
    await make_due(store)
    await worker.once()
    ids = {sid for batch in viking.writes for m in batch for sid in m["source_message_ids"]}
    assert len(ids) == assistants + 1
    state = (await store.capture.get(p.scope, p.session)).value
    assert not state["error"] and not state["pending"]
    assert state["retained"] == [{"anchor": p.chain[-2], "count": assistants + 1}]


async def test_failed_commit_is_retried_with_later_messages(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1, keep_recent_messages=0)
    viking.archive_status = "completed"
    p = await prepare(
        kernel, credential, policy, [*history(1), {"role": "user", "content": "next"}]
    )
    worker = await worker_for(store, encryption, credential, viking)
    commit = viking.commit

    async def unavailable(*args):
        raise VikingError("commit_unavailable")

    viking.commit = unavailable
    await worker.once()
    state = (await store.capture.get(p.scope, p.session)).value
    assert state["error"]["reason"] == "commit_unavailable" and not state["archive"]
    await prepare(kernel, credential, policy, [*history(2), {"role": "user", "content": "next"}])
    viking.commit = commit
    await make_due(store)
    await worker.once()
    assert [[m["parts"][0]["text"] for m in batch] for batch in viking.archived.values()] == [
        ["Question 0", "Answer 0", "Question 1", "Answer 1"]
    ]


@pytest.mark.parametrize("edit", [False, True])
async def test_concurrent_request_reconciles_inflight_delivery(
    setup_kernel, credential, policy, edit
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False)
    p = await prepare(kernel, credential, policy, history(1)[:1])
    await kernel.completed(p, credential, ResponseCapture("chat", history(1)[1], complete=True))
    await make_due(store)
    worker = await worker_for(store, encryption, credential, viking)
    entered, released = asyncio.Event(), asyncio.Event()
    write = viking.write

    async def slow(*args):
        entered.set()
        await released.wait()
        return await write(*args)

    viking.write = slow
    task = asyncio.create_task(worker.once())
    await asyncio.wait_for(entered.wait(), 2)
    messages = history(2)
    if edit:
        messages[1]["content"] = "edited reply"
    try:
        revised = await prepare(
            kernel, credential, policy, [*messages, {"role": "user", "content": "next"}]
        )
    finally:
        released.set()
        await task
    await worker.once()
    batches = [
        batch
        for batch, target in zip(viking.writes, viking.write_sessions, strict=True)
        if target == revised.capture_target
    ]
    assert [m["parts"][0]["text"] for batch in batches for m in batch] == [
        m["content"] for m in messages
    ]
    assert (revised.capture_target != p.capture_target) is edit


async def test_tool_continuations_replace_pending_tail_without_reset(
    setup_kernel, credential, policy
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    messages = [*history(1), {"role": "user", "content": "Read both files"}]

    async def prepare_response():
        return await kernel.prepare(
            {"input": messages, "store": False},
            "responses",
            {"x-openviking-session": "tool-loop"},
            credential,
            {"id": "upstream"},
            policy,
        )

    first = await prepare_response()
    worker = await worker_for(store, encryption, credential, viking)
    await worker.once()
    initial = (await store.capture.get(first.scope, first.session)).value
    assert initial["tokens"] > 0
    for index in range(2):
        request = await prepare_response()
        response = ResponseCapture("responses")
        response.nonstream(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"call-{index}",
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    }
                ],
            }
        )
        await kernel.completed(request, credential, response)
        messages.extend(response.output_items)
        messages.append(
            {"type": "function_call_output", "call_id": f"call-{index}", "output": "contents"}
        )
    request = await prepare_response()
    answer = {"role": "assistant", "content": "Both files checked"}
    await kernel.completed(request, credential, ResponseCapture("responses", answer, complete=True))
    staged = (await store.capture.get(first.scope, first.session)).value
    assert staged["ov_session"] == first.capture_target
    assert staged["delivered"] == initial["delivered"]
    assert staged["tokens"] == initial["tokens"]
    assert len(staged["pending"]) == 1 and not staged["pending"][0]["confirmed"]
    messages.extend([answer, {"role": "user", "content": "Continue"}])
    await prepare_response()
    await worker.once()
    assert set(viking.write_sessions) == {first.capture_target}
    parts = [part for batch in viking.writes for message in batch for part in message["parts"]]
    assert [part["tool_id"] for part in parts if part["type"] == "tool"] == ["call-0", "call-1"]
    assert sum(part.get("text") == "Question 0" for part in parts) == 1


async def test_worker_merge_preserves_rotated_credential_and_request_fields(
    setup_kernel, credential, policy
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    messages = [*history(1), {"role": "user", "content": "Next"}]
    first = await prepare(kernel, credential, policy, messages)
    worker = await worker_for(store, encryption, credential, viking)
    entered, released = asyncio.Event(), asyncio.Event()
    write = viking.write
    keys = []

    async def slow(key, *args):
        keys.append(key)
        entered.set()
        await released.wait()
        return await write(key, *args)

    viking.write = slow
    task = asyncio.create_task(worker.once())
    await asyncio.wait_for(entered.wait(), 2)
    rotated = {**credential, "id": "replacement-key", "openviking_key": "replacement-secret"}
    try:
        await worker.management.save("tenant", "keys", rotated["id"], rotated)
        current = await prepare(kernel, rotated, policy, messages)
        await kernel.completed(
            current,
            rotated,
            ResponseCapture("chat", {"role": "assistant", "content": "Last"}, complete=True),
        )
        await worker.management.delete("tenant", "keys", credential["id"])
        # Concurrent request metadata belongs to the request, including future fields.
        await update_capture(store, current, request_metadata="new")
    finally:
        released.set()
        await task
    merged = (await store.capture.get(first.scope, first.session)).value
    assert merged["credential_id"] == rotated["id"]
    assert merged["request_metadata"] == "new"
    await make_due(store)
    await worker.once()
    assert keys == [credential["openviking_key"], rotated["openviking_key"]]
    assert any(m["parts"][0].get("text") == "Last" for batch in viking.writes for m in batch)
    worker.management.close()
