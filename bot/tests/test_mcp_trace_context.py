import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from opentelemetry.context import Context, attach, detach
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    TraceState,
    set_span_in_context,
)
from vikingbot.agent.tools import mcp as mcp_module
from vikingbot.agent.tools.mcp import MCPToolWrapper


def _wrapper() -> tuple[SimpleNamespace, MCPToolWrapper]:
    session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(content=[])))
    tool = SimpleNamespace(
        name="inspect_context",
        description="Inspect received trace context",
        inputSchema={"type": "object", "properties": {}},
    )
    return session, MCPToolWrapper(session, "probe", tool)


def test_mcp_tool_call_forwards_active_w3c_trace_context():
    session, wrapper = _wrapper()
    span_context = SpanContext(
        trace_id=0x0AF7651916CD43DD8448EB211C80319C,
        span_id=0x00F067AA0BA902B7,
        is_remote=False,
        trace_flags=TraceFlags.SAMPLED,
        trace_state=TraceState([("vendor", "state")]),
    )
    token = attach(set_span_in_context(NonRecordingSpan(span_context)))
    try:
        asyncio.run(wrapper.execute(None, query="probe"))
    finally:
        detach(token)

    session.call_tool.assert_awaited_once_with(
        "inspect_context",
        arguments={"query": "probe"},
        meta={
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
            "tracestate": "vendor=state",
        },
    )


def test_mcp_tool_call_omits_meta_without_active_trace_context():
    session, wrapper = _wrapper()
    token = attach(Context())
    try:
        asyncio.run(wrapper.execute(None, query="probe"))
    finally:
        detach(token)

    assert session.call_tool.await_args.kwargs["meta"] is None


def test_mcp_tool_call_survives_trace_context_injection_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    session, wrapper = _wrapper()
    monkeypatch.setattr(
        mcp_module._trace_context_propagator,
        "inject",
        Mock(side_effect=RuntimeError("broken propagator")),
    )

    asyncio.run(wrapper.execute(None, query="probe"))

    assert session.call_tool.await_args.kwargs["meta"] is None
