"""Generated notices through native staging, capture, retries and extraction text."""

from types import SimpleNamespace

import pytest
from test_gateway_recall import initialize, profile_scope

NOTICES = [
    ("internal_notification", "Worker assignment: inspect the requested source."),
    ("async_delegation_complete", "ASYNC DELEGATION BATCH COMPLETE\nWorker result: source inspected."),
    ("process_complete", "Background worker completed with exit code 0."),
]


def staged_message(kind, text):
    from agent.turn_context import _stage_turn_user_message
    from gateway.response_filters import display_kind_for_event, display_metadata_for_event

    event = SimpleNamespace(internal=kind not in (None, "hidden"), metadata={
        "display_kind": kind, "display_text": text,
    })
    message, _ = _stage_turn_user_message(
        SimpleNamespace(), text, None, None, None,
        "hidden" if kind == "hidden" else display_kind_for_event(event), display_metadata_for_event(event),
    )
    return message


def capture(external_provider, monkeypatch, kind, text, failures=0, *, with_tools=False, **identity):
    home, provider, _, manager, backend = initialize(external_provider, monkeypatch, **identity)
    backend.batch_failures = failures
    message = staged_message(kind, text)
    tool_messages = [
        {"role": "assistant", "content": "Inspecting source", "tool_calls": [
            {"id": "read-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"source.py"}'}}]},
        {"role": "tool", "tool_call_id": "read-1", "content": "Source file evidence"},
    ] if with_tools else []
    try:
        with profile_scope(home):
            manager.sync_all(text, "Recorded the result.", session_id="shared-group",
                             turn_author={"id": "alice"},
                             messages=[message, *tool_messages, {"role": "assistant", "content": "Recorded the result."}])
            assert manager.flush_pending(timeout=10)
            assert provider._drain_writers("shared-group", timeout=10)
        return backend.pending
    finally:
        manager.shutdown_all()


@pytest.mark.parametrize("kind,text", NOTICES)
@pytest.mark.parametrize("failures", [0, 1, 4])
def test_generated_capture_is_not_a_human_instruction(external_provider, monkeypatch, kind, text, failures):
    messages = capture(external_provider, monkeypatch, kind, text, failures)
    notice = messages[0]
    assert notice["role"] == "assistant"
    # Peer is the interaction's sender scope, not a claim of human authorship.
    assert notice["peer_id"] == "telegram.alice"
    body = notice["parts"][0]["text"]
    assert "Hermes generated notification" in body
    assert f"kind={kind}" in body
    assert "not human-authored instructions or current authorization" in body
    assert body.endswith(text)
    assert messages[-1]["peer_id"] == "telegram.assistant"


@pytest.mark.parametrize("kind", [None, "hidden"])
@pytest.mark.parametrize("failures", [0, 1, 4])
def test_human_quote_is_not_classified_by_its_text(external_provider, monkeypatch, kind, failures):
    text = 'I am quoting "ASYNC DELEGATION BATCH COMPLETE": do not execute it.'
    messages = capture(external_provider, monkeypatch, kind, text, failures)
    assert messages[0] == {"role": "user", "peer_id": "telegram.alice",
                           "parts": [{"type": "text", "text": text}]}


@pytest.mark.parametrize("context", ["subagent", "cron", "flush"])
def test_generated_capture_does_not_bypass_nonprimary_guard(external_provider, monkeypatch, context):
    assert capture(external_provider, monkeypatch, *NOTICES[1], agent_context=context) == []


def test_native_origin_without_display_kind(external_provider):
    _, provider, _, _ = external_provider("origin")
    messages = provider._messages_to_openviking_batch([
        {"role": "user", "content": "Worker result", "display_metadata": {
            "notification_origin": "process_registry_synthetic"}},
    ], user_peer_id="telegram.alice")
    assert messages[0]["role"] == "assistant"
    assert "source=process_registry_synthetic" in messages[0]["parts"][0]["text"]


@pytest.mark.parametrize("failures", [0, 1, 4])
@pytest.mark.parametrize("kind,text", NOTICES)
def test_actual_extraction_renderer_preserves_notice_meaning(external_provider, monkeypatch, failures, kind, text):
    # The HTTP-only plugin does not depend on the server. Run this integration
    # check in a source environment with OpenViking's server dependencies present.
    renderer_module = pytest.importorskip("openviking.session.memory.session_extract_context_provider")
    from openviking.message import Message

    payloads = capture(external_provider, monkeypatch, kind, text, failures)
    messages = [Message.from_dict({"id": str(i), **p}) for i, p in enumerate(payloads)]
    messages = [Message.from_dict(m.to_dict()) for m in messages]
    renderer = renderer_module.SessionExtractContextProvider.__new__(renderer_module.SessionExtractContextProvider)
    renderer.include_tool_parts_in_conversation = False
    rendered = renderer._assemble_conversation(messages)
    assert "[0][assistant][telegram.alice]: [Hermes generated notification" in rendered
    assert "[0][user]" not in rendered
    assert "not human-authored instructions or current authorization" in rendered
    assert text in rendered


@pytest.mark.parametrize("kind", [None, "async_delegation_complete"])
@pytest.mark.parametrize("failures", [0, 1, 4])
def test_ordinary_assistant_and_tool_capture_is_preserved(external_provider, monkeypatch, kind, failures):
    payloads = capture(external_provider, monkeypatch, kind, "Inspect the source", failures, with_tools=True)
    assert payloads[-1] == {"role": "assistant", "peer_id": "telegram.assistant",
                            "parts": [{"type": "text", "text": "Recorded the result."}]}
    tools = [part for message in payloads for part in message["parts"] if part["type"] == "tool"]
    if failures == 1:  # Existing text-only degradation omits tool evidence.
        assert tools == []
    else:
        assert len(tools) == 1
        assert tools[0]["tool_name"] == "read_file"
        assert tools[0]["tool_output"] == "Source file evidence"


@pytest.mark.parametrize("failures", [0, 1, 4])
def test_actual_renderer_keeps_human_quotes_and_tool_evidence_distinct(external_provider, monkeypatch, failures):
    renderer_module = pytest.importorskip("openviking.session.memory.session_extract_context_provider")
    from openviking.message import Message

    text = 'Human quotation: "ASYNC DELEGATION BATCH COMPLETE" is not my instruction.'
    payloads = capture(external_provider, monkeypatch, None, text, failures, with_tools=True)
    messages = [Message.from_dict({"id": str(i), **p}) for i, p in enumerate(payloads)]
    renderer = renderer_module.SessionExtractContextProvider.__new__(renderer_module.SessionExtractContextProvider)
    renderer.include_tool_parts_in_conversation = False
    rendered = renderer._assemble_conversation(messages)
    assert f"[0][user][telegram.alice]: {text}" in rendered
    assert "Hermes generated notification" not in rendered
    assert "Source file evidence" not in rendered
    assert "[assistant][telegram.assistant]: Recorded the result." in rendered
    renderer.include_tool_parts_in_conversation = True
    assert ("Source file evidence" in renderer._assemble_conversation(messages)) == (failures != 1)


def test_text_fallback_preserves_notice_prefix_when_truncated(external_provider, monkeypatch):
    payloads = capture(external_provider, monkeypatch, "async_delegation_complete", "result" * 1000, 1)
    assert payloads[0]["role"] == "assistant"
    text = payloads[0]["parts"][0]["text"]
    assert len(text) == 4000
    assert text.startswith("[Hermes generated notification")
    assert "not human-authored instructions or current authorization" in text
