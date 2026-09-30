"""Recall timeout diagnostics through the installed external provider."""

import json
import logging
import threading
import time
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def recall_provider(external_provider, inject_deps):
    home, provider, module, _ = external_provider("recall-timeout")
    (home / "config.yaml").write_text(
        "memory:\n  provider: openviking\n  openviking:\n"
        "    endpoint: http://openviking.test\n"
        "    use_ovcli_config: false\n"
        "    recall_timeout_seconds: 1.5\n"
        "    recall_request_timeout_seconds: 0.75\n"
        "    recall_prefer_abstract: true\n",
        encoding="utf-8",
    )
    requests = []
    behavior = {"error": None, "fallback": False, "respond": None, "home": home}

    def handle(request):
        requests.append(request.url.path)
        if behavior["respond"] is not None:
            return behavior["respond"](request)
        if behavior["error"] is not None and not (
            behavior["fallback"] and request.url.path == "/api/v1/search/find"
        ):
            raise behavior["error"]("private query and identity in exception")
        return httpx.Response(
            200,
            json={"result": {"memories": [{
                "uri": "viking://user/private-identity/memories/decision.md",
                "abstract": "The decision is retained.",
                "score": 0.9,
            }]}},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        inject_deps(module, provider, transport=lambda: client, health=lambda *_: ("healthy", ""))
        provider.initialize(
            "recall-session", hermes_home=str(home), platform="telegram", user_id="private-sender"
        )
        yield provider, module, requests, behavior


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout,
    httpx.PoolTimeout, TimeoutError,
])
def test_timeout_warns_without_private_text_or_extra_requests(recall_provider, caplog, error):
    provider, _module, requests, behavior = recall_provider
    behavior["error"] = error
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        assert provider._search_prefetch_context(
            "private query", client=provider._client
        ) == ""

    warnings = [record.getMessage() for record in caplog.records
                if record.name == type(provider).__module__ and record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "recall timed out" in warnings[0]
    assert error.__name__ in warnings[0]
    assert "budget_s=1.5" in warnings[0]
    assert "request_s=0.75" in warnings[0]
    assert "private query" not in warnings[0]
    assert "private-identity" not in warnings[0]
    assert "private query and identity in exception" not in warnings[0]
    assert requests == ["/api/v1/search/find"]


def test_recovered_session_search_timeout_is_quiet(recall_provider, caplog):
    provider, _module, requests, behavior = recall_provider
    behavior.update(error=httpx.ReadTimeout, fallback=True)
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        result = provider._search_prefetch_context(
            "what was the decision", session_id="recall-session", client=provider._client
        )
    assert "The decision is retained." in result
    assert requests == ["/api/v1/search/search", "/api/v1/search/find"]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_non_timeout_error_keeps_existing_debug_behavior(recall_provider, caplog):
    provider, _module, requests, behavior = recall_provider
    behavior["error"] = ValueError
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        assert provider._search_prefetch_context(
            "what was the decision", client=provider._client
        ) == ""
    assert requests == ["/api/v1/search/find"]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def configure_query_recall(provider, behavior, *, scope, compress):
    provider.save_config(
        {"recall_scope": scope, "recall_compress": compress}, str(behavior["home"])
    )
    provider.on_turn_start(1, "private query", author_id="private-sender")
    # A later turn has already received the once-per-session profile block.
    provider._profile_prefetched_sessions.add("recall-session")


def assert_timeout_warning(caplog, provider, error):
    warnings = [record.getMessage() for record in caplog.records
                if record.name == type(provider).__module__ and record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert f"({error.__name__}; budget_s=1.5 request_s=0.75)" in warnings[0]
    assert "private" not in warnings[0]


@pytest.mark.parametrize("compress", ["off", "server", "auto"])
@pytest.mark.parametrize("error", [httpx.ReadTimeout, TimeoutError])
def test_public_peer_identity_timeout_warns_and_stops(
    recall_provider, caplog, compress, error
):
    provider, _module, requests, behavior = recall_provider
    configure_query_recall(provider, behavior, scope="peer", compress=compress)
    behavior["error"] = error
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        assert provider.prefetch("private query", session_id="recall-session") == ""
    assert_timeout_warning(caplog, provider, error)
    assert requests == ["/api/v1/system/status"]


@pytest.mark.parametrize("compress", ["off", "server", "auto"])
@pytest.mark.parametrize("scope", ["shared", "peer"])
def test_public_prefetch_exhausted_budget_warns_without_fallback_request(
    recall_provider, inject_deps, caplog, compress, scope
):
    provider, module, requests, behavior = recall_provider
    configure_query_recall(provider, behavior, scope=scope, compress=compress)
    clock = SimpleNamespace(now=100.0)
    inject_deps(module, provider, monotonic=lambda: clock.now)

    def respond(request):
        if request.url.path == "/api/v1/system/status":
            return httpx.Response(200, json={"result": {"user": "private-identity"}})
        assert request.url.path == "/api/v1/search/search"
        # Both scopes have a sender here, so every compression setting uses context mode.
        assert json.loads(request.content).get("mode") == "context"
        # Consume the entire budget in the first search. The real deadline
        # check must prevent a second HTTP request and report TimeoutError.
        clock.now += 1.5
        raise httpx.ReadTimeout("private query and identity in exception")

    behavior["respond"] = respond
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        assert provider.prefetch("private query", session_id="recall-session") == ""
    assert_timeout_warning(caplog, provider, TimeoutError)
    assert requests == (
        ["/api/v1/system/status"] if scope == "peer" else []
    ) + ["/api/v1/search/search"]


@pytest.mark.parametrize("error", [httpx.ReadTimeout, ValueError])
def test_identity_probe_default_preserves_existing_fallback(
    recall_provider, caplog, error
):
    provider, module, requests, behavior = recall_provider
    behavior["error"] = error
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        assert module._resolve_user_space(provider._client) is None
    assert requests == ["/api/v1/system/status"]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("compress", ["server", "auto"])
def test_public_compression_timeout_with_successful_fallback_is_quiet(
    recall_provider, caplog, compress
):
    provider, _module, requests, behavior = recall_provider
    configure_query_recall(provider, behavior, scope="shared", compress=compress)

    def respond(request):
        if json.loads(request.content).get("mode") == "context":
            raise httpx.ReadTimeout("private query and identity in exception")
        return httpx.Response(200, json={"result": {"memories": [{
            "uri": "viking://user/private-identity/memories/decision.md",
            "abstract": "The decision is retained.", "score": 0.9,
        }]}})

    behavior["respond"] = respond
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        result = provider.prefetch("private query", session_id="recall-session")
    assert "The decision is retained." in result
    assert requests == ["/api/v1/search/search", "/api/v1/search/search"]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def write_recall_config(home, **settings):
    lines = [
        "memory:", "  provider: openviking", "  openviking:",
        "    endpoint: http://openviking.test", "    use_ovcli_config: false",
        "    recall_prefer_abstract: true",
    ]
    lines += [f"    {key}: {value}" for key, value in settings.items()]
    (home / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def join_prefetch_threads():
    for thread in threading.enumerate():
        if thread.name.startswith("openviking-prefetch-"):
            thread.join(5)
            assert not thread.is_alive()


def budget_responder(release, *, slow):
    """Serve the session-start block and one recall hit; ``slow`` parts wait for ``release``."""
    def respond(request):
        path = request.url.path
        if path == "/api/v1/system/status":
            return httpx.Response(200, json={"result": {"user": "alice"}})
        if path == "/api/v1/fs/ls":
            return httpx.Response(200, json={"result": []})
        part = "session-start" if path == "/api/v1/content/read" else "recall"
        if part in slow:
            assert release.wait(10)
        if part == "session-start":
            return httpx.Response(200, json={"result": "Alice drinks tea."})
        return httpx.Response(200, json={"result": {"memories": [{
            "uri": "viking://user/alice/memories/decision.md",
            "abstract": "The decision is retained.", "score": 0.9,
        }]}})
    return respond


def test_prefetch_budget_bounds_both_slow_parts(recall_provider):
    provider, _module, _requests, behavior = recall_provider
    write_recall_config(behavior["home"], recall_timeout_seconds=0.5, recall_request_timeout_seconds=0.5)
    release = threading.Event()
    behavior["respond"] = budget_responder(release, slow={"session-start", "recall"})
    started = time.monotonic()
    try:
        assert provider.prefetch("what was the decision", session_id="recall-session") == ""
        elapsed = time.monotonic() - started
    finally:
        release.set()
        join_prefetch_threads()
    assert elapsed < 1.5
    assert provider.last_recall_outcome("recall-session") == "timeout"
    # The block never reached a turn, so the late part must not latch the session.
    assert "recall-session" not in provider._profile_prefetched_sessions
    assert provider._session_start_claims == {}


@pytest.mark.parametrize("slow", ["session-start", "recall"])
def test_prefetch_returns_the_part_that_met_the_budget(recall_provider, slow):
    provider, _module, _requests, behavior = recall_provider
    write_recall_config(behavior["home"], recall_timeout_seconds=0.5, recall_request_timeout_seconds=0.5)
    release = threading.Event()
    behavior["respond"] = budget_responder(release, slow={slow})
    started = time.monotonic()
    try:
        result = provider.prefetch("what was the decision", session_id="recall-session")
        elapsed = time.monotonic() - started
    finally:
        release.set()
        join_prefetch_threads()
    assert elapsed < 1.5
    assert ("The decision is retained." in result) == (slow == "session-start")
    assert ("Alice drinks tea." in result) == (slow == "recall")
    assert provider.last_recall_outcome("recall-session") == "injected"
    assert provider.recall_status().count == (1 if slow == "session-start" else 0)
    # A dropped session-start block is injected by the next prefetch instead.
    assert ("recall-session" in provider._profile_prefetched_sessions) == (slow == "recall")
    behavior["respond"] = budget_responder(release, slow=set())
    assert ("Alice drinks tea." in provider.prefetch("", session_id="recall-session")) == (
        slow == "session-start"
    )


@pytest.mark.parametrize("timeouts", [{}, {"recall_timeout_seconds": 60, "recall_request_timeout_seconds": 60}])
@pytest.mark.parametrize("compress", ["server", "auto"])
def test_rewrite_timeout_falls_back_within_the_prefetch_budget(
    recall_provider, inject_deps, caplog, compress, timeouts
):
    provider, module, requests, behavior = recall_provider
    write_recall_config(behavior["home"], recall_scope="shared", recall_compress=compress, **timeouts)
    provider._profile_prefetched_sessions.add("recall-session")
    clock = SimpleNamespace(now=100.0)
    inject_deps(module, provider, monotonic=lambda: clock.now)
    seen = []

    def respond(request):
        timeout = request.extensions["timeout"]["read"]
        seen.append((request.url.path, json.loads(request.content).get("mode"), timeout))
        if json.loads(request.content).get("mode") == "context":
            # The rewrite request uses its whole timeout, then times out.
            clock.now += timeout
            raise httpx.ReadTimeout("rewrite took too long")
        return httpx.Response(200, json={"result": {"memories": [{
            "uri": "viking://user/alice/memories/decision.md",
            "abstract": "The decision is retained.", "score": 0.9,
        }]}})

    behavior["respond"] = respond
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        result = provider.prefetch("what was the decision", session_id="recall-session")
    assert "The decision is retained." in result
    # 7.5 s budget: the rewrite request keeps 1 s for the search without rewrite.
    # That leaves no time for the session-aware search, so search/find runs.
    assert requests == ["/api/v1/search/search", "/api/v1/search/find"]
    assert seen[0][1] == "context" and seen[0][2] == pytest.approx(6.5)
    assert seen[1][2] == pytest.approx(1.0)
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_concurrent_prefetches_inject_the_session_start_block_once(recall_provider):
    provider, _module, requests, behavior = recall_provider
    write_recall_config(behavior["home"], recall_timeout_seconds=5, recall_request_timeout_seconds=5)
    entered, release = threading.Event(), threading.Event()
    serve = budget_responder(release, slow=set())

    def respond(request):
        if request.url.path == "/api/v1/content/read":
            entered.set()
            assert release.wait(10)
        return serve(request)

    behavior["respond"] = respond
    first = []
    thread = threading.Thread(target=lambda: first.append(provider.prefetch("", session_id="recall-session")))
    thread.start()
    try:
        assert entered.wait(10)
        # A second prefetch for the same session while the first is fetching the block.
        assert provider.prefetch("", session_id="recall-session") == ""
    finally:
        release.set()
        thread.join(10)
    assert first and "Alice drinks tea." in first[0]
    assert requests.count("/api/v1/content/read") == 1
    assert provider.prefetch("", session_id="recall-session") == ""
    assert requests.count("/api/v1/content/read") == 1
