"""session_history through the gateway: offer, default session, nothing-saved answer, note."""

import orjson
import pytest
from conftest import HISTORY_TOOL, MCP_TOOLS, update_capture
from test_tools import gateway_call
from test_windows import NOTES, windows_request

from openviking_gateway.capture_store import Document
from openviking_gateway.records import RecordKind as K
from openviking_gateway.tool_catalog import TOOL_OVERRIDES, notice_head, select_tools
from openviking_gateway.tool_executor import NOTHING_SAVED, ToolExecutor
from openviking_gateway.windows import NEW_CONTEXT

CATALOG = [*MCP_TOOLS, HISTORY_TOOL]
NAME = "openviking_session_history"
SEARCH = {"action": "search", "query": "blue"}
DECIDE = [
    {"role": "user", "content": "First task"},
    {"role": "assistant", "content": "Done."},
    {"role": "user", "content": "Second task"},
    {"role": "assistant", "content": "Done too."},
    {"role": "user", "content": "What did we decide?"},
]


def names(tools):
    return [t["function"]["name"] for t in tools]


async def history_request(kernel, credential, policy, messages=DECIDE, **changes):
    policy.update(gateway_tools=True, **changes)
    kernel.viking.catalog = CATALOG
    return await kernel.prepare(
        {"messages": messages},
        "chat",
        {"x-openviking-session": "history"},
        credential,
        {"id": "u"},
        policy,
    )


def recorder(viking, text="| found"):
    calls = []

    async def mcp(method, key, params, **kwargs):
        calls.append(params)
        return {"content": [{"type": "text", "text": text}]}

    viking.mcp = mcp
    return calls


def lookup(arguments=SEARCH, identifier="g-1"):
    return gateway_call("session_history", arguments, identifier)


async def delivered(store, prepared, index):
    """Mark the history up to ``index`` as saved, as the worker would."""
    value = (await store.capture.get(prepared.scope, prepared.session)).value
    anchor = prepared.capture_chain[index]
    pending = [t for t in value["pending"] if prepared.capture_chain.index(t["anchor"]) > index]
    return await update_capture(store, prepared, delivered=anchor, pending=pending)


# (1) The tool is offered from the MCP catalog and session defaults to this conversation.


def test_history_tool_is_offered_with_a_suffix_unless_disabled_or_not_captured():
    offered = select_tools(CATALOG, {})
    assert names(offered) == [*names(select_tools(MCP_TOOLS, {})), NAME]
    function = offered[-1]["function"]
    assert function["description"] == (
        "OpenViking session_history session defaults to this conversation's saved sessions; "
        "leave it out unless you need another session."
    )
    assert function["parameters"] == HISTORY_TOOL["inputSchema"]
    assert function["parameters"] is not HISTORY_TOOL["inputSchema"]
    assert TOOL_OVERRIDES["session_history"]["notice"] == ("query", "id")
    # Disabled, or without capture, the frozen tools are the same bytes as without the tool.
    for policy in (
        {"disabled_tools": ["session_history"]},
        {"capture": False},
        {"agent_windows": True, "disabled_tools": ["session_history"]},
    ):
        assert orjson.dumps(select_tools(CATALOG, policy)) == orjson.dumps(
            select_tools(MCP_TOOLS, policy)
        )


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("change", [{"disabled_tools": ["session_history"]}, {"capture": False}])
async def test_upstream_body_is_unchanged_when_the_tool_is_not_offered(
    setup_kernel, credential, policy, protocol, change
):
    kernel, _, viking, _ = setup_kernel
    policy.update(gateway_tools=True, agent_windows=True, **change)
    bodies = []
    for session, catalog in (("with", CATALOG), ("without", MCP_TOOLS)):
        viking.catalog = catalog
        field = "input" if protocol == "responses" else "messages"
        body = {"model": "model", field: [{"role": "user", "content": "What did we decide?"}]}
        if protocol == "responses":
            body["store"] = False
        header = {"x-openviking-session": session}
        prepared = await kernel.prepare(body, protocol, header, credential, {"id": "u"}, policy)
        assert prepared.tools_active
        bodies.append(orjson.dumps(prepared.body))
    assert bodies[0] == bodies[1]


async def test_history_tool_is_not_offered_without_capture(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy, capture=False)
    assert prepared.tools_active and NAME not in names(prepared.body["tools"])


async def test_session_defaults_to_the_saved_sessions_of_this_conversation(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy)
    current = prepared.capture.value["ov_session"]
    await delivered(store, prepared, 3)
    await update_capture(store, prepared, previous=["s-1", "s-2"])
    calls = recorder(viking)
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    await run.execute(lookup())
    assert calls[-1]["name"] == "session_history"
    assert calls[-1]["arguments"] == {**SEARCH, "session": f"{current},s-1,s-2"}
    # Blank counts as left out.
    for index, blank in enumerate(["", "  "]):
        await run.execute(lookup({**SEARCH, "session": blank}, f"blank-{index}"))
        assert calls[-1]["arguments"]["session"] == f"{current},s-1,s-2"
    # A session the model names is passed through unchanged.
    await run.execute(lookup({**SEARCH, "session": "other"}, "g-3"))
    assert calls[-1]["arguments"] == {**SEARCH, "session": "other"}
    # A current session that holds nothing yet is left out; the earlier ones remain.
    await update_capture(store, prepared, delivered="")
    await run.execute(lookup(SEARCH, "g-4"))
    assert calls[-1]["arguments"]["session"] == "s-1,s-2"
    assert notice_head(lookup()).endswith('session_history: "blue"')


async def test_lookup_before_anything_is_saved_answers_without_openviking(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy)
    calls = recorder(viking)
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    result = await run.execute(lookup())
    assert calls == [] and not result["failed"]
    assert result["content"] == (
        "Nothing from this conversation is saved in OpenViking yet, so there is nothing to look "
        "up; work from what is in your context. Its 5 messages so far are waiting to be saved."
    )
    assert result["content"].startswith(NOTHING_SAVED)
    # The answer is the call's receipt: a retry of it returns the same bytes.
    again = await ToolExecutor(viking, store, prepared, credential, "", 1024).execute(lookup())
    assert again == result and calls == []
    # A session the model names is still looked up.
    await run.execute(lookup({**SEARCH, "session": "other"}, "g-2"))
    assert calls[-1]["arguments"]["session"] == "other"


async def test_schema_without_a_session_property_is_not_filled(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    schema = {"type": "object", "properties": {"action": {"type": "string"}}}
    prepared = await history_request(kernel, credential, policy)
    prepared.root["tools"][-1]["function"]["parameters"] = schema
    calls = recorder(viking)

    async def unread(*args):
        raise AssertionError("no capture read without a session to fill")

    store.capture.get = unread
    result = await ToolExecutor(viking, store, prepared, credential, "", 1024).execute(
        lookup({"action": "overview"})
    )
    assert calls[-1]["arguments"] == {"action": "overview"} and result["content"] == "| found"


async def test_store_read_failure_falls_back_to_the_request_capture(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy)
    current = prepared.capture.value["ov_session"]
    prepared.capture = Document({**prepared.capture.value, "delivered": prepared.capture_chain[3]})

    async def broken(*args):
        raise RuntimeError("store unavailable")

    store.capture.get = broken
    calls = recorder(viking)
    result = await ToolExecutor(viking, store, prepared, credential, "", 1024).execute(lookup())
    assert not result["failed"] and calls[-1]["arguments"]["session"] == current
    assert result["content"].startswith("| found")


# (2) A lookup of this conversation says how many messages a cut removed before they were saved.


def note(count):
    last, verb = ("last message", "is") if count == 1 else (f"last {count} messages", "are")
    return (
        f"\nGateway note: the {last} before your current context window {verb} not saved yet "
        "and may be missing from this result."
    )


def cut(prepared, index):
    """A compaction summary in force that replaced the history up to ``index``."""
    anchor = prepared.capture_chain[index]
    prepared.records[K.REPLACEMENT, anchor] = {"source": "compaction", "text": "", "tokens": 0}


async def test_no_note_while_every_unsaved_message_is_in_context(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy)
    recorder(viking)
    value = await delivered(store, prepared, 1)
    assert sum(len(t["messages"]) for t in value["pending"]) == 2
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    assert (await run.execute(lookup()))["content"] == "| found"


async def test_note_counts_unsaved_messages_up_to_the_cut(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy)
    recorder(viking)
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    await delivered(store, prepared, 1)
    # The second turn is cut but not saved; the current message is in context.
    cut(prepared, 3)
    assert (await run.execute(lookup()))["content"] == "| found" + note(2)
    prepared.records.pop((K.REPLACEMENT, prepared.capture_chain[3]))
    cut(prepared, 2)
    assert (await run.execute(lookup(SEARCH, "g-2")))["content"] == "| found" + note(1)
    # Not when the model chose the sessions, nor for other tools.
    assert (await run.execute(lookup({**SEARCH, "session": "x"}, "g-3")))["content"] == "| found"
    content = (await run.execute(gateway_call("find", {"query": "blue"}, "g-4")))["content"]
    assert content == "| found"


async def test_no_note_when_the_cut_part_is_saved(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy)
    recorder(viking)
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    await delivered(store, prepared, 3)
    cut(prepared, 3)
    assert (await run.execute(lookup()))["content"] == "| found"
    prepared.records.pop((K.REPLACEMENT, prepared.capture_chain[3]))
    cut(prepared, 1)
    assert (await run.execute(lookup(SEARCH, "g-2")))["content"] == "| found"


async def test_no_note_when_the_saved_part_is_unknown(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy)
    recorder(viking)
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    cut(prepared, 3)
    # The saved anchor follows another branch, or a new session took over from earlier ones.
    await update_capture(store, prepared, delivered="other-branch")
    assert (await run.execute(lookup()))["content"] == "| found"
    await update_capture(store, prepared, delivered="", previous=["s-1"])
    assert (await run.execute(lookup(SEARCH, "g-2")))["content"] == "| found"


async def test_note_counts_the_part_a_new_window_just_cut(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    viking.catalog = CATALOG
    prepared = await windows_request(kernel, credential, policy, list(DECIDE))
    await delivered(store, prepared, 1)
    recorder(viking)
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    assert (await run.execute(lookup()))["content"] == "| found"
    started = await run.execute(gateway_call(NEW_CONTEXT.removeprefix("openviking_"), NOTES))
    # The tool loop switches to the new window; persist would queue the cut part later.
    prepared.records[K.REPLACEMENT, started["cut"][0]] = started["cut"][1]
    assert started["cut"][0] == prepared.capture_chain[4]
    assert (await run.execute(lookup(SEARCH, "g-2")))["content"] == "| found" + note(3)


async def test_note_survives_truncation(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy, tool_result_bytes=1024)
    await delivered(store, prepared, 1)
    cut(prepared, 3)
    recorder(viking, "| " + "x" * 5000)
    content = (await ToolExecutor(viking, store, prepared, credential, "", 1024).execute(lookup()))[
        "content"
    ]
    assert content.endswith("\n[Tool result truncated.]" + note(2))
