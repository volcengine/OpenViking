"""What a saved turn holds, saves at gateway cuts, session lineage and the history hint."""

import asyncio

import pytest
from aiohttp import web
from conftest import MCP_TOOLS, mcp_tool
from test_review_regressions import history

from openviking_gateway.blocks import history_hint
from openviking_gateway.capture import lineage, new_capture, reset_capture
from openviking_gateway.client import VikingError
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

    def __init__(self, setup, credential, policy, protocol="chat", messages=None, peer=None):
        self.kernel, self.store = setup[0], setup[1]
        self.credential, self.policy, self.protocol, self.peer = credential, policy, protocol, peer
        self.messages = messages or [*history(1), {"role": "user", "content": "Fix the build"}]

    async def prepare(self, **kwargs):
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
            peer=self.peer,
            **kwargs,
        )

    async def reply(self, items, cut=False, request=None, usage=None):
        """Serve the history, cutting at its last message if asked, then append the reply.

        A reply of tool calls hands the turn to the client; any other ends it.
        """
        request = request or await self.prepare()
        if cut:
            await self.kernel.capture.save_cut(request, self.credential, request.capture_chain[-1])
        response = ResponseCapture(
            self.protocol,
            items[-1],
            usage=usage,
            complete="call-" not in str(items),
            output_items=items if self.protocol == "responses" else None,
        )
        await self.kernel.completed(request, self.credential, response)
        saved = await self.kernel.save(request, self.credential, response)
        self.messages.extend(items)
        return request, saved

    async def state(self, request):
        return (await self.store.capture.get(request.scope, request.session)).value


def sent(viking):
    """Saved text and tool IDs per OpenViking session, in write order."""
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


@pytest.mark.parametrize("cut", [False, True])
@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_tool_loop_turn_is_saved_once_with_its_tool_pairs_and_peers(
    setup_kernel, credential, policy, protocol, cut
):
    _, _, viking, _ = setup_kernel
    policy.update(recall=False)
    chat = Conversation(setup_kernel, credential, policy, protocol, peer="alice-bot")
    first, saved = await chat.reply(call(protocol, 0))
    assert saved == 0 and not viking.writes
    chat.messages.append(result(protocol, 0))
    _, saved = await chat.reply(call(protocol, 1), cut=cut)
    assert saved == 0
    session = (await chat.state(first))["ov_session"]
    # A cut saves the history up to it at once, the turn's end the rest.
    assert sent(viking) == ({session: [*TURN, "Step 0", "call-0"]} if cut else {})
    chat.messages.append(result(protocol, 1))
    await chat.reply([answer("Done")])
    assert sent(viking) == {session: FULL}
    assert_once(viking)
    messages = [m for batch in viking.writes for m in batch]
    assert [m["peer_id"] for m in messages if m["role"] == "user"] == ["alice-bot"] * 2
    assert all("peer_id" not in m for m in messages if m["role"] != "user")
    tools = [p for m in messages for p in m["parts"] if p["type"] == "tool"]
    assert [(p["tool_name"], p["tool_input"], p["tool_output"]) for p in tools] == [
        ("run", {}, "output 0"),
        ("run", {}, "output 1"),
    ]


async def summarize(prepared, body):
    return {"choices": [{"message": {"content": "Summary"}, "finish_reason": "stop"}]}


@pytest.mark.parametrize("fails", [False, True])
async def test_compaction_inside_a_turn_saves_the_cut_part_or_leaves_it_to_the_turn_end(
    setup_kernel, credential, policy, fails
):
    _, _, viking, _ = setup_kernel
    policy.update(recall=False, context_window=1024)
    chat = Conversation(
        setup_kernel, credential, policy, messages=[{"role": "user", "content": "Fix the build"}]
    )
    usage = {"input_tokens": 1000, "output_tokens": 5}
    first, _ = await chat.reply(call("chat", 0), usage=usage)
    chat.messages.append(result("chat", 0))
    viking.write_failure = VikingError("openviking_http_503", 503) if fails else None
    request = await chat.prepare(summarize=summarize)
    assert request.kind == "continuation" and request.metrics["compaction_applied"]
    session = (await chat.state(first))["ov_session"]
    # A failed save does not fail the request it was made for.
    assert sent(viking) == ({} if fails else {session: ["Fix the build", "Step 0", "call-0"]})
    viking.write_failure = None
    _, saved = await chat.reply([answer("Done")], request=request)
    assert saved == (3 if fails else 1)
    assert sent(viking) == {session: ["Fix the build", "Step 0", "call-0", "Done"]}


@pytest.mark.parametrize("case", ["capture_off", "disabled", "already_saved", "unknown_anchor"])
async def test_save_cut_writes_nothing(setup_kernel, credential, policy, case):
    _, store, viking, _ = setup_kernel
    policy.update(recall=False, capture=case != "capture_off")
    chat = Conversation(setup_kernel, credential, policy)
    await chat.reply(call("chat", 0))
    chat.messages.append(result("chat", 0))
    request = await chat.prepare()
    pipeline = chat.kernel.capture
    anchor = request.capture_chain[-1]
    if case == "disabled":
        request.disabled = True
    elif case == "already_saved":
        await pipeline.save_cut(request, credential, anchor)
        assert viking.writes
    elif case == "unknown_anchor":
        anchor = "elsewhere"
    writes = len(viking.writes)
    before = await store.capture.get(request.scope, request.session)
    await pipeline.save_cut(request, credential, anchor)
    assert await store.capture.get(request.scope, request.session) == before
    assert len(viking.writes) == writes


async def test_turn_with_a_system_message_is_not_written_again(setup_kernel, credential, policy):
    _, _, viking, _ = setup_kernel
    policy.update(recall=False)
    reminder = {"role": "system", "content": "Reminder"}
    chat = Conversation(
        setup_kernel,
        credential,
        policy,
        messages=[*history(1), reminder, {"role": "user", "content": "next"}],
    )
    first, _ = await chat.reply([answer("Answer next")])
    chat.messages.append({"role": "user", "content": "more"})
    await chat.reply([answer("Answer more")])
    session = (await chat.state(first))["ov_session"]
    assert sent(viking) == {
        session: ["Question 0", "Answer 0", "next", "Answer next", "more", "Answer more"]
    }
    assert_once(viking)


async def test_rotations_record_the_sessions_that_received_messages(
    setup_kernel, credential, policy
):
    assert lineage({}) == []
    fresh = new_capture()
    assert fresh["previous"] == [] and lineage(fresh) == []
    used = {**fresh, "delivered": "anchor", "previous": ["a", "b", "c", "d", "e"]}
    assert lineage(used) == [fresh["ov_session"], "a", "b", "c", "d", "e"]
    rotated = new_capture("manual_reset", used)
    assert rotated["previous"] == [fresh["ov_session"], "a", "b", "c", "d"]
    # A session that never received a message is not worth searching.
    assert new_capture("manual_reset", rotated)["previous"] == rotated["previous"]
    policy.update(recall=False)
    chat = Conversation(setup_kernel, credential, policy)
    first, _ = await chat.reply([answer("Answer next")])
    target = (await chat.state(first))["ov_session"]
    route = await reset_capture(chat.store.capture, first.scope, first.session, "manual_reset")
    assert route["previous"] == [target]


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


@pytest.mark.parametrize("fails", [False, True])
async def test_request_record_says_what_the_turn_saved(running_gateway, fails):
    app, client, _, issued, _, writes = running_gateway
    seen = []

    async def backend(request):
        if request.path.endswith("/messages/batch"):
            seen.append(request.headers.get("X-Request-ID"))
            if fails:
                return web.json_response({}, status=503)

    app.state.test_backend["handler"] = backend
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + issued["key"], "X-OpenViking-Session": "saved"},
        json={"model": "model", "messages": [{"role": "user", "content": "Remember this"}]},
    )
    assert response.status_code == 200, response.text
    for _ in range(100):
        logs = await app.state.management.logs("tenant")
        if logs:
            break
        await asyncio.sleep(0.01)
    [record] = logs
    # The write carries the ID of the request whose turn it saves.
    assert seen == [response.headers["x-openviking-request-id"]]
    if fails:
        assert (record["capture_status"], record["capture_reason"]) == (
            "failed",
            "openviking_http_503",
        )
        assert "capture_messages" not in record and not writes
    else:
        assert record["capture_status"] == "active" and record["capture_messages"] == 2
        assert record["openviking_session"].startswith("gateway-")
        assert "Remember this" in str(writes)
