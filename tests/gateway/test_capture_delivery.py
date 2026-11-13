"""Delivery at gateway cuts, session lineage and the history search hint."""

import asyncio

import pytest
from conftest import MCP_TOOLS, make_due, mcp_tool, update_capture
from test_review_regressions import history, prepare, worker_for

from openviking_gateway.blocks import history_hint
from openviking_gateway.capture import (
    MAX_CONFIRMED,
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

    def __init__(self, setup, credential, policy, protocol="chat", messages=None, header=True):
        self.kernel, self.store = setup[0], setup[1]
        self.headers = {"x-openviking-session": "cut"} if header else {}
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
            self.headers,
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


async def test_regenerating_an_answer_keeps_a_named_session(setup_kernel, credential, policy):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False)
    chat = Conversation(setup_kernel, credential, policy)
    first = await chat.reply([answer("Answer A")])
    # Regenerating an answer that is not due for saving replaces it.
    chat.messages.pop()
    assert (await chat.prepare()).capture_target == first.capture_target
    await chat.reply([answer("Answer B")])
    # One the worker may already be writing is kept, and the new one follows it.
    await make_due(store)
    chat.messages.pop()
    assert (await chat.prepare()).capture_target == first.capture_target
    assert (await chat.state(first))["reason"] == ""
    await chat.reply([answer("Answer C")])
    await drain(store, await worker_for(store, encryption, credential, viking))
    assert sent(viking) == {first.capture_target: [*TURN, "Answer B", "Answer C"]}


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
async def test_cut_then_changed_history_stays_in_the_named_session(
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
    assert revised.capture_target == first.capture_target
    before = await store.capture.get(first.scope, first.session)
    await CapturePipeline(store.capture).confirm_cut(stale, stale.capture_chain[-1])
    assert (await store.capture.get(first.scope, first.session)).version == before.version
    await chat.say("Thanks")
    assert await worker.once()
    # The session keeps the abandoned attempt, including its queued step; an edited
    # first message is not resent, but the user message after it is.
    edited = ["Question 0, corrected", "Answer 0", "Fix the build"] if change == "edit" else []
    assert sent(viking) == {
        first.capture_target: [*TURN, "Step 0", "call-0", "Step 1", *edited, "Done differently"]
    }
    state = await chat.state(first)
    assert lineage(state) == [first.capture_target]


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


async def test_delivery_401_removes_key_pauses_and_resumes_on_new_request(
    setup_kernel, credential, policy
):
    from openviking_gateway.client import VikingError

    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply([answer("Completed before revocation")])
    await make_due(store)
    original_write, original_identify, calls = viking.write, viking.identify, []

    async def refused(*args):
        calls.append(args)
        raise VikingError("openviking_http_401", 401)

    viking.write = refused
    viking.identify = refused
    assert await worker.once()
    # The write's 401 is confirmed on /health before the key is dropped.
    assert len(calls) == 2
    assert await worker.management.get("tenant", "keys", credential["id"]) is None
    assert (await chat.state(first))["error"]["reason"] == "openviking_http_401"
    await make_due(store)
    assert await worker.once()
    state = await chat.state(first)
    assert state["error"]["reason"] == "gateway_key_revoked"
    assert state["error"]["retry_at"] is None
    for _ in range(3):
        assert not await worker.once()
    assert len(calls) == 2
    await worker.management.save("tenant", "keys", credential["id"], credential)
    viking.write, viking.identify = original_write, original_identify
    await chat.say("Resume with a valid key")
    assert not (await chat.state(first))["error"]
    await make_due(store)
    assert await worker.once()
    assert viking.writes and not (await chat.state(first))["pending"]
    worker.management.close()


async def test_delivery_401_keeps_key_when_health_accepts_it(setup_kernel, credential, policy):
    from openviking_gateway.client import VikingError

    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply([answer("OpenViking's own model key is invalid")])
    await make_due(store)

    async def refused(*args):
        raise VikingError("openviking_http_401", 401)

    viking.write = refused
    assert await worker.once()
    assert await worker.management.get("tenant", "keys", credential["id"])
    state = await chat.state(first)
    assert state["error"]["reason"] == "openviking_http_401"
    assert state["error"]["retry_at"] is not None
    worker.management.close()


SUMMARY = "This session is being continued from a previous conversation. Fix the build."
BEFORE = [*TURN, "Step 0", "call-0", "Step 1"]
# The client's summary, the latest step it kept, and the reply.
AFTER = [SUMMARY, "Step 1", "call-1", "Done"]
LEADS = {
    "chat": {"role": "system", "content": "Be brief."},
    "responses": {"role": "developer", "content": "Be brief."},
}


async def compact_mid_turn(chat, worker, keep_first=False):
    """A tool loop the client compacts: its summary follows the instructions, which carry
    no anchor, and the first user message if kept; the last step stays."""
    lead = [m for m in chat.messages[:1] if m.get("role") in {"system", "developer"}]
    lead = chat.messages[: len(lead) + keep_first]
    protocol = chat.protocol
    first = await chat.reply(call(protocol, 0))
    assert await worker.once()  # The confirmed history is delivered at once.
    chat.messages.append(result(protocol, 0))
    await chat.reply(call(protocol, 1))
    summary = {"role": "user", "content": SUMMARY}
    chat.messages = [*lead, summary, *call(protocol, 1), result(protocol, 1)]
    return first


async def drain(store, worker):
    await make_due(store)
    while await worker.once():
        pass


@pytest.mark.parametrize(
    "then, lead, keep_first",
    [
        ("next_message", "chat", False),
        ("session_ends", "responses", False),
    ],
)
async def test_mid_turn_client_compaction_keeps_the_named_session(
    setup_kernel, credential, policy, then, lead, keep_first
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    protocol = lead or "chat"
    messages = [*history(1), {"role": "user", "content": "Fix the build"}]
    chat = Conversation(
        setup_kernel, credential, policy, protocol, [*([LEADS[lead]] if lead else []), *messages]
    )
    worker = await worker_for(store, encryption, credential, viking)
    first = await compact_mid_turn(chat, worker, keep_first)
    after = await chat.reply([answer("Done")])
    assert after.capture_target == first.capture_target
    assert await worker.once()  # What was queued before the compaction is due at once.
    # A kept user message the session cannot prove it holds is saved again.
    kept = ["Question 0"] if keep_first else []
    assert sent(viking) == {first.capture_target: [*BEFORE, *kept]}
    tail = []
    if then == "next_message":  # Within the idle window.
        chat.messages.append({"role": "user", "content": "Thanks"})
        later = await chat.reply([answer("Welcome")])
        assert later.capture_target == first.capture_target
        tail = ["Thanks", "Welcome"]
    await drain(store, worker)
    assert sent(viking) == {first.capture_target: [*BEFORE, *kept, *AFTER, *tail]}
    if not keep_first:
        assert_once(viking)


async def test_mid_turn_client_compaction_without_a_header_starts_a_new_session(
    setup_kernel, credential, policy
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy, header=False)
    worker = await worker_for(store, encryption, credential, viking)
    first = await compact_mid_turn(chat, worker)
    after = await chat.reply([answer("Done")])
    assert after.session != first.session
    await drain(store, worker)
    assert sent(viking) == {first.capture_target: BEFORE}
    # The new session's first request is a tool step, so it is saved from the next message.
    chat.messages.append({"role": "user", "content": "Thanks"})
    later = await chat.reply([answer("Welcome")])
    assert later.session == after.session
    await drain(store, worker)
    assert sent(viking) == {
        first.capture_target: BEFORE,
        later.capture_target: [*AFTER, "Thanks", "Welcome"],
    }


@pytest.mark.parametrize("failure", [False, True])
async def test_mid_turn_client_compaction_during_delivery_loses_nothing(
    setup_kernel, credential, policy, failure
):
    from openviking_gateway.client import VikingError

    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await compact_mid_turn(chat, worker)
    await make_due(store)  # The worker writes the idle tail while the client compacts.

    async def compact():
        await chat.reply(call("chat", 2))

    await while_delivering(worker, viking, compact)
    chat.messages.append(result("chat", 2))
    await chat.reply([answer("Done")])
    chat.messages.append({"role": "user", "content": "Thanks"})
    later = await chat.reply([answer("Welcome")])
    assert later.capture_target == first.capture_target
    if failure:
        write = viking.write

        async def failing(*args):
            viking.write = write
            raise VikingError("openviking_http_503", 503)

        viking.write = failing
        assert await worker.once()
        assert (await chat.state(first))["error"]["reason"] == "openviking_http_503"
    await drain(store, worker)
    steps = [SUMMARY, "Step 1", "call-1", "Step 2", "call-2", "Done", "Thanks", "Welcome"]
    assert sent(viking) == {first.capture_target: [*BEFORE, *steps]}
    assert_once(viking)


@pytest.mark.parametrize("case", ["in_flight", "sent_late", "regenerated", "across_compaction"])
async def test_a_continuation_of_an_abandoned_history_leaves_the_capture_alone(
    setup_kernel, credential, policy, case
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("chat", 0))
    assert await worker.once()
    chat.messages.append(result("chat", 0))
    if case == "regenerated":  # Saved steps the regenerated history lacks.
        await chat.reply(call("chat", 1), cut=True)
        assert await worker.once()
        chat.messages.append(result("chat", 1))
    abandoned = [*chat.messages]
    stale = None if case == "sent_late" else await chat.prepare()
    if case == "across_compaction":
        chat.messages = [{"role": "user", "content": SUMMARY}, *chat.messages[3:]]
        live = await chat.reply([answer("Done")])
        expected = [*TURN, "Step 0", SUMMARY, "Step 0", "call-0", "Done"]
    elif case == "regenerated":
        chat.messages = chat.messages[:3]
        live = await chat.reply([answer("Done differently")])
        expected = [*TURN, "Step 0", "call-0", "Step 1", "Done differently"]
    else:
        chat.messages.append({"role": "user", "content": "Do something else"})
        live = await chat.reply([answer("Other done")])
        expected = [*TURN, "Step 0", "call-0", "Do something else", "Other done"]
    if stale is None:
        live_messages, chat.messages = chat.messages, abandoned
        stale = await chat.prepare()
        chat.messages = live_messages
    before = await store.capture.get(live.scope, live.session)
    response = ResponseCapture("chat", answer("Stale done"), complete=True)
    await chat.kernel.completed(stale, credential, response)
    assert await store.capture.get(live.scope, live.session) == before
    await drain(store, worker)
    assert sent(viking) == {first.capture_target: expected}


async def test_clearing_an_old_tool_result_mid_turn_keeps_the_turn(
    setup_kernel, credential, policy
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    earlier = [{"role": "user", "content": "Question 0"}, *call("chat", 9), result("chat", 9)]
    messages = [*earlier, answer("Answer 0"), {"role": "user", "content": "Fix the build"}]
    chat = Conversation(setup_kernel, credential, policy, messages=messages)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("chat", 0))
    assert await worker.once()
    chat.messages.append(result("chat", 0))
    await chat.reply(call("chat", 1))
    chat.messages[2] = {**chat.messages[2], "content": "[Old tool result content cleared]"}
    chat.messages.append(result("chat", 1))
    after = await chat.reply([answer("Done")])
    assert after.capture_target == first.capture_target
    await drain(store, worker)
    # The session proves none of the rewritten history, so all of it is saved again.
    turn = ["Question 0", "Step 9", "call-9", "Answer 0", "Fix the build", "Step 0", "call-0"]
    assert sent(viking) == {
        first.capture_target: [*turn, "Step 1", *turn, "Step 1", "call-1", "Done"]
    }


async def test_editing_the_last_message_keeps_its_queued_attempt(setup_kernel, credential, policy):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("chat", 0))
    chat.messages = [*chat.messages[:2], {"role": "user", "content": "Fix the tests"}]
    edited = await chat.reply([answer("Fixed")])
    assert edited.capture_target == first.capture_target
    await drain(store, worker)
    # What was queued happened: the attempt the user edited away is saved too.
    expected = [*TURN, "Step 0", "Fix the tests", "Fixed"]
    assert sent(viking) == {first.capture_target: expected}


async def test_a_stale_step_older_than_the_confirmed_list_is_taken_as_a_rewrite(
    setup_kernel, credential, policy
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("chat", 0))
    stale_history = [*chat.messages, result("chat", 0)]
    for turn in range(MAX_CONFIRMED):
        chat.messages.append({"role": "user", "content": f"Turn {turn}"})
        await chat.reply([answer(f"Reply {turn}")])
    live = [*chat.messages]
    chat.messages = stale_history
    stale = await chat.reply([answer("Stale done")])
    chat.messages = [*live, {"role": "user", "content": "Last"}]
    await chat.reply([answer("Bye")])
    assert stale.capture_target == first.capture_target
    await drain(store, worker)
    # The live history replaces the stale reply, and everything after the point the stale
    # step realigned to is saved again: nothing is lost, the later turns are duplicated.
    turns = [x for turn in range(MAX_CONFIRMED) for x in (f"Turn {turn}", f"Reply {turn}")]
    since = ["Step 0", *turns]
    again = ["Fix the build", *since]
    assert sent(viking) == {first.capture_target: [*TURN, *since, *again, "Last", "Bye"]}


async def test_a_capture_saved_before_the_confirmed_list_detects_a_compaction(
    setup_kernel, credential, policy
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await compact_mid_turn(chat, worker)
    # As the released gateway saved it: without the list of confirmed user messages.
    old = await store.capture.get(first.scope, first.session)
    value = {k: v for k, v in old.value.items() if k != "confirmed"}
    assert await store.capture.swap(first.scope, first.session, old, value, None)
    after = await chat.reply([answer("Done")])
    assert after.capture_target == first.capture_target
    await drain(store, worker)
    assert sent(viking) == {first.capture_target: [*BEFORE, *AFTER]}


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("change", ["cleared", "compacted"])
async def test_instructions_before_a_new_message_do_not_resend_the_history(
    setup_kernel, credential, policy, protocol, change
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy, protocol)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call(protocol, 0))
    chat.messages.append(result(protocol, 0))
    await chat.reply([answer("Done")])
    await chat.say("Thanks")
    await drain(store, worker)
    saved = [*TURN, "Step 0", "call-0", "Done"]
    assert sent(viking) == {first.capture_target: saved}
    note = {"role": "developer" if protocol == "responses" else "system", "content": "Note"}
    if change == "cleared":  # A client clears an old tool result.
        index = chat.messages.index(result(protocol, 0))
        key = "output" if protocol == "responses" else "content"
        chat.messages[index] = {**chat.messages[index], key: "[cleared]"}
        # The session saves again what follows the last user message it knows.
        again = ["Step 0", "call-0", "Done", "Thanks"]
    else:
        chat.messages = [{"role": "user", "content": SUMMARY}]
        again = [SUMMARY]
    chat.messages.append(note)  # Codex sends instructions before a user message.
    await chat.say("Next")
    later = await chat.reply([answer("Fine")])
    assert later.capture_target == first.capture_target
    await drain(store, worker)
    assert sent(viking) == {first.capture_target: [*saved, *again, "Next", "Fine"]}


@pytest.mark.parametrize("shape", ["side_question", "alternating"])
async def test_another_history_under_the_same_header_loses_nothing(
    setup_kernel, credential, policy, shape
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    main = Conversation(
        setup_kernel, credential, policy, messages=[{"role": "user", "content": "Main task"}]
    )
    other = Conversation(
        setup_kernel, credential, policy, messages=[{"role": "user", "content": "Other task"}]
    )
    worker = await worker_for(store, encryption, credential, viking)
    first = await main.reply(call("chat", 0))
    main.messages.append(result("chat", 0))
    if shape == "side_question":  # Such as /btw, sent with the main history.
        other.messages = [*main.messages, {"role": "user", "content": "btw what is X?"}]
        await other.reply([answer("X is Y")])
    else:
        await other.reply([answer("Other done")])
    await main.reply(call("chat", 1))
    if shape == "alternating":
        other.messages.append({"role": "user", "content": "Other next"})
        await other.reply([answer("Other next done")])
    main.messages.append(result("chat", 1))
    last = await main.reply([answer("Main done")])
    main.messages.append({"role": "user", "content": "Next"})
    await main.reply([answer("Next done")])
    await drain(store, worker)
    assert last.capture_target == first.capture_target
    rest = ["Step 1", "call-1", "Main done", "Next", "Next done"]
    if shape == "side_question":
        expected = ["Main task", "Step 0", "call-0", "btw what is X?", "X is Y", *rest]
    else:  # The other history's turns come between; the step they interrupted is saved again.
        other = ["Other task", "Other done", "Other next", "Other next done"]
        expected = ["Main task", "Step 0", *other, "Step 0", "call-0", *rest]
    assert sent(viking) == {first.capture_target: expected}


async def test_compaction_before_delivery_keeps_the_confirmed_turn(
    setup_kernel, credential, policy
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply([answer("Done")])
    await chat.say("Thanks")
    chat.messages = [{"role": "user", "content": SUMMARY}, {"role": "user", "content": "Go on"}]
    after = await chat.reply([answer("Went on")])
    await drain(store, worker)
    assert after.capture_target == first.capture_target
    assert sent(viking) == {first.capture_target: [*TURN, "Done", SUMMARY, "Go on", "Went on"]}


@pytest.mark.parametrize(
    "main_request", ["step_late", "answer_late", "failed", "regenerate_failed", "regenerate_late"]
)
async def test_a_user_message_never_queued_is_saved_after_another_history(
    setup_kernel, credential, policy, main_request
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    main = Conversation(
        setup_kernel, credential, policy, messages=[{"role": "user", "content": "Main task"}]
    )
    other = Conversation(
        setup_kernel, credential, policy, messages=[{"role": "user", "content": "Other task"}]
    )
    worker = await worker_for(store, encryption, credential, viking)
    if main_request.startswith("regenerate"):
        await main.reply([answer("Answer 1")])
        main.messages.pop()  # The user regenerates the answer.
    pending = await main.prepare()  # Its reply comes after the other history's turn, or never.
    await other.reply([answer("Other done")])
    late = {"step_late": call("chat", 0), "answer_late": [answer("Main done")]}
    late["regenerate_late"] = [answer("Main done")]
    if main_request in late:
        response = ResponseCapture("chat", late[main_request][-1], complete=True)
        await main.kernel.completed(pending, credential, response)
        main.messages.extend(late[main_request])
    if main_request == "step_late":
        main.messages.append(result("chat", 0))
    if main_request not in ("answer_late", "regenerate_late"):  # A tool step, or a retry.
        await main.reply([answer("Main done")])
    main.messages.append({"role": "user", "content": "Next"})
    await main.reply([answer("Next done")])
    await drain(store, worker)
    steps = ["Step 0", "call-0"] if main_request == "step_late" else []
    mine = ["Main task", *steps, "Main done", "Next", "Next done"]
    assert list(sent(viking).values()) == [["Other task", "Other done", *mine]]


@pytest.mark.parametrize("change", ["compacted_keeping_a_step", "compacted", "edited"])
async def test_a_realigned_turn_written_while_a_reply_is_queued_is_written_once(
    setup_kernel, credential, policy, change
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("chat", 0))
    assert await worker.once()  # Question 0 and Answer 0 are saved.
    chat.messages.append(result("chat", 0))
    await chat.reply(call("chat", 1))
    if change == "compacted_keeping_a_step":
        chat.messages = [{"role": "user", "content": SUMMARY}, *call("chat", 1), result("chat", 1)]
    elif change == "compacted":
        chat.messages = [{"role": "user", "content": SUMMARY}]
    else:
        chat.messages = [*chat.messages[:2], {"role": "user", "content": "Fix the tests"}]
    request = await chat.prepare()  # Realigns: the queued turn is due at once.

    async def reply():
        response = ResponseCapture("chat", answer("Done"), complete=True)
        await chat.kernel.completed(request, credential, response)

    await while_delivering(worker, viking, reply)
    await drain(store, worker)
    assert_once(viking)
    assert request.capture_target == first.capture_target


async def test_tool_steps_do_not_crowd_the_queued_user_messages_out(
    setup_kernel, credential, policy
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy)
    first = await chat.reply(call("chat", 0))
    stale_history = [*chat.messages, result("chat", 0)]
    for turn in range(1, MAX_CONFIRMED):  # Each turn queues its user message twice.
        chat.messages.append({"role": "user", "content": f"Turn {turn}"})
        await chat.reply(call("chat", turn))
        chat.messages.append(result("chat", turn))
        await chat.reply([answer(f"Reply {turn}")])
    before = await store.capture.get(first.scope, first.session)
    chat.messages = stale_history
    stale = await chat.reply([answer("Stale done")])
    assert stale.kind == "continuation"
    assert await store.capture.get(first.scope, first.session) == before


COMPACT = "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n\nYour task is to summarize"


@pytest.mark.parametrize("header", [True, False])
async def test_a_compaction_request_queues_the_tool_step_it_alone_carries(
    setup_kernel, credential, policy, header
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy, "anthropic", header=header)
    worker = await worker_for(store, encryption, credential, viking)
    first = await chat.reply(call("anthropic", 0))
    chat.messages.append(result("anthropic", 0))
    await chat.reply(call("anthropic", 1))
    # Step 1 ran; the client compacts before sending its result anywhere else.
    last = result("anthropic", 1)
    chat.messages.append({**last, "content": [*last["content"], {"type": "text", "text": COMPACT}]})
    side = await chat.prepare()
    assert side.kind == "auxiliary" and side.session == first.session
    response = ResponseCapture(
        "anthropic", answer("<summary>Steps so far</summary>"), complete=True
    )
    await chat.kernel.completed(side, credential, response)
    await drain(store, worker)
    # The step is saved once; the instruction and the summary are not.
    assert sent(viking) == {first.capture_target: [*TURN, "Step 0", "call-0", "Step 1", "call-1"]}


@pytest.mark.parametrize("request_text", ["title", "new_message", "marked_with_a_note"])
async def test_other_side_requests_queue_nothing(setup_kernel, credential, policy, request_text):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy, "anthropic")
    first = await chat.reply(call("anthropic", 0))
    chat.messages.append(result("anthropic", 0))
    before = await store.capture.get(first.scope, first.session)
    live = chat.messages
    if request_text == "title":
        chat.messages = [
            {
                "role": "user",
                "content": "Generate a short kebab-case name (2-4 words) that captures the main"
                " topic of this conversation. Use lowercase words separated by hyphens.",
            }
        ]
    elif request_text == "new_message":  # The instruction as a message of its own.
        chat.messages = [*live, {"role": "user", "content": COMPACT}]
    else:  # Marked a side request by its header; the text after the result is not one.
        chat.headers = {**chat.headers, "x-claude-code-request-class": "auxiliary"}
        note = {"type": "text", "text": "also check f5 please"}
        chat.messages = [*live[:-1], {**live[-1], "content": [*live[-1]["content"], note]}]
    side = await chat.prepare()
    assert side.kind == "auxiliary"
    assert await store.capture.get(first.scope, first.session) == before


async def test_a_compaction_request_never_replaces_what_was_queued(
    setup_kernel, credential, policy
):
    _, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1000000)
    chat = Conversation(setup_kernel, credential, policy, "anthropic")
    first = await chat.reply(call("anthropic", 0))
    chat.messages.append(result("anthropic", 0))
    await chat.reply([answer("Done")])
    # Compacting after the turn ended: the request carries the turn without its answer.
    *turn, last, _ = chat.messages
    appended = {**last, "content": [*last["content"], {"type": "text", "text": COMPACT}]}
    chat.messages = [*turn, appended]
    before = await store.capture.get(first.scope, first.session)
    assert (await chat.prepare()).kind == "auxiliary"
    assert await store.capture.get(first.scope, first.session) == before
