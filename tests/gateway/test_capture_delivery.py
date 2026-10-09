"""Delivery at gateway cuts, session lineage and the history search hint."""

import asyncio

import pytest
from conftest import MCP_TOOLS, make_due, mcp_tool, update_capture
from test_review_regressions import history, prepare, worker_for

from openviking_gateway.blocks import history_hint
from openviking_gateway.capture import (
    CapturePipeline,
    lineage,
    new_capture,
    reset_capture,
)
from openviking_gateway.tool_catalog import select_tools
from openviking_gateway.tool_protocols import ResponseCapture

PROTOCOLS = ["chat", "anthropic", "responses"]
TURN = ["Question 0", "Answer 0", "Fix the build"]
FULL = [*TURN, "Step 0", "call-0", "Step 1", "call-1", "Done"]


def call(protocol, index, text=True):
    """One assistant tool call, as the protocol's output items."""
    ident, note = f"call-{index}", f"Step {index}"
    if protocol == "anthropic":
        use = {"type": "tool_use", "id": ident, "name": "run", "input": {}}
        return [{"role": "assistant", "content": [{"type": "text", "text": note}] * text + [use]}]
    if protocol == "responses":
        item = {"type": "function_call", "call_id": ident, "name": "run", "arguments": "{}"}
        message = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": note}],
        }
        return [message, item] if text else [item]
    function = {"name": "run", "arguments": "{}"}
    return [
        {
            "role": "assistant",
            "content": note if text else None,
            "tool_calls": [{"id": ident, "type": "function", "function": function}],
        }
    ]


def result(protocol, index):
    ident, output = f"call-{index}", f"output {index}"
    if protocol == "anthropic":
        block = {"type": "tool_result", "tool_use_id": ident, "content": output}
        return {"role": "user", "content": [block]}
    if protocol == "responses":
        return {"type": "function_call_output", "call_id": ident, "output": output}
    return {"role": "tool", "tool_call_id": ident, "content": output}


def answer(text):
    return {"role": "assistant", "content": text}


class Conversation:
    """One client session driven through the kernel the way a client sends it."""

    def __init__(self, setup, credential, policy, protocol="chat", messages=None):
        self.kernel, self.store = setup[0], setup[1]
        self.credential, self.policy, self.protocol = credential, policy, protocol
        self.messages = messages or [*history(1), {"role": "user", "content": "Fix the build"}]

    async def prepare(self):
        body = {
            "model": "model",
            "input" if self.protocol == "responses" else "messages": [*self.messages],
        }
        if self.protocol == "responses":
            body["store"] = False
        return await self.kernel.prepare(
            body,
            self.protocol,
            {"x-openviking-session": "cut"},
            self.credential,
            {"id": "upstream"},
            self.policy,
        )

    async def reply(self, items, cut=False):
        """Serve the history, cutting at its last message if asked, then append the reply."""
        request = await self.prepare()
        if cut:
            await CapturePipeline(self.store.capture).confirm_cut(
                request, request.capture_chain[-1]
            )
        response = ResponseCapture(
            self.protocol,
            items[-1],
            complete=True,
            output_items=items if self.protocol == "responses" else None,
        )
        await self.kernel.completed(request, self.credential, response)
        self.messages.extend(items)
        return request

    async def say(self, text):
        self.messages.append({"role": "user", "content": text})
        return await self.prepare()

    async def state(self, request):
        return (await self.store.capture.get(request.scope, request.session)).value


def sent(viking):
    """Delivered text and tool IDs per OpenViking session, in delivery order."""
    labels = {}
    for batch, session in zip(viking.writes, viking.write_sessions, strict=True):
        for message in batch:
            for part in message["parts"]:
                labels.setdefault(session, []).append(part.get("text") or part["tool_id"])
    return labels


def assert_once(viking):
    sources = {}
    for batch, session in zip(viking.writes, viking.write_sessions, strict=True):
        sources.setdefault(session, []).extend(s for m in batch for s in m["source_message_ids"])
    for ids in sources.values():
        assert len(ids) == len(set(ids))


async def while_delivering(worker, viking, action):
    """Run action while the worker's OpenViking write is in flight."""
    entered, released = asyncio.Event(), asyncio.Event()
    write = viking.write

    async def slow(*args):
        entered.set()
        await released.wait()
        return await write(*args)

    viking.write = slow
    task = asyncio.create_task(worker.once())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await action()
    finally:
        released.set()
        await task
        viking.write = write


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_cut_inside_a_tool_loop_is_delivered_at_once_and_the_turn_once(
    setup_kernel, credential, policy, protocol
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy, protocol)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call(protocol, 0))
    assert await worker.once()
    chat.messages.append(result(protocol, 0))
    await chat.reply(call(protocol, 1), cut=True)
    assert await worker.once()
    assert sent(viking) == {first.capture_target: [*TURN, "Step 0", "call-0"]}
    # The reply after the cut is not confirmed until the turn ends or goes idle.
    assert not await worker.once()
    chat.messages.append(result(protocol, 1))
    await chat.reply([answer("Done")])
    await chat.say("Thanks")
    assert await worker.once()
    assert sent(viking) == {first.capture_target: FULL}
    assert_once(viking)
    state = await chat.state(first)
    assert not state["pending"] and not state["idle"]
    ids = [s for batch in viking.writes for m in batch for s in m["source_message_ids"]]
    # One retained entry per turn, so commits never split the cut turn.
    assert [entry["count"] for entry in state["retained"]] == [2, len(ids) - 2]


@pytest.mark.parametrize("in_flight", ["previous_turn", "cut"])
async def test_requests_and_worker_share_the_queue_around_a_cut(
    setup_kernel, credential, policy, in_flight
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("chat", 0))

    async def cut():
        chat.messages.append(result("chat", 0))
        await chat.reply(call("chat", 1), cut=True)

    async def finish():
        chat.messages.append(result("chat", 1))
        await chat.reply([answer("Done")])
        await chat.say("Thanks")

    if in_flight == "previous_turn":
        await while_delivering(worker, viking, cut)
        await finish()
    else:
        assert await worker.once()
        await cut()
        await while_delivering(worker, viking, finish)
    for _ in range(2):
        await worker.once()
    assert sent(viking) == {first.capture_target: FULL}
    assert_once(viking)
    assert not (await chat.state(first))["pending"]


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_cut_requeuing_an_idle_tail_in_flight_is_trimmed_not_reset(
    setup_kernel, credential, policy, protocol
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(
        setup_kernel, credential, policy, protocol, [{"role": "user", "content": "Fix the build"}]
    )
    worker = await worker_for(store, encryption, credential, viking)
    # Only the user message of this tail is captured: the call has no result yet.
    first = await chat.reply(call(protocol, 0, text=False))
    await make_due(store)

    async def cut():
        chat.messages.append(result(protocol, 0))
        await chat.reply(call(protocol, 1), cut=True)

    await while_delivering(worker, viking, cut)
    assert sent(viking) == {first.capture_target: ["Fix the build", "call-0"]}
    state = await chat.state(first)
    assert state["ov_session"] == first.capture_target and not state["idle"]
    assert [entry["count"] for entry in state["retained"]] == [2]
    # The reply after the cut stays an unconfirmed tail.
    assert [t["confirmed"] for t in state["pending"]] == [False]
    assert not await worker.once()
    chat.messages.append(result(protocol, 1))
    await chat.reply([answer("Done")])
    await chat.say("Thanks")
    assert await worker.once()
    assert sent(viking) == {
        first.capture_target: ["Fix the build", "call-0", "Step 1", "call-1", "Done"]
    }
    assert_once(viking)


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_idle_tail_that_gains_its_tool_result_in_flight_is_delivered_again(
    setup_kernel, credential, policy, protocol
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy, protocol)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call(protocol, 0))
    assert await worker.once()
    await make_due(store)

    async def finish():
        chat.messages.append(result(protocol, 0))
        await chat.reply([answer("Done")])

    # The idle tail lacks the unanswered call; the copy queued during its write has it.
    await while_delivering(worker, viking, finish)
    await chat.say("Thanks")
    assert await worker.once()
    state = await chat.state(first)
    assert sent(viking)[state["ov_session"]] == [*TURN, "Step 0", "call-0", "Done"]
    assert_once(viking)


async def test_only_a_due_tail_moves_a_changed_history_to_a_new_session(
    setup_kernel, credential, policy
):
    policy.update(recall=False)
    chat = Conversation(setup_kernel, credential, policy)
    first = await chat.reply([answer("Answer A")])
    # Regenerating an answer that is not due for saving keeps the session.
    chat.messages.pop()
    assert (await chat.prepare()).capture_target == first.capture_target
    await chat.reply([answer("Answer B")])
    # The worker may already be writing a due answer, so changing it starts over.
    await make_due(setup_kernel[1])
    chat.messages.pop()
    assert (await chat.prepare()).capture_target != first.capture_target
    assert (await chat.state(first))["reason"] == "history_changed"


@pytest.mark.parametrize("cut", [False, True])
async def test_extending_a_turn_delivered_while_idle_starts_a_new_session(
    setup_kernel, credential, policy, cut
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("chat", 0))
    assert await worker.once()
    await make_due(store)
    assert await worker.once()  # The idle tail lacks the unanswered call.
    assert sent(viking)[first.capture_target] == [*TURN, "Step 0"]
    chat.messages.append(result("chat", 0))
    later = await chat.reply([answer("Done")], cut=cut)
    state = await chat.state(first)
    assert state["reason"] == "continued_after_idle"
    assert state["previous"] == [first.capture_target]
    if cut:
        assert later.capture_target == state["ov_session"]
        assert await worker.once()  # The cut is delivered before the turn ends.
        assert sent(viking)[state["ov_session"]] == [*TURN, "Step 0", "call-0"]
    await chat.say("Thanks")
    assert await worker.once()
    assert sent(viking) == {
        first.capture_target: [*TURN, "Step 0"],
        state["ov_session"]: [*TURN, "Step 0", "call-0", "Done"],
    }
    assert_once(viking)


@pytest.mark.parametrize("change", ["edit", "regenerate"])
async def test_cut_then_changed_history_moves_to_a_linked_session(
    setup_kernel, credential, policy, change
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("chat", 0))
    chat.messages.append(result("chat", 0))
    await chat.reply(call("chat", 1), cut=True)
    for _ in range(2):
        await worker.once()
    chat.messages.append(result("chat", 1))
    stale = await chat.prepare()  # A continuation of the branch the user abandons.
    if change == "edit":
        chat.messages = [
            {"role": "user", "content": "Question 0, corrected"},
            *chat.messages[1:3],
        ]
    else:
        chat.messages = chat.messages[:3]
    revised = await chat.reply([answer("Done differently")])
    assert revised.capture_target != first.capture_target
    before = await store.capture.get(first.scope, first.session)
    await CapturePipeline(store.capture).confirm_cut(stale, stale.capture_chain[-1])
    assert (await store.capture.get(first.scope, first.session)).version == before.version
    await chat.say("Thanks")
    assert await worker.once()
    assert sent(viking) == {
        first.capture_target: [*TURN, "Step 0", "call-0"],
        revised.capture_target: [chat.messages[0]["content"], *TURN[1:], "Done differently"],
    }
    assert_once(viking)
    state = await chat.state(first)
    assert lineage(state) == [revised.capture_target, first.capture_target]


@pytest.mark.parametrize("case", ["capture_off", "disabled", "already_delivered", "superseded"])
async def test_confirm_cut_leaves_the_queue_alone(setup_kernel, credential, policy, case):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, capture=case != "capture_off")
    chat = Conversation(setup_kernel, credential, policy)
    await chat.reply(call("chat", 0))
    chat.messages.append(result("chat", 0))
    request = await chat.prepare()
    pipeline = CapturePipeline(store.capture)
    if case == "disabled":
        request.disabled = True
    elif case == "already_delivered":
        await pipeline.confirm_cut(request, request.capture_chain[-1])
        assert await (await worker_for(store, encryption, credential, viking)).once()
    elif case == "superseded":
        chat.messages = [*chat.messages[:2], {"role": "user", "content": "Something else"}]
        await chat.prepare()
    before = await store.capture.get(request.scope, request.session)
    await pipeline.confirm_cut(request, request.capture_chain[-1])
    after = await store.capture.get(request.scope, request.session)
    assert after == before


async def test_turn_queued_during_archive_observation_survives_the_worker(
    setup_kernel, credential, policy
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1, keep_recent_messages=0)
    first = await prepare(
        kernel, credential, policy, [*history(1), {"role": "user", "content": "next"}]
    )
    worker = await worker_for(store, encryption, credential, viking)
    assert await worker.once()  # Deliver and commit; the archive is pending.
    await update_capture(store, first, error={"reason": "outage", "attempts": 1, "retry_at": 0})
    await make_due(store)
    entered, released = asyncio.Event(), asyncio.Event()

    async def observed(*args):
        entered.set()
        await released.wait()
        return "completed"

    viking.archive_state = observed
    task = asyncio.create_task(worker.once())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await prepare(
            kernel, credential, policy, [*history(2), {"role": "user", "content": "more"}]
        )
    finally:
        released.set()
        await task
    state = (await store.capture.get(first.scope, first.session)).value
    assert not state["error"]
    assert [t["messages"][0]["parts"][0]["text"] for t in state["pending"]] == ["Question 1"]
    assert await worker.once()
    assert sent(viking)[first.capture_target] == [
        "Question 0",
        "Answer 0",
        "Question 1",
        "Answer 1",
    ]


async def test_turn_ending_in_a_system_message_is_not_delivered_again(
    setup_kernel, credential, policy
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1, keep_recent_messages=0)
    reminder = {"role": "system", "content": "Reminder"}
    messages = [*history(1), reminder, {"role": "user", "content": "next"}]
    first = await prepare(kernel, credential, policy, messages)
    worker = await worker_for(store, encryption, credential, viking)
    assert await worker.once()  # Delivered and archived, so no live source ID dedupes it.
    assert (await store.capture.get(first.scope, first.session)).value["delivered"]
    messages = [*messages, answer("Answer next"), {"role": "user", "content": "more"}]
    await prepare(kernel, credential, policy, messages)
    await make_due(store)
    assert await worker.once()
    assert sent(viking) == {first.capture_target: ["Question 0", "Answer 0", "next", "Answer next"]}
    assert_once(viking)


async def test_rotations_record_the_sessions_that_received_messages(
    setup_kernel, credential, policy
):
    assert lineage({}) == []
    fresh = new_capture()
    assert fresh["previous"] == [] and lineage(fresh) == []
    used = {**fresh, "delivered": "anchor", "previous": ["a", "b", "c", "d", "e"]}
    assert lineage(used) == [fresh["ov_session"], "a", "b", "c", "d", "e"]
    rotated = new_capture("history_changed", used)
    assert rotated["previous"] == [fresh["ov_session"], "a", "b", "c", "d"]
    # A session that never received a message is not worth searching.
    assert new_capture("history_changed", rotated)["previous"] == rotated["previous"]
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False)
    first = await prepare(
        kernel, credential, policy, [*history(1), {"role": "user", "content": "next"}]
    )
    assert await (await worker_for(store, encryption, credential, viking)).once()
    route = await reset_capture(store.capture, first.scope, first.session, "manual_reset")
    assert route["previous"] == [first.capture_target]


def test_history_hint_lists_sessions_and_follows_the_frozen_read_tool():
    tools = select_tools(MCP_TOOLS, {})
    hint = history_hint("alice", ["new", "old"], tools, True)
    assert hint.splitlines()[1:3] == [
        "- viking://user/alice/sessions/new/",
        "- viking://user/alice/sessions/old/",
    ]
    assert "openviking_grep" in hint and "history/archive_NNN/messages.jsonl" in hint
    assert "offset" not in hint
    ranged = select_tools(
        [
            *(t for t in MCP_TOOLS if t["name"] != "read"),
            mcp_tool(
                "read",
                {"uris": {"type": "array"}, "offset": {"type": "integer"}, "limit": {}},
                ("uris",),
            ),
        ],
        {},
    )
    assert "offset and limit" in history_hint("alice", ["new"], ranged, True)
    grep_off = select_tools(MCP_TOOLS, {"disabled_tools": ["grep"]})
    assert history_hint("alice", ["new"], tools, False) == ""
    assert history_hint("alice", [], tools, True) == ""
    assert history_hint("alice", ["new"], grep_off, True) == ""
