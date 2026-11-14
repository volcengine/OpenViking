"""Injected Studio scaffolding blocks must not reach OpenViking captures.

Studio re-injects <studio_task_plan_context>, <studio_session_tasks_context>
and <memory-context> blocks into every user turn. Captured unchanged, the
injection becomes next turn's memory and inflates VLM extraction. Inline or
backtick-fenced examples of these tags are user prose and survive (#6858).
"""
import pytest


@pytest.fixture
def plugin_module(external_provider):
    _, provider, module, _ = external_provider("injected-blocks")
    return module


def test_strip_injected_system_blocks_removes_studio_blocks(plugin_module):
    text = (
        "please remember my setup\n"
        "<studio_task_plan_context>\n"
        "task 1: build the thing\n"
        "</studio_task_plan_context>\n"
        "thanks"
    )
    assert plugin_module._strip_injected_system_blocks(text) == (
        "please remember my setup\nthanks"
    )


def test_strip_injected_system_blocks_handles_all_three_tags(plugin_module):
    text = (
        "<memory-context>\n"
        "<available-memories>old memories</available-memories>\n"
        "</memory-context>\n"
        "<studio_session_tasks_context>\n"
        "- pending task\n"
        "</studio_session_tasks_context>\n"
        "the actual user message"
    )
    assert plugin_module._strip_injected_system_blocks(text) == "the actual user message"


def test_strip_injected_system_blocks_strips_unterminated_block_to_end(plugin_module):
    text = "before\n<studio_task_plan_context>\nnever closed"
    assert plugin_module._strip_injected_system_blocks(text) == "before"


def test_strip_injected_system_blocks_keeps_fenced_examples(plugin_module):
    text = (
        "example:\n"
        "```\n"
        "<memory-context>\n"
        "</memory-context>\n"
        "```\n"
        "done"
    )
    # The example lives inside a fence the sanitizer does not parse; only
    # standalone blocks are stripped, and this one has no opening tag on its
    # own line outside the fence, so the text passes through unchanged.
    assert plugin_module._strip_injected_system_blocks(text) == text


def test_strip_injected_system_blocks_leaves_plain_text_untouched(plugin_module):
    text = "no injections here\nsecond line <memory-context> inline\nthird"
    assert plugin_module._strip_injected_system_blocks(text) == text


def test_sync_turn_batch_drops_injected_blocks(external_provider, monkeypatch):
    """The batch main path strips injected blocks from user and assistant text."""
    home, provider, module, _ = external_provider("injected-batch")
    monkeypatch.setenv("HERMES_HOME", str(home))
    provider._hermes_home = str(home)
    provider._session_id = "inj-sid"
    messages = [
        {
            "role": "user",
            "content": (
                "<memory-context>\n"
                "<available-memories>x</available-memories>\n"
                "</memory-context>\n"
                "what is the plan?"
            ),
        },
        {"role": "assistant", "content": "here is the plan"},
    ]
    batch = module.OpenVikingMemoryProvider._messages_to_openviking_batch(
        messages, assistant_peer_id="agent", user_peer_id="user"
    )
    user_texts = [
        part["text"]
        for message in batch
        if message["role"] == "user"
        for part in message["parts"]
        if part.get("type") == "text"
    ]
    assert user_texts == ["what is the plan?"]


def test_remember_tool_strips_injected_blocks(external_provider, monkeypatch):
    """viking_remember submits sanitized content."""
    home, provider, module, _ = external_provider("injected-remember")
    monkeypatch.setenv("HERMES_HOME", str(home))
    provider._hermes_home = str(home)
    captured = {}

    class _Client:
        def post(self, path, payload):
            captured.setdefault("payloads", []).append(payload)
            return {"status": "accepted"}

        def get(self, path):
            return {"pending_tokens": 20000}

    provider._client = _Client()
    provider._ensure_client = lambda: provider._client
    provider._unwrap_result = staticmethod(lambda result: result)
    provider._user_space = lambda _client: "main"
    result = provider._tool_remember({
        "content": (
            "<studio_task_plan_context>\n"
            "injected\n"
            "</studio_task_plan_context>\n"
            "remember this fact"
        )
    })
    assert '"status": "submitted"' in result or "submitted" in result
    message_payload = captured["payloads"][0]
    assert message_payload["parts"][0]["text"] == "remember this fact"
