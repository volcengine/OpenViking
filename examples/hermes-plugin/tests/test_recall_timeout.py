"""Recall timeout diagnostics through the installed external provider."""

import logging

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
    behavior = {"error": None, "fallback": False}

    def handle(request):
        requests.append(request.url.path)
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
        provider.initialize("recall-session", hermes_home=str(home))
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
