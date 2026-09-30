"""recall_status, last_recall_outcome and unavailable_reason through the public provider API."""

import json
import threading
from contextlib import contextmanager

import httpx
import pytest


def _memory(name):
    return {"uri": f"viking://user/alice/memories/{name}.md", "abstract": f"Remembered {name}.", "score": 0.9}


@contextmanager
def _home_scope(home):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


@pytest.fixture
def recall(external_provider, inject_deps):
    home, provider, module, _ = external_provider("recall-status")
    (home / "config.yaml").write_text(
        "memory:\n  provider: openviking\n  openviking:\n"
        "    endpoint: http://openviking.test\n"
        "    use_ovcli_config: false\n"
        "    recall_scope: shared\n"
        "    recall_timeout_seconds: 5\n"
        "    recall_request_timeout_seconds: 5\n"
        "    recall_prefer_abstract: true\n",
        encoding="utf-8",
    )
    # search: list of memories, an exception class to raise, or a callable(payload) -> response.
    behavior = {"search": [], "gate": None, "home": home}

    def handle(request):
        path = request.url.path
        if path == "/api/v1/system/status":
            return httpx.Response(200, json={"result": {"user": "alice"}})
        if path == "/api/v1/content/read":
            return httpx.Response(404, json={"error": "not found"})
        if path == "/api/v1/fs/ls":
            return httpx.Response(200, json={"result": []})
        assert path in ("/api/v1/search/search", "/api/v1/search/find")
        payload = json.loads(request.content)
        if behavior["gate"] is not None and payload["query"] == "slow query":
            behavior["gate"]["entered"].set()
            assert behavior["gate"]["release"].wait(10)
            return httpx.Response(200, json={"result": {"memories": [_memory("late")]}})
        search = behavior["search"]
        if isinstance(search, type) and issubclass(search, BaseException):
            raise search("search failed")
        if callable(search):
            return search(payload)
        return httpx.Response(200, json={"result": {"memories": search}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        inject_deps(module, provider, transport=lambda: client, health=lambda *_: ("healthy", ""))
        provider.initialize("s1", hermes_home=str(home), platform="telegram", user_id="alice")
        yield provider, module, behavior


def test_injected_recall_reports_entry_count(recall):
    provider, module, behavior = recall
    assert provider.last_recall_outcome() == ""
    assert provider.recall_status() is None
    behavior["search"] = [_memory("tea"), _memory("coffee")]
    assert "Remembered tea." in provider.prefetch("what do I drink", session_id="s1")
    assert provider.recall_status() == module.RecallStatus(provider_label="OpenViking", count=2)
    assert provider.last_recall_outcome("s1") == provider.last_recall_outcome() == "injected"


@pytest.mark.parametrize(
    "search,outcome",
    [
        ([], "empty"),
        (httpx.ReadTimeout, "timeout"),
        (TimeoutError, "timeout"),
        (ValueError, "error"),
        (lambda payload: httpx.Response(500, json={"error": "boom"}), "error"),
    ],
    ids=["empty", "read-timeout", "timeout-error", "value-error", "http-500"],
)
def test_failed_or_empty_recall_has_no_indicator(recall, search, outcome):
    provider, _module, behavior = recall
    behavior["search"] = search
    assert provider.prefetch("what do I drink", session_id="s1") == ""
    assert provider.recall_status() is None
    assert provider.last_recall_outcome("s1") == outcome


def test_unreachable_server_is_unavailable(external_provider, inject_deps):
    home, provider, module, _ = external_provider("recall-status-down")
    (home / "config.yaml").write_text(
        "memory:\n  provider: openviking\n  openviking:\n"
        "    endpoint: http://openviking.test\n    use_ovcli_config: false\n",
        encoding="utf-8",
    )
    inject_deps(module, provider, health=lambda *_: ("responded", "down"))
    provider.initialize("s1", hermes_home=str(home), platform="telegram")
    assert provider.prefetch("what do I drink", session_id="s1") == ""
    assert provider.recall_status() is None
    assert provider.last_recall_outcome("s1") == "unavailable"


def test_context_mode_counts_server_entries(recall):
    provider, module, behavior = recall

    def context(payload):
        assert payload["mode"] == "context"
        return httpx.Response(200, json={"result": {"rendered": "Alice drinks tea.", "entries": [{}, {}, {}]}})

    behavior["search"] = context
    provider.save_config({"recall_compress": "server"}, str(behavior["home"]))
    assert "Alice drinks tea." in provider.prefetch("what do I drink", session_id="s1")
    assert provider.recall_status() == module.RecallStatus(provider_label="OpenViking", count=3)


def test_status_reflects_only_the_latest_prefetch(recall):
    provider, _module, behavior = recall
    behavior["search"] = [_memory("tea")]
    assert provider.prefetch("what do I drink", session_id="s1")
    behavior["search"] = []
    assert provider.prefetch("what do I drink", session_id="s2") == ""
    # The earlier session keeps its own outcome, but the indicator follows the last prefetch.
    assert provider.recall_status() is None
    assert provider.last_recall_outcome("s1") == "injected"
    assert provider.last_recall_outcome("s2") == provider.last_recall_outcome() == "empty"


def test_running_prefetch_hides_the_previous_result(recall):
    provider, _module, behavior = recall
    behavior["search"] = [_memory("tea")]
    assert provider.prefetch("what do I drink", session_id="s1")
    gate = {"entered": threading.Event(), "release": threading.Event()}
    behavior["gate"] = gate
    results = []
    slow = threading.Thread(target=lambda: results.append(provider.prefetch("slow query", session_id="s1")))
    slow.start()
    try:
        assert gate["entered"].wait(10)
        assert provider.recall_status() is None
        assert provider.last_recall_outcome("s1") == "pending"
        # The host abandons the slow call; a newer prefetch for the same session wins.
        behavior["search"] = []
        assert provider.prefetch("what do I drink", session_id="s1") == ""
    finally:
        gate["release"].set()
        slow.join(10)
    assert results and "Remembered late." in results[0]
    assert provider.last_recall_outcome("s1") == "empty"
    assert provider.recall_status() is None


def test_host_recall_indicator(recall):
    from agent.memory_manager import MemoryManager

    provider, _module, behavior = recall
    manager = MemoryManager(external_prefetch_timeout=10)
    manager.add_provider(provider)
    behavior["search"] = [_memory("tea")]
    assert manager.prefetch_all("what do I drink", session_id="s1")
    assert manager.describe_recall().endswith("OpenViking — recalled 1 memory")


@pytest.mark.parametrize(
    "linked,ovcli,expected",
    [
        (False, None, "no endpoint is configured"),
        (True, None, "does not exist"),
        (True, "{not json", "could not be read"),
        (True, '["not", "an", "object"]', "must be a JSON object"),
        (True, '{"url": "ftp://openviking.test"}', "could not be read (Invalid OpenViking endpoint"),
        (True, '{"api_key": "secret-key"}', 'has no "url"'),
    ],
    ids=["unlinked", "missing-ovcli", "bad-json", "not-object", "bad-url", "no-url"],
)
def test_unavailable_reason(external_provider, tmp_path, linked, ovcli, expected):
    home, provider, _module, _ = external_provider("unavailable")
    ovcli_path = tmp_path / "ovcli.conf"
    if ovcli is not None:
        ovcli_path.write_text(ovcli, encoding="utf-8")
    (home / "config.yaml").write_text(
        "memory:\n  provider: openviking\n  openviking:\n"
        f"    use_ovcli_config: {'true' if linked else 'false'}\n"
        f"    ovcli_config_path: {ovcli_path}\n",
        encoding="utf-8",
    )
    with _home_scope(home):
        assert provider.is_available() is False
        reason = provider.unavailable_reason()
    assert reason.startswith("OpenViking: ")
    assert expected in reason
    assert "hermes memory setup" in reason
    assert "secret-key" not in reason
    if linked:
        assert str(ovcli_path) in reason


def test_unavailable_reason_is_empty_when_available(external_provider):
    home, provider, _module, _ = external_provider("available")
    (home / "config.yaml").write_text(
        "memory:\n  provider: openviking\n  openviking:\n"
        "    endpoint: http://openviking.test\n    use_ovcli_config: false\n",
        encoding="utf-8",
    )
    with _home_scope(home):
        assert provider.is_available() is True
        assert provider.unavailable_reason() == ""
