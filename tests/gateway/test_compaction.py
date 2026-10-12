"""Model-written compaction: trigger, cuts, summary requests and their replay."""

import asyncio
import copy

import orjson
import pytest
from aiohttp import web
from conftest import make_due, replay_records
from test_review_regressions import worker_for

from openviking_gateway.blocks import block, history_hint
from openviking_gateway.client import VikingClient
from openviking_gateway.compaction import (
    HEADER,
    IN_PROGRESS,
    INSTRUCTION,
    MEDIA_TOKENS,
    UNFINISHED,
    estimate,
    opening_block,
)
from openviking_gateway.protocols import text_content, usage_of
from openviking_gateway.state_store import get_state
from openviking_gateway.tool_protocols import ResponseCapture, hidden_chain, tool_protocol
from openviking_gateway.tool_protocols.common import SUMMARY_HEADROOM, SummaryError

FIELD = {"chat": "messages", "anthropic": "messages", "responses": "input"}
INSTRUCTION_START = INSTRUCTION[:40]


def summary_response(protocol, text="Deployed to blue; verify next."):
    if protocol == "chat":
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    if protocol == "anthropic":
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
    }


class Summarizer:
    def __init__(self, protocol, response=None):
        self.protocol, self.response, self.requests = protocol, response, []

    async def __call__(self, prepared, body):
        self.requests.append(body)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response or summary_response(self.protocol)


async def prepare(kernel, protocol, messages, credential, policy, session="compact", **kwargs):
    return await kernel.prepare(
        {"model": "model", FIELD[protocol]: messages, **kwargs.pop("extra", {})},
        protocol,
        {"x-openviking-session": session},
        credential,
        {"id": "upstream"},
        policy,
        **kwargs,
    )


async def answered(kernel, request, credential, reply, tokens=1000):
    """Record a completed reply whose usage makes the next request's context large."""
    response = ResponseCapture(
        request.protocol, reply, usage={"input_tokens": tokens, "output_tokens": 5}, complete=True
    )
    await kernel.completed(request, credential, response)


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_user_cut_replaces_the_history_before_the_latest_user_message(
    setup_kernel, credential, policy, protocol
):
    kernel, store, _, _ = setup_kernel
    policy.update(context_window=1024)
    field = FIELD[protocol]
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, protocol, messages, credential, policy)
    reply = {"role": "assistant", "content": "Use the blue cluster."}
    await answered(kernel, first, credential, reply)
    messages += [reply, {"role": "user", "content": "What next?"}]
    summarize = Summarizer(protocol)
    second = await prepare(kernel, protocol, messages, credential, policy, summarize=summarize)
    # The summary request holds exactly the replaced span and the instruction.
    [request] = summarize.requests
    assert request["stream"] is False
    assert request[field][:2] == first.body[field][:1] + [reply]
    assert text_content(request[field][2]).startswith(INSTRUCTION_START)
    assert UNFINISHED not in text_content(request[field][2])
    opening = opening_block(second.records, second.chain)
    assert opening.startswith('<openviking-context source="gateway-session-start">')
    text = (
        block("gateway-compaction", HEADER + "\n\nDeployed to blue; verify next.")
        + "\n\n"
        + opening
    )
    assert second.body[field][0] == {"role": "user", "content": text}
    assert text_content(second.body[field][1]).startswith("What next?")
    assert len(second.body[field]) == 2
    assert second.metrics["compaction_applied"] == second.capture_chain[1]
    assert second.metrics["compaction_tokens"] > 0 and second.metrics["context_tokens"] > 1000
    assert second.context_tokens < second.metrics["context_tokens"]
    [record] = (await replay_records(store, second, "replacement")).values()
    assert record["source"] == "compaction" and record["text"] == text
    # The cut is immutable: a retry replays it without another summary.
    again = await prepare(kernel, protocol, messages, credential, policy, summarize=summarize)
    assert again.body == second.body and len(summarize.requests) == 1
    assert "compaction_tokens" not in again.metrics


async def test_continuation_cut_keeps_thinking_and_delivers_the_cut_part_at_once(
    setup_kernel, credential, policy
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(context_window=1024, recall=False)
    thinking = {"type": "enabled", "budget_tokens": 2000}
    call = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "Check the cluster", "signature": "signed"},
            {"type": "tool_use", "id": "t-1", "name": "shell", "input": {"cmd": "kubectl"}},
        ],
    }
    result = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t-1", "content": "blue ok"}],
    }
    messages = [{"role": "user", "content": "Check the deploy"}]
    first = await prepare(
        kernel, "anthropic", messages, credential, policy, extra={"thinking": thinking}
    )
    await answered(kernel, first, credential, call)
    summarize = Summarizer("anthropic")
    second = await prepare(
        kernel,
        "anthropic",
        [*messages, call, result],
        credential,
        policy,
        summarize=summarize,
        extra={"thinking": thinking},
    )
    assert second.kind == "continuation"
    [request] = summarize.requests
    # Nothing is stripped: the signed thinking stays with its tool call.
    assert request["messages"][:3] == [*messages, call, result]
    assert request["max_tokens"] == policy["summary_max_tokens"] + 2000
    assert request["thinking"] == thinking
    # The turn is unfinished, so neither the summary nor its reader may treat it as answered.
    assert text_content(request["messages"][3]).endswith(UNFINISHED)
    assert [m["role"] for m in second.body["messages"]] == ["user"]
    assert second.body["messages"][0]["content"].startswith(
        '<openviking-context source="gateway-compaction">\n' + HEADER + " " + IN_PROGRESS
    )
    # The replaced part is saved before the turn ends, so the model can search it now.
    worker = await worker_for(store, encryption, credential, viking)
    assert await worker.once()
    state = (await store.capture.get(second.scope, second.session)).value
    assert state["delivered"] and not state["pending"]
    assert viking.write_sessions == [second.capture_target]
    # The tool result travels with its call.
    assert viking.writes[0][-1]["parts"][-1]["tool_output"] == "blue ok"


@pytest.mark.parametrize(
    "response,reason",
    [
        (
            {
                "choices": [
                    {"message": {"tool_calls": [{"id": "x"}]}, "finish_reason": "tool_calls"}
                ]
            },
            "summary_tool_call",
        ),
        ({"choices": [{"message": {"content": "cut"}, "finish_reason": "length"}]}, None),
        ({"choices": [{"message": {"content": " "}, "finish_reason": "stop"}]}, "summary_empty"),
        (SummaryError("summary_http_500"), "summary_http_500"),
        (RuntimeError("bug"), "summary_failed"),
    ],
)
async def test_failed_summary_forwards_the_history_and_backs_off(
    setup_kernel, credential, policy, response, reason
):
    kernel, store, _, _ = setup_kernel
    policy.update(context_window=1024, recall=False)
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, "chat", messages, credential, policy)
    reply = {"role": "assistant", "content": "Blue."}
    await answered(kernel, first, credential, reply)
    messages += [reply, {"role": "user", "content": "What next?"}]
    summarize = Summarizer("chat", response)
    failed = await prepare(kernel, "chat", messages, credential, policy, summarize=summarize)
    assert failed.metrics["compaction_failed"] == (reason or "summary_incomplete")
    assert failed.body["messages"] == messages
    assert "compaction_applied" not in failed.metrics
    retried = await prepare(kernel, "chat", messages, credential, policy, summarize=summarize)
    assert retried.body["messages"] == messages and len(summarize.requests) == 1
    # After the backoff the next request tries again.
    old = await get_state(store.state, failed.scope, failed.session)
    value = copy.deepcopy(old.value)
    value["compaction"]["failed_at"] -= 61
    assert await store.state.swap(failed.scope, failed.session, old, value)
    await prepare(kernel, "chat", messages, credential, policy, summarize=summarize)
    assert len(summarize.requests) == 2


async def test_rejected_summary_cap_retries_without_headroom(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    policy.update(context_window=1024, recall=False)
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, "chat", messages, credential, policy)
    reply = {"role": "assistant", "content": "Blue."}
    await answered(kernel, first, credential, reply)
    messages += [reply, {"role": "user", "content": "What next?"}]
    caps = []

    # Models such as gpt-4o or deepseek-chat reject an output cap above their limit.
    async def summarize(prepared, body):
        caps.append(body["max_tokens"])
        if body["max_tokens"] > policy["summary_max_tokens"]:
            raise SummaryError("summary_http_400")
        return summary_response("chat")

    request = await prepare(kernel, "chat", messages, credential, policy, summarize=summarize)
    limit = policy["summary_max_tokens"]
    assert caps == [limit + SUMMARY_HEADROOM, limit]
    assert "compaction_failed" not in request.metrics
    assert request.body["messages"] != messages


async def test_hidden_tool_records_cross_cuts_and_earlier_summaries_fold_in(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(context_window=1024, recall=False, capture=False, gateway_tools=True)
    long = "deploy notes " * 400
    visible = [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "One"},
        {"role": "user", "content": "Second " + long},
        {"role": "assistant", "content": "Two"},
        {"role": "user", "content": "Third"},
    ]
    first = await prepare(kernel, "chat", visible[:1], credential, policy)
    chain = hidden_chain(visible, "chat")

    def transcript(answer, call_id):
        call = {
            "id": call_id,
            "type": "function",
            "function": {"name": "openviking_find", "arguments": "{}"},
        }
        return [
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call_id, "content": "found"},
            {"role": "assistant", "content": answer},
        ]

    for index, answer in ((1, "One"), (3, "Two")):
        record = {"messages": transcript(answer, f"g-{index}"), "visible_count": 1}
        await store.replay.put(first.scope, first.session, "hidden", chain[index], record)
    earlier = {"source": "compaction", "text": "Earlier summary", "tokens": 3}
    await store.replay.put(first.scope, first.session, "replacement", chain[1], earlier)
    summarize = Summarizer("chat")
    request = await prepare(kernel, "chat", visible, credential, policy, summarize=summarize)
    # The new span starts at the earlier summary and expands the hidden rounds after it.
    span = summarize.requests[0]["messages"][:-1]
    assert span == [
        {"role": "user", "content": "Earlier summary"},
        visible[2],
        *transcript("Two", "g-3"),
    ]
    assert request.body["messages"][0]["content"].startswith(
        '<openviking-context source="gateway-compaction">'
    )
    assert request.body["messages"][1:] == [visible[4]]


async def test_concurrent_compactions_share_the_first_written_cut(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    policy.update(context_window=1024, recall=False)
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, "chat", messages, credential, policy, session="one")
    await answered(kernel, first, credential, {"role": "assistant", "content": "Blue."})
    messages += [{"role": "assistant", "content": "Blue."}, {"role": "user", "content": "Next?"}]
    arrived = []
    both = asyncio.Event()

    def summarizer(text):
        async def summarize(prepared, body):
            arrived.append(text)
            if len(arrived) == 2:
                both.set()
            await both.wait()
            return summary_response("chat", text)

        return summarize

    one, two = await asyncio.gather(
        prepare(kernel, "chat", messages, credential, policy, "one", summarize=summarizer("A")),
        prepare(kernel, "chat", messages, credential, policy, "one", summarize=summarizer("B")),
    )
    assert sorted(arrived) == ["A", "B"]
    assert one.body == two.body
    text = one.body["messages"][0]["content"]
    assert ("\n\nA\n" in text) != ("\n\nB\n" in text)


async def test_recall_budget_restarts_after_a_cut(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, "chat", messages, credential, policy)
    spent = first.observation.value["recall"]["spent"]
    policy.update(session_max_tokens=spent + 63)
    # A session continuing the reply inherits the opening recall and its cost.
    await answered(kernel, first, credential, {"role": "assistant", "content": "Blue."})
    messages += [{"role": "assistant", "content": "Blue."}, {"role": "user", "content": "Next?"}]
    exhausted = await prepare(kernel, "chat", messages, credential, policy, session="budget")
    assert exhausted.metrics["recall_reason"] == "disabled" and len(viking.recalls) == 1
    cut = {"source": "compaction", "text": "Earlier summary", "tokens": 3}
    await store.replay.put(
        first.scope, exhausted.session, "replacement", exhausted.capture_chain[1], cut
    )
    messages += [{"role": "assistant", "content": "Done."}, {"role": "user", "content": "More?"}]
    fresh = await prepare(kernel, "chat", messages, credential, policy, session="budget")
    # Memory the cut removed may be recalled again in the new window.
    assert fresh.metrics["recall_reason"] == "recalled"
    assert viking.recalls[-1][2] == []
    assert fresh.observation.value["recall"]["window"] == exhausted.capture_chain[1]


async def test_count_requests_apply_cuts_without_compacting_or_writing_usage(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(context_window=1024, recall=False)
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, "anthropic", messages, credential, policy)
    await answered(kernel, first, credential, {"role": "assistant", "content": "Blue."})
    usage = (await get_state(store.state, first.scope, first.session)).value["usage"]
    messages += [{"role": "assistant", "content": "Blue."}, {"role": "user", "content": "Next?"}]
    summarize = Summarizer("anthropic")
    counted = await prepare(
        kernel, "anthropic", messages, credential, policy, counting=True, summarize=summarize
    )
    assert counted.kind == "count" and not summarize.requests
    assert "context_tokens" not in counted.metrics
    reply = {"role": "assistant", "content": "x"}
    await kernel.completed(
        counted, credential, ResponseCapture("anthropic", reply, usage={"input_tokens": 20})
    )
    assert (await get_state(store.state, first.scope, first.session)).value["usage"] == usage
    cut = {"source": "compaction", "text": "Earlier summary", "tokens": 3}
    await store.replay.put(first.scope, first.session, "replacement", counted.capture_chain[1], cut)
    counted = await prepare(kernel, "anthropic", messages, credential, policy, counting=True)
    assert counted.body["messages"][0] == {"role": "user", "content": "Earlier summary"}
    assert counted.metrics["compaction_applied"] == counted.capture_chain[1]


async def test_usage_only_measures_the_history_its_reply_belongs_to(
    setup_kernel, credential, policy
):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False)
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, "chat", messages, credential, policy)
    reply = {"role": "assistant", "content": "Blue."}
    await answered(kernel, first, credential, reply, tokens=50000)
    messages += [reply, {"role": "user", "content": "Next?"}]
    measured = await prepare(kernel, "chat", messages, credential, policy)
    assert 50005 < measured.context_tokens < 50100
    assert measured.context_window == 1_000_000
    # A subagent's reply never replaces the main history's usage.
    subagent = await kernel.prepare(
        {"model": "model", "messages": [{"role": "user", "content": "Explore the repo"}]},
        "chat",
        {"x-openviking-session": "compact", "x-claude-code-agent-id": "child"},
        credential,
        {"id": "upstream"},
        policy,
    )
    await answered(kernel, subagent, credential, {"role": "assistant", "content": "Done."}, 90000)
    assert (await prepare(kernel, "chat", messages, credential, policy)).context_tokens < 50100
    # Another branch's usage does not describe this history: estimate the body instead.
    branch = await prepare(
        kernel, "chat", [{"role": "user", "content": "Other"}], credential, policy
    )
    await answered(kernel, branch, credential, {"role": "assistant", "content": "Ok."}, 90000)
    assert (await prepare(kernel, "chat", messages, credential, policy)).context_tokens < 1000


async def test_replies_without_usage_leave_the_estimate_to_the_whole_request(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    messages = [{"role": "user", "content": "word " * 2000}]
    first = await prepare(kernel, "chat", messages, credential, policy)
    reply = {"role": "assistant", "content": "Blue."}
    # A streamed reply without include_usage, or a tool loop whose rounds report none.
    response = ResponseCapture("chat", reply, usage=usage_of({}), complete=True)
    await kernel.completed(first, credential, response)
    assert "usage" not in (await get_state(store.state, first.scope, first.session)).value
    messages += [reply, {"role": "user", "content": "Next?"}]
    assert (await prepare(kernel, "chat", messages, credential, policy)).context_tokens > 2500


async def test_a_cut_that_stays_over_the_threshold_backs_off(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    policy.update(context_window=2000, recall=False)
    # The system prompt alone fills the window, and no cut can remove it.
    messages = [
        {"role": "system", "content": "rule " * 1900},
        {"role": "user", "content": "How do I deploy?"},
    ]
    first = await prepare(kernel, "chat", messages, credential, policy)
    reply = {"role": "assistant", "content": "Blue."}
    await answered(kernel, first, credential, reply, tokens=2400)
    messages += [reply, {"role": "user", "content": "Next?"}]
    summarize = Summarizer("chat")
    second = await prepare(kernel, "chat", messages, credential, policy, summarize=summarize)
    assert second.metrics["compaction_tokens"] and second.context_tokens >= 1800
    assert second.metrics["compaction_failed"] == "still_over_threshold"
    await answered(kernel, second, credential, reply, tokens=2400)
    messages += [reply, {"role": "user", "content": "And then?"}]
    third = await prepare(kernel, "chat", messages, credential, policy, summarize=summarize)
    assert len(summarize.requests) == 1 and "compaction_failed" not in third.metrics


def test_estimate_counts_inline_media_like_an_image():
    image = {"type": "image", "source": {"type": "base64", "data": "iVBOR" + "A" * 400000}}
    assert MEDIA_TOKENS <= estimate([image]) < MEDIA_TOKENS + 50
    assert estimate(["word " * 400]) == (len(orjson.dumps(["word " * 400])) + 3) // 4


@pytest.mark.parametrize(
    "protocol,body,cap",
    [
        # Models such as DeepSeek reason without being asked, so headroom is unconditional.
        (
            "chat",
            {"stream": True, "stream_options": {"include_usage": True}},
            {"max_tokens": 24000},
        ),
        ("chat", {"max_completion_tokens": 100}, {"max_completion_tokens": 24000}),
        # o-series models reject max_tokens; a client that sent it keeps it.
        ("chat", {"reasoning_effort": "high"}, {"max_completion_tokens": 24000}),
        ("chat", {"reasoning_effort": "high", "max_tokens": 100}, {"max_tokens": 24000}),
        (
            "anthropic",
            {"thinking": {"type": "enabled", "budget_tokens": 4000}},
            {"max_tokens": 12000},
        ),
        ("anthropic", {"thinking": {"type": "adaptive"}}, {"max_tokens": 24000}),
        ("anthropic", {"max_tokens": 100}, {"max_tokens": 24000}),
        ("responses", {"max_output_tokens": 50}, {"max_output_tokens": 24000}),
        ("responses", {"reasoning": {"effort": "low"}}, {"max_output_tokens": 24000}),
    ],
)
def test_summary_requests_keep_client_settings_and_set_the_cap(protocol, body, cap):
    adapter = tool_protocol(protocol)
    body = {"model": "m", "tools": [{"name": "x"}], "tool_choice": "auto", **body}
    span = [{"role": "user", "content": "hi"}]
    request = adapter.summary_request(body, span, "Summarize", 8000)
    assert request[adapter.field] == [*span, {"role": "user", "content": "Summarize"}]
    assert request["stream"] is False and "stream_options" not in request
    assert request["tools"] == body["tools"] and request["tool_choice"] == "auto"
    assert {key: request[key] for key in cap} == cap
    for key in ("thinking", "reasoning", "reasoning_effort"):
        assert request.get(key) == body.get(key)


@pytest.mark.parametrize(
    "protocol,body,kept",
    [
        (
            "chat",
            {"tool_choice": "required", "response_format": {"type": "json_object"}, "stop": "x"},
            {"tool_choice": "none"},
        ),
        (
            "anthropic",
            {
                "tool_choice": {"type": "any"},
                "stop_sequences": ["x"],
                "output_config": {"effort": "high", "format": {"type": "json_schema"}},
            },
            {"tool_choice": {"type": "none"}, "output_config": {"effort": "high"}},
        ),
        (
            "responses",
            {
                "tool_choice": {"type": "function", "name": "x"},
                "text": {"format": {"type": "json_schema"}, "verbosity": "low"},
            },
            {"tool_choice": "none", "text": {"verbosity": "low"}},
        ),
    ],
)
def test_summary_requests_drop_output_settings_a_summary_cannot_follow(protocol, body, kept):
    adapter = tool_protocol(protocol)
    body = {"model": "m", "tools": [{"name": "x"}], **body}
    original = copy.deepcopy(body)
    request = adapter.summary_request(body, [], "Summarize", 8000)
    assert {key: request[key] for key in kept} == kept
    assert not {"response_format", "stop", "stop_sequences"} & request.keys()
    # The client's own request is forwarded unchanged.
    assert request["tools"] == body["tools"] and body == original


@pytest.mark.parametrize(
    "protocol,response,reason",
    [
        (
            "chat",
            {"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]},
            "summary_incomplete",
        ),
        (
            "anthropic",
            {"content": [{"type": "tool_use", "id": "t"}], "stop_reason": "tool_use"},
            "summary_tool_call",
        ),
        (
            "anthropic",
            {"content": [{"type": "text", "text": "x"}], "stop_reason": "max_tokens"},
            "summary_incomplete",
        ),
        (
            "anthropic",
            {"content": [{"type": "text", "text": "x"}], "stop_reason": "stop_sequence"},
            "summary_incomplete",
        ),
        (
            "responses",
            {"status": "completed", "output": [{"type": "function_call"}]},
            "summary_tool_call",
        ),
        ("responses", {"status": "incomplete", "output": []}, "summary_incomplete"),
    ],
)
def test_summary_text_rejects_tool_calls_and_cut_off_replies(protocol, response, reason):
    with pytest.raises(SummaryError) as error:
        tool_protocol(protocol).summary_text(response)
    assert error.value.reason == reason


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_summary_text_returns_text_only(protocol):
    response = summary_response(protocol, "Summary text")
    if protocol == "anthropic":
        response["content"].insert(0, {"type": "thinking", "thinking": "private"})
    if protocol == "responses":
        response["output"].insert(0, {"type": "reasoning", "summary": []})
    assert tool_protocol(protocol).summary_text(response) == "Summary text"


async def test_new_openviking_sessions_turn_off_working_memory():
    client = VikingClient(None, "http://unused", "0.4.16")
    bodies = []

    async def request(method, path, key, body=None, timeout=30):
        bodies.append(body)
        return {}

    client.request = request
    await client.create_session("key", "session")
    assert bodies[0]["memory_policy"] == {"working_memory": {"enabled": False}}


async def test_proxy_sends_the_summary_with_vendor_alias_and_headers(running_gateway):
    app, client, admin, key, seen, _ = running_gateway
    response = await client.put(
        "/admin/upstreams/chat",
        headers=admin,
        json={
            "name": "chat",
            "protocol": "chat",
            "base_url": str(app.state.config.openviking_url),
            "models": ["model"],
            "aliases": {"alias": "model"},
            "context_windows": {"model": 1024},
        },
    )
    assert response.status_code == 200, response.text
    summaries = []

    async def backend(request):
        if request.path != "/v1/chat/completions":
            return None
        payload = await request.json()
        if text_content(payload["messages"][-1]).startswith(INSTRUCTION_START):
            summaries.append((payload, request.headers.get("Authorization")))
            return web.json_response(summary_response("chat", "Blue cluster chosen."))
        seen.append((request.path, orjson.dumps(payload), dict(request.headers)))
        reply = {"role": "assistant", "content": "hello"}
        return web.json_response(
            {
                "choices": [{"message": reply, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 5},
            }
        )

    app.state.test_backend["handler"] = backend
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "proxy-compaction"}
    messages = [{"role": "user", "content": "How do I deploy?"}]
    body = {"model": "alias", "messages": messages, "stream_options": {"include_usage": True}}
    assert (
        await client.post("/v1/chat/completions", headers=headers, json=body)
    ).status_code == 200
    messages += [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "Next?"}]
    assert (
        await client.post("/v1/chat/completions", headers=headers, json=body)
    ).status_code == 200
    [(summary, authorization)] = summaries
    assert summary["model"] == "model" and summary["stream"] is False
    assert "stream_options" not in summary and authorization == "Bearer model-secret"
    forwarded = orjson.loads(seen[-1][1])["messages"]
    assert "Blue cluster chosen." in forwarded[0]["content"]
    assert text_content(forwarded[1]).startswith("Next?") and len(forwarded) == 2
    logs = (await client.get("/admin/logs", headers=admin)).json()
    [log] = [entry for entry in logs if entry.get("compaction_tokens")]
    assert log["compaction_applied"] and log["context_window"] == 1024


async def test_hints_name_the_sessions_that_hold_earlier_history(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    policy.update(context_window=1024, gateway_tools=True)
    worker = await worker_for(store, encryption, credential, viking)
    reply = {"role": "assistant", "content": "Blue."}
    messages = [{"role": "user", "content": "How do I deploy?"}]
    first = await prepare(kernel, "chat", messages, credential, policy)
    await kernel.completed(first, credential, ResponseCapture("chat", reply, complete=True))
    messages += [reply, {"role": "user", "content": "And staging?"}]
    await prepare(kernel, "chat", messages, credential, policy)
    assert await worker.once()
    # The client compacts its own history: the new history's opening block points
    # to the session that holds the old one.
    messages = [{"role": "user", "content": "Summary of our deploy work. Continue."}]
    fresh = await prepare(kernel, "chat", messages, credential, policy)
    assert fresh.capture_target != first.capture_target
    tools = fresh.root["tools"]
    hint = history_hint("alice", [first.capture_target], tools, True)
    assert hint and hint in opening_block(fresh.records, fresh.chain)
    # A gateway cut names the current session first: the replaced part is queued for it.
    await answered(kernel, fresh, credential, reply)
    messages += [reply, {"role": "user", "content": "Next?"}]
    second = await prepare(
        kernel, "chat", messages, credential, policy, summarize=Summarizer("chat")
    )
    hint = history_hint("alice", [fresh.capture_target, first.capture_target], tools, True)
    summary_block = second.body["messages"][0]["content"].split("</openviking-context>")[0]
    assert summary_block.endswith("\n\n" + hint + "\n")


async def test_a_cut_that_moves_capture_to_a_new_session_names_it_first(
    setup_kernel, credential, policy
):
    kernel, store, viking, encryption = setup_kernel
    policy.update(context_window=1024, gateway_tools=True, recall=False, commit_tokens=1000000)
    worker = await worker_for(store, encryption, credential, viking)
    messages = [{"role": "user", "content": "Fix the build"}]
    first = await prepare(kernel, "chat", messages, credential, policy)
    function = {"name": "run", "arguments": "{}"}
    step = {
        "role": "assistant",
        "content": "Step 0",
        "tool_calls": [{"id": "call-0", "type": "function", "function": function}],
    }
    await answered(kernel, first, credential, step)
    # The tool runs long enough for the turn to be saved while idle, without its call.
    await make_due(store)
    assert await worker.once()
    messages += [step, {"role": "tool", "tool_call_id": "call-0", "content": "ok"}]
    cut = await prepare(kernel, "chat", messages, credential, policy, summarize=Summarizer("chat"))
    state = (await store.capture.get(cut.scope, cut.session)).value
    assert state["reason"] == "continued_after_idle"
    hint = history_hint(
        "alice", [state["ov_session"], first.capture_target], cut.root["tools"], True
    )
    summary_block = cut.body["messages"][0]["content"].split("</openviking-context>")[0]
    assert summary_block.endswith("\n\n" + hint + "\n")
