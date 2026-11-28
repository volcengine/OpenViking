"""recall_exclude_uris forwarding on the context recall path."""

import json
import logging

import httpx
import pytest


@pytest.fixture
def exclude_provider(external_provider, monkeypatch):
    def install(settings_yaml: str):
        home, provider, module, _ = external_provider("exclude")
        (home / "config.yaml").write_text(
            "memory:\n  provider: openviking\n  openviking:\n"
            "    endpoint: http://openviking.test\n"
            "    use_ovcli_config: false\n"
            + settings_yaml,
            encoding="utf-8",
        )
        requests = []

        def handle(request):
            if request.content:
                requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(
                200,
                json={"result": {"rendered": "<memory>ok</memory>", "stats": {}, "memories": [], "resources": []}},
            )

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(httpx, "get", client.get)
            monkeypatch.setattr(httpx, "post", client.post)
            monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))
            provider.initialize(
                "exclude-session", hermes_home=str(home), platform="telegram", user_id="sender-1"
            )
            return provider, module, requests

    yield install


def test_configured_list_is_forwarded_on_context_search(exclude_provider):
    provider, _module, requests = exclude_provider(
        "    recall_compress: server\n"
        "    recall_exclude_uris:\n"
        "      - viking://user/memories/skills\n"
        "      - viking://agent/skills\n"
    )
    assert provider._search_prefetch_context("what is the current status?", client=provider._client)
    payload = next(body for path, body in requests if path.endswith("/api/v1/search/search"))
    assert payload["exclude_uris"] == ["viking://user/memories/skills", "viking://agent/skills"]


def test_comma_string_form_is_normalized(exclude_provider):
    provider, _module, requests = exclude_provider(
        "    recall_compress: server\n"
        '    recall_exclude_uris: " viking://user/memories/skills, viking://agent/skills\\nviking://user/memories/skills"\n'
    )
    assert provider._search_prefetch_context("what is the current status?", client=provider._client)
    payload = next(body for path, body in requests if path.endswith("/api/v1/search/search"))
    assert payload["exclude_uris"] == ["viking://user/memories/skills", "viking://agent/skills"]


def test_unset_sends_no_exclude_uris(exclude_provider):
    provider, _module, requests = exclude_provider("    recall_compress: server\n")
    assert provider._search_prefetch_context("what is the current status?", client=provider._client)
    for _path, body in requests:
        assert "exclude_uris" not in body


def test_list_fallback_search_never_receives_exclude_uris(exclude_provider):
    # The server refuses exclude_uris outside mode=context; the provider must
    # not send it on the search/find fallback either.
    provider, _module, requests = exclude_provider(
        "    recall_exclude_uris: viking://agent/skills\n"
    )
    provider._search_prefetch_context("what is the current status?", client=provider._client)
    fallback = [body for path, body in requests if path.endswith("/api/v1/search/find")]
    assert fallback
    for body in fallback:
        assert "exclude_uris" not in body


def test_invalid_shape_warns_and_falls_back_to_off(exclude_provider, caplog):
    provider, _module, requests = exclude_provider(
        "    recall_compress: server\n"
        "    recall_exclude_uris: 42\n"
    )
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        assert provider._search_prefetch_context("what is the current status?", client=provider._client)
    for _path, body in requests:
        assert "exclude_uris" not in body
    warnings = [record.getMessage() for record in caplog.records
                if record.name == type(provider).__module__ and record.levelno == logging.WARNING]
    assert any("Invalid" in message and "recall_exclude_uris" in message for message in warnings)


def test_non_viking_entry_warns_and_falls_back_to_off(exclude_provider, caplog):
    # The server validates every exclude_uris entry; one malformed value would
    # fail every context recall request, so the plugin refuses it up front.
    provider, _module, requests = exclude_provider(
        "    recall_compress: server\n"
        "    recall_exclude_uris: skills,viking://agent/skills\n"
    )
    with caplog.at_level(logging.WARNING, logger=type(provider).__module__):
        assert provider._search_prefetch_context("what is the current status?", client=provider._client)
    for _path, body in requests:
        assert "exclude_uris" not in body
    warnings = [record.getMessage() for record in caplog.records
                if record.name == type(provider).__module__ and record.levelno == logging.WARNING]
    assert any("Invalid" in message and "recall_exclude_uris" in message for message in warnings)
