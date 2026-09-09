"""Model-call timing stays attributable without exposing prompts or streamed text."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from loguru import logger
from vikingbot.agent import loop as loop_module
from vikingbot.agent.loop import AgentLoop
from vikingbot.config.schema import SessionKey
from vikingbot.providers.base import LLMResponse, LLMStreamEvent, ToolCallRequest


@pytest.fixture
def timing_logs():
    """Capture only timing records and restore logging after each test."""
    records = []

    def capture(message):
        marker, payload = message.record["message"].split(" ", 1)
        records.append((marker, json.loads(payload)))

    sink = logger.add(capture, filter=lambda record: record["message"].startswith("[LLM_"))
    try:
        yield records
    finally:
        logger.remove(sink)


def make_loop(provider):
    """Build the provider-facing portion of a loop without workspace or network setup."""
    loop = object.__new__(AgentLoop)
    loop.provider, loop.model, loop.temperature = provider, "fake-model", 0
    return loop


KEY = SessionKey(type="compile", channel_id="cmp_test", chat_id="cmp_test")


@pytest.mark.asyncio
async def test_stream_timing_preserves_events_and_omits_content(monkeypatch, timing_logs):
    clock = [10.0]
    monkeypatch.setattr(loop_module.time, "perf_counter", lambda: clock[0])
    response = LLMResponse(
        content="private answer",
        reasoning_content="private reasoning",
        tool_calls=[ToolCallRequest("tool-1", "write_file", {"content": "private file"}, 0)],
        finish_reason="tool_calls",
        usage={"prompt_tokens": 120, "completion_tokens": 30},
    )

    async def stream(**kwargs):
        clock[0] = 10.1
        yield LLMStreamEvent(type="content_delta", content="")
        clock[0] = 10.3
        yield LLMStreamEvent(type="reasoning_delta", content="private reasoning")
        clock[0] = 12.4
        yield LLMStreamEvent(type="content_delta", content="private answer")
        clock[0] = 13.0
        yield LLMStreamEvent(type="response", response=response)

    loop = make_loop(SimpleNamespace(chat_stream=stream))
    actual, content, reasoning = await loop._chat_with_stream_events(
        [{"role": "user", "content": "private prompt"}],
        [],
        KEY,
        False,
        agent_id="worker-1",
        iteration=7,
    )
    assert actual is response and content and reasoning
    start, end = [record for _, record in timing_logs]
    assert start["call_id"] == end["call_id"]
    assert end["session_id"] == KEY.safe_name()
    assert end["agent_id"] == "worker-1" and end["iteration"] == 7
    assert end["first_event_type"] == "reasoning_delta"
    assert end["first_event_ms"] == end["first_reasoning_ms"] == 300
    assert end["first_content_ms"] == 2400
    assert end["duration_ms"] == 3000
    assert end["usage"] == response.usage
    assert end["tool_names"] == ["write_file"]
    assert end["status"] == "ok" and not end["fallback"]
    assert "private" not in json.dumps(timing_logs)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_buffered_response_has_no_first_delta_time(monkeypatch, timing_logs, fallback):
    clock = [0.0]
    monkeypatch.setattr(loop_module.time, "perf_counter", lambda: clock[0])
    response = LLMResponse(content="done")

    async def stream(**kwargs):
        clock[0] = 2.0
        if not fallback:
            yield LLMStreamEvent(type="response", response=response)

    async def chat(**kwargs):
        clock[0] = 3.0
        return response

    result = await make_loop(
        SimpleNamespace(chat_stream=stream, chat=chat)
    )._chat_with_stream_events(
        [],
        [],
        KEY,
        False,
    )
    assert result == (response, False, False)
    end = timing_logs[-1][1]
    assert end["first_content_ms"] is None and end["first_reasoning_ms"] is None
    assert end["first_event_type"] == (None if fallback else "response")
    assert end["first_event_ms"] == (None if fallback else 2000)
    assert end["duration_ms"] == (3000 if fallback else 2000)
    assert end["fallback"] is fallback and end["usage"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["exception", "error_response", "cancelled"])
async def test_failed_calls_log_their_outcome_and_close_streams(timing_logs, outcome):
    entered, closed = asyncio.Event(), asyncio.Event()

    async def stream(**kwargs):
        try:
            if outcome == "exception":
                raise RuntimeError("private error")
            if outcome == "cancelled":
                entered.set()
                await asyncio.Event().wait()
            yield LLMStreamEvent(
                type="response",
                response=LLMResponse(content="private error", finish_reason="error"),
            )
        finally:
            closed.set()

    loop = make_loop(SimpleNamespace(chat_stream=stream))
    task = asyncio.create_task(loop._chat_with_stream_events([], [], KEY, False))
    if outcome == "cancelled":
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
    if outcome == "error_response":
        assert (await task)[0].finish_reason == "error"
    else:
        with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else RuntimeError):
            await task
    assert closed.is_set()
    assert [marker for marker, _ in timing_logs] == ["[LLM_START]", "[LLM_END]"]
    assert timing_logs[-1][1]["status"] == ("cancelled" if outcome == "cancelled" else "error")
    assert "private" not in json.dumps(timing_logs)
