"""Recall timeout diagnostics through the installed external provider."""

import json
import logging
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def recall_provider(external_provider, monkeypatch):
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
        monkeypatch.setattr(httpx, "get", client.get)
        monkeypatch.setattr(httpx, "post", client.post)
        monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))
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
    recall_provider, monkeypatch, caplog, compress, scope
):
    provider, module, requests, behavior = recall_provider
    configure_query_recall(provider, behavior, scope=scope, compress=compress)
    clock = SimpleNamespace(now=100.0)
    local_time = SimpleNamespace(**vars(module.time))
    local_time.monotonic = lambda: clock.now
    monkeypatch.setattr(module, "time", local_time)

    def respond(request):
        if request.url.path == "/api/v1/system/status":
            return httpx.Response(200, json={"result": {"user": "private-identity"}})
        assert request.url.path == "/api/v1/search/search"
        assert (json.loads(request.content).get("mode") == "context") == (compress != "off")
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
