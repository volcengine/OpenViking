"""viking_session_history: default sessions across compression, older servers, and the pointer note."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

_POINTER_JS = (
    Path(__file__).resolve().parents[2]
    / "memory-plugin-shared"
    / "lib"
    / "session-history-pointer.mjs"
)


@pytest.fixture
def history_provider(external_provider, monkeypatch):
    home, provider, module, _ = external_provider("session-history")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:19531")
    monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))
    provider.initialize("sid-1", hermes_home=str(home))
    client = Mock()
    client.get.return_value = {"pending_tokens": 0}
    client.post.return_value = {
        "status": "ok",
        "result": {"text": "found it", "ids": [], "has_more": False},
    }
    provider._client = client
    provider._ensure_client = lambda: client
    provider._new_client = lambda: client
    monkeypatch.setattr(provider, "_session_start_memory_context", lambda _sid: "")
    monkeypatch.setattr(provider, "_search_prefetch_context", lambda *_args, **_kwargs: "")
    yield provider, module, client
    assert provider._drain_finalizers(timeout=5)


def _history_calls(client):
    return [call.args for call in client.post.call_args_list if call.args[0].endswith("/history")]


def test_tool_defaults_to_the_compression_lineage(history_provider):
    provider, module, client = history_provider
    assert "viking_session_history" in {schema["name"] for schema in provider.get_tool_schemas()}
    assert module._is_openviking_recall_tool_name("viking_session_history")

    assert (
        provider.handle_tool_call(
            "viking_session_history", {"action": "search", "query": "ECONNREFUSED", "role": None}
        )
        == "found it"
    )
    provider.on_session_switch("sid-2", parent_session_id="sid-1", reason="compression")
    provider.handle_tool_call(
        "viking_session_history", {"action": "read", "id": "a#3", "offset": 10}
    )
    provider.handle_tool_call(
        "viking_session_history", {"action": "overview", "session": "other, older"}
    )

    assert _history_calls(client) == [
        (
            "/api/v1/sessions/sid-1/history",
            {"action": "search", "query": "ECONNREFUSED", "sessions": []},
        ),
        (
            "/api/v1/sessions/sid-2/history",
            {"action": "read", "id": "a#3", "offset": 10, "sessions": ["sid-1"]},
        ),
        ("/api/v1/sessions/other/history", {"action": "overview", "sessions": ["older"]}),
    ]


def test_lineage_keeps_ten_sessions_and_resets_on_new(history_provider):
    provider, _, client = history_provider
    for n in range(2, 14):
        provider.on_session_switch(f"sid-{n}", reason="compression")
    provider.handle_tool_call("viking_session_history", {"action": "overview"})
    provider.on_session_switch("fresh", reset=True, reason="new_session")
    provider.handle_tool_call("viking_session_history", {"action": "overview"})

    (path, body), (fresh_path, fresh_body) = _history_calls(client)
    assert path == "/api/v1/sessions/sid-13/history"
    assert body["sessions"] == [f"sid-{n}" for n in range(12, 3, -1)]
    assert (fresh_path, fresh_body["sessions"]) == ("/api/v1/sessions/fresh/history", [])


def test_branch_keeps_the_parent_and_current_id_means_the_lineage(history_provider):
    provider, _, client = history_provider
    provider.on_session_switch("sid-2", parent_session_id="sid-1", reason="compression")
    provider.on_session_switch("sid-3", parent_session_id="sid-2", reason="branch")
    provider.handle_tool_call("viking_session_history", {"action": "overview", "session": "sid-3"})
    provider.on_session_switch("sid-4", parent_session_id="sid-3", reason="resume")
    provider.handle_tool_call("viking_session_history", {"action": "overview"})

    assert _history_calls(client) == [
        ("/api/v1/sessions/sid-3/history", {"action": "overview", "sessions": ["sid-2", "sid-1"]}),
        ("/api/v1/sessions/sid-4/history", {"action": "overview", "sessions": []}),
    ]


def test_older_server_answers_with_one_line(history_provider):
    provider, module, client = history_provider
    for status in (404, 405):
        client.post.side_effect = module._OpenVikingHTTPError("Not Found", status)
        assert (
            provider.handle_tool_call("viking_session_history", {"action": "overview"})
            == module._HISTORY_UNSUPPORTED
        )
    client.post.side_effect = module._OpenVikingHTTPError("boom", 500)
    assert "boom" in provider.handle_tool_call("viking_session_history", {"action": "overview"})


def test_pointer_follows_compression_and_resume(history_provider, monkeypatch):
    provider, _, _ = history_provider
    assert "session-history" not in provider.prefetch("hi")

    provider.on_session_switch("sid-2", reason="compression")
    first = provider.prefetch("hi")
    assert "session-history" not in provider.prefetch("next question")
    assert (
        "OpenViking keeps the full record of this session as sid-2, including what the compaction summary replaced"
        in first
    )
    assert "look it up with the viking_session_history tool" in first
    assert 'pass session="sid-2"' in first

    monkeypatch.setenv("OPENVIKING_SESSION_HISTORY_POINTER", "false")
    provider.on_session_switch("sid-2", reason="compression")
    assert "session-history" not in provider.prefetch("hi")
    monkeypatch.delenv("OPENVIKING_SESSION_HISTORY_POINTER")

    provider.on_session_switch("sid-3", reason="resume")
    assert "including anything an earlier compaction summarized" in provider.prefetch("hi")
    provider.on_session_switch("sid-4", reason="branch")
    assert "session-history" not in provider.prefetch("hi")
    provider.on_session_switch("sid-4", reason="compression")
    assert "as sid-4, including what the compaction summary replaced" in provider.prefetch("hi")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("event", ["compact", "resume"])
def test_pointer_matches_the_shared_wording(history_provider, event):
    _, module, _ = history_provider
    script = (
        f"import({str(_POINTER_JS)!r}).then(m => process.stdout.write(m.formatSessionHistoryPointer('sid-9', "
        f"{{event: {event!r}, tool: 'the viking_session_history tool'}})))"
    )
    expected = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    ).stdout
    assert module._format_session_history_pointer("sid-9", event=event) == expected
