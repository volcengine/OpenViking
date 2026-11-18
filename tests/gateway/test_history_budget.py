"""session_history results in one context window stay within a share of the window."""

import orjson
import pytest
from aiohttp import web
from conftest import HISTORY_TOOL, MCP_TOOLS
from test_app import enable_tools
from test_tools import gateway_call
from test_windows import FIELDS, PATHS, client_body, model_reply, reply, visible_messages

from openviking_gateway.blocks import token_estimate
from openviking_gateway.records import RecordKind as K
from openviking_gateway.tool_executor import ToolExecutor, history_tokens
from openviking_gateway.tool_protocols import tool_protocol
from openviking_gateway.windows import NEW_CONTEXT

CATALOG = [*MCP_TOOLS, HISTORY_TOOL]
NAME = "openviking_session_history"
# A session the model names gets no note, so results are exactly what OpenViking returned.
SEARCH = {"action": "search", "query": "blue", "session": "s"}


def hidden_round(protocol, name, identifier, content):
    """One stored hidden round: the call as the model sent it and its result."""
    if protocol == "chat":
        function = {"name": name, "arguments": "{}"}
        call = {"id": identifier, "type": "function", "function": function}
        output = {"role": "assistant", "content": None, "tool_calls": [call]}
    elif protocol == "anthropic":
        block = {"type": "tool_use", "id": identifier, "name": name, "input": {}}
        output = {"role": "assistant", "content": [block]}
    else:
        output = {"type": "function_call", "call_id": identifier, "name": name, "arguments": "{}"}
    results = tool_protocol(protocol)({}).results(
        [{"role": "tool", "tool_call_id": identifier, "content": content}]
    )
    return [output, *results]


def recorder(viking, text):
    calls = []

    async def mcp(method, key, params, **kwargs):
        calls.append(params)
        return {"content": [{"type": "text", "text": text}]}

    viking.mcp = mcp
    return calls


async def history_request(kernel, credential, policy, **changes):
    policy.update(gateway_tools=True, compaction=False, **changes)
    kernel.viking.catalog = CATALOG
    messages = [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": "What did we decide?"},
    ]
    return await kernel.prepare(
        {"messages": messages},
        "chat",
        {"x-openviking-session": "budget"},
        credential,
        {"id": "u"},
        policy,
    )


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_history_tokens_count_raw_history_results_only(protocol):
    quoted = "<system-reminder>" + "q" * 400 + "</system-reminder>"
    messages = [
        *hidden_round(protocol, NAME, "h-1", "a" * 400),
        *hidden_round(protocol, "openviking_find", "f-1", "b" * 4000),
        *hidden_round(protocol, NAME, "h-2", quoted),
    ]
    assert history_tokens(messages) == 100 + token_estimate(quoted)
    assert token_estimate(quoted) > 100
    assert history_tokens([]) == 0


async def test_budget_counts_the_window_replayed_and_this_request(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy, context_window=100_000)
    assert prepared.context_window == 100_000
    calls = recorder(viking, "x" * 40000)
    # 20,000 of the 100,000-token window (0.2) may be history results; 10,000 are replayed.
    hidden = {"messages": hidden_round("chat", NAME, "h-1", "y" * 40000), "visible_count": 1}
    prepared.records[K.HIDDEN, prepared.capture_chain[1]] = hidden
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    assert (await run.execute(gateway_call("session_history", SEARCH)))["content"] == "x" * 40000
    refused = await run.execute(gateway_call("session_history", SEARCH, "g-2"))
    assert len(calls) == 1 and not refused["failed"]
    assert refused["content"] == (
        "The session history budget for this context window is used up: earlier results (about "
        "20000 tokens) are still in context above. Work from them and note the ids you need."
    )
    # Other tools are not limited.
    await run.execute(gateway_call("find", {"query": "blue"}, "g-3"))
    assert len(calls) == 2
    # A cut after the replayed round starts a new window with a new budget; neither the
    # replayed round nor this request's earlier results count there.
    prepared.records[K.REPLACEMENT, prepared.capture_chain[1]] = {"source": "window", "text": ""}
    for identifier in ("g-4", "g-5", "g-6"):
        refused = await run.execute(gateway_call("session_history", SEARCH, identifier))
    assert len(calls) == 4 and "(about 20000 tokens)" in refused["content"]


async def test_small_windows_keep_room_for_a_search_and_a_read(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(kernel, credential, policy, context_window=1024)
    # A fifth of 1,024 tokens is less than one result; the 8,000-token floor applies.
    calls = recorder(viking, "x" * 20000)
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    for identifier in ("g-1", "g-2", "g-3"):
        content = (await run.execute(gateway_call("session_history", SEARCH, identifier)))[
            "content"
        ]
    assert len(calls) == 2 and "(about 10000 tokens)" in content


async def test_budget_answer_names_only_the_resets_this_conversation_has(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    prepared = await history_request(
        kernel, credential, policy, context_window=1024, agent_windows=True, recall=False
    )
    prepared.root["policy"]["compaction"] = True
    recorder(viking, "x" * 40000)
    run = ToolExecutor(viking, store, prepared, credential, "", 1024)
    await run.execute(gateway_call("session_history", SEARCH))
    refused = (await run.execute(gateway_call("session_history", SEARCH, "g-2")))["content"]
    assert refused.endswith(
        " More lookups are possible after the next compaction or after "
        f"{NEW_CONTEXT} starts a new window."
    )
    prepared.root["policy"]["compaction"] = False
    refused = (await run.execute(gateway_call("session_history", SEARCH, "g-3")))["content"]
    assert refused.endswith(f" More lookups are possible after {NEW_CONTEXT} starts a new window.")


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_budget_reads_replayed_rounds_of_each_protocol(running_gateway, protocol):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, context_window=2000)
    app.state.test_backend["catalog"] = CATALOG
    mcp, upstream = [], []
    plan = [[(NAME, SEARCH)], [], [(NAME, {**SEARCH, "action": "overview"})], []]

    async def backend(request):
        if request.path == "/mcp":
            mcp.append(await request.json())
            text = "| " + "z" * 40000
            return web.json_response(
                {"id": 1, "result": {"content": [{"type": "text", "text": text}]}}
            )
        if request.path != PATHS[protocol]:
            return None
        upstream.append(await request.json())
        value = model_reply(protocol, len(upstream), plan[len(upstream) - 1])
        return await reply(request, protocol, value, False)

    app.state.test_backend["handler"] = backend
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "budget"}
    body = client_body(protocol, False)
    first = await client.post(PATHS[protocol], headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert len(mcp) == 1
    # The next turn replays the hidden round; its result already uses up the window's share.
    field = FIELDS[protocol]
    body[field] = [
        *body[field],
        *visible_messages(protocol, first, False),
        {"role": "user", "content": "And the other one?"},
    ]
    second = await client.post(PATHS[protocol], headers=headers, json=body)
    assert second.status_code == 200, second.text
    assert len(mcp) == 1 and len(upstream) == 4
    assert "z" * 40000 in orjson.dumps(upstream[2]).decode()
    assert "budget for this context window is used up" in orjson.dumps(upstream[3]).decode()
