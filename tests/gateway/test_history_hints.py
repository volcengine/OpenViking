"""Hints and the summary instruction with and without the session_history tool."""

import pytest
from conftest import HISTORY_TOOL, MCP_TOOLS
from test_compaction import FIELD, Summarizer, answered, prepare
from test_review_regressions import worker_for
from test_windows import NOTES, windows_request

from openviking_gateway.capture_store import Document
from openviking_gateway.compaction import opening_block
from openviking_gateway.protocols import text_content
from openviking_gateway.tool_catalog import select_tools
from openviking_gateway.tool_protocols import ResponseCapture
from openviking_gateway.windows import new_context

CATALOG = [*MCP_TOOLS, HISTORY_TOOL]


def old_hint(*sessions):
    """Today's text for servers without session_history, whose read takes no offset."""
    return "\n".join(
        [
            "Earlier parts of this conversation are saved in OpenViking, newest session first:",
            *(f"- viking://user/alice/sessions/{session}/" for session in sessions),
            "In each session, messages.jsonl holds the latest messages and "
            "history/archive_NNN/messages.jsonl the older ones, one JSON message per line. "
            "To find something, call openviking_grep on a session URI with a specific "
            "pattern; it lists each matching line with its file and line number. "
            "openviking_read returns a whole file, so read one only when the matching lines "
            "are not enough.",
        ]
    )


async def two_turns(kernel, protocol, credential, policy, session="compact", **kwargs):
    """A second user message whose context is over the threshold, so it compacts."""
    extra = {"extra": {"store": False}} if protocol == "responses" else {}
    reply = {"role": "assistant", "content": "Blue."}
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, protocol, messages, credential, policy, session, **extra)
    await answered(kernel, first, credential, reply)
    messages += [reply, {"role": "user", "content": "Next?"}]
    return await prepare(kernel, protocol, messages, credential, policy, session, **extra, **kwargs)


# (3) The hints after a cut and at session start point to the new tool when it is frozen.


def test_hints_use_the_history_tool_only_when_it_is_frozen():
    from openviking_gateway.blocks import LOOKUP, history_hint
    from openviking_gateway.windows import HISTORY_SEARCHABLE, SEARCHABLE

    offered, plain = select_tools(CATALOG, {}), select_tools(MCP_TOOLS, {})
    disabled = select_tools(CATALOG, {"disabled_tools": ["session_history"]})
    for place in ("start", "summary", "window"):
        assert history_hint("alice", ["s"], offered, True, place) == LOOKUP[place]
        assert history_hint("alice", ["s"], plain, True, place) == old_hint("s")
        assert history_hint("alice", ["s"], disabled, True, place) == old_hint("s")
        assert history_hint("alice", ["s"], offered, False, place) == ""
        assert history_hint("alice", [], offered, True, place) == ""
    # The new tool does not need grep or read.
    alone = select_tools([HISTORY_TOOL], {})
    assert history_hint("alice", ["s"], alone, True, "summary") == LOOKUP["summary"]
    assert LOOKUP["summary"].startswith(
        "The full conversation before this summary is saved in OpenViking."
    )
    assert "put its id in your notes" in LOOKUP["window"]
    for text in LOOKUP.values():
        assert 'openviking_session_history(action="search", query="<exact words' in text
    windows = {"agent_windows": True}
    assert select_tools(CATALOG, windows)[-2]["function"]["description"].endswith(
        HISTORY_SEARCHABLE
    )
    assert (
        SEARCHABLE == " Earlier windows stay searchable with openviking_grep and openviking_read."
    )
    assert select_tools(MCP_TOOLS, windows)[-2]["function"]["description"].endswith(SEARCHABLE)
    off = {**windows, "disabled_tools": ["session_history"]}
    assert select_tools(CATALOG, off)[-2]["function"]["description"].endswith(SEARCHABLE)


@pytest.mark.parametrize("offered", [True, False])
async def test_compaction_footer_points_to_the_history_tool(
    setup_kernel, credential, policy, offered
):
    from openviking_gateway.blocks import LOOKUP

    kernel, _, viking, _ = setup_kernel
    policy.update(context_window=1024, gateway_tools=True)
    viking.catalog = CATALOG if offered else MCP_TOOLS
    second = await two_turns(kernel, "chat", credential, policy, summarize=Summarizer("chat"))
    summary_block = second.body["messages"][0]["content"].split("</openviking-context>")[0]
    hint = LOOKUP["summary"] if offered else old_hint(second.capture.value["ov_session"])
    assert summary_block.endswith("\n\n" + hint + "\n")


@pytest.mark.parametrize("offered", [True, False])
async def test_window_header_points_to_the_history_tool(setup_kernel, credential, policy, offered):
    from openviking_gateway.blocks import LOOKUP

    kernel, _, viking, _ = setup_kernel
    policy.update(recall=False)
    viking.catalog = CATALOG if offered else MCP_TOOLS
    messages = [{"role": "user", "content": "First task"}]
    prepared = await windows_request(kernel, credential, policy, messages)
    prepared.capture = Document({"ov_session": "s-now", "delivered": "x", "previous": []})
    header = (await new_context(prepared, credential, NOTES))["cut"][1]["text"]
    if offered:
        assert LOOKUP["window"] in header and "call openviking_grep" not in header
    else:
        assert old_hint("s-now") in header and "openviking_session_history" not in header


async def test_session_start_after_a_client_compaction_points_to_the_history_tool(
    setup_kernel, credential, policy
):
    from openviking_gateway.blocks import LOOKUP

    kernel, store, viking, encryption = setup_kernel
    policy.update(gateway_tools=True)
    viking.catalog = CATALOG
    worker = await worker_for(store, encryption, credential, viking)
    reply = {"role": "assistant", "content": "Blue."}
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, "chat", messages, credential, policy)
    await kernel.completed(first, credential, ResponseCapture("chat", reply, complete=True))
    messages += [reply, {"role": "user", "content": "And staging?"}]
    await prepare(kernel, "chat", messages, credential, policy)
    assert await worker.once()
    fresh = await prepare(
        kernel, "chat", [{"role": "user", "content": "Summary. Continue."}], credential, policy
    )
    assert fresh.capture_target != first.capture_target
    assert LOOKUP["start"] in opening_block(fresh.records, fresh.chain)


# (4) With the tool offered, the summary ends with strings to search for later.


def test_summary_instruction_adds_the_lookup_section_only_when_asked():
    from openviking_gateway.compaction import (
        INSTRUCTION,
        LOOKUP_STRINGS,
        UNFINISHED,
        summary_instruction,
    )

    assert summary_instruction(100, False) == INSTRUCTION.format(tokens=100)
    assert summary_instruction(100, True) == INSTRUCTION.format(tokens=100) + "\n" + UNFINISHED
    assert summary_instruction(100, False, True) == (
        INSTRUCTION.format(tokens=100) + "\n" + LOOKUP_STRINGS
    )
    assert summary_instruction(100, True, True).endswith(LOOKUP_STRINGS + "\n" + UNFINISHED)
    assert LOOKUP_STRINGS.startswith('End the summary with a short section "Strings to look up')


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("offered", [True, False])
async def test_summary_request_asks_for_lookup_strings_when_the_tool_is_offered(
    setup_kernel, credential, policy, protocol, offered
):
    from openviking_gateway.compaction import INSTRUCTION, LOOKUP_STRINGS

    kernel, _, viking, _ = setup_kernel
    policy.update(context_window=1024, gateway_tools=True)
    viking.catalog = CATALOG if offered else MCP_TOOLS
    summarize = Summarizer(protocol)
    await two_turns(kernel, protocol, credential, policy, summarize=summarize)
    [request] = summarize.requests
    instruction = INSTRUCTION.format(tokens=policy["summary_max_tokens"])
    expected = instruction + "\n" + LOOKUP_STRINGS if offered else instruction
    assert text_content(request[FIELD[protocol]][-1]) == expected


async def test_summary_instruction_is_unchanged_when_capture_is_off(
    setup_kernel, credential, policy
):
    from openviking_gateway.compaction import INSTRUCTION
    from openviking_gateway.storage import digest

    kernel, store, viking, _ = setup_kernel
    policy.update(context_window=1024, gateway_tools=True)
    viking.catalog = CATALOG
    body = {"model": "model", "messages": [{"role": "user", "content": "Hi"}]}
    initial = await kernel.prepare(body, "chat", {}, credential, {"id": "u"}, policy)
    # A root whose frozen tools include session_history while its policy saves nothing.
    saved = {key: value for key, value in initial.root.items() if key != "tool_skip_reason"}
    saved.update(tools=select_tools(CATALOG, {}), policy={**policy, "capture": False})
    await store.replay.put(initial.scope, digest("saved"), "root", "", saved)
    summarize = Summarizer("chat")
    second = await two_turns(kernel, "chat", credential, policy, "saved", summarize=summarize)
    assert "openviking_session_history" in [t["function"]["name"] for t in second.root["tools"]]
    [request] = summarize.requests
    instruction = INSTRUCTION.format(tokens=policy["summary_max_tokens"])
    assert text_content(request["messages"][-1]) == instruction
