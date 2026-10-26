"""Shared SDK URL vectors and protocol authentication."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openviking_gateway.models import Upstream
from openviking_gateway.proxy import ProxyRequest, upstream_headers, upstream_url

VECTORS = json.loads(
    (Path(__file__).parents[1] / "fixtures/gateway_upstream_urls.json").read_text()
)


@pytest.mark.parametrize("vector", VECTORS, ids=lambda value: value["name"])
def test_upstream_url_vectors(vector):
    upstream = {
        "protocol": vector["protocol"],
        "base_url": vector["base_url"],
        "append_endpoint_path": vector.get("append_endpoint_path", True),
    }
    if vector.get("error"):
        with pytest.raises(HTTPException) as error:
            upstream_url(upstream, vector["path"], vector.get("query", ""))
        assert error.value.status_code == 404
    else:
        assert upstream_url(upstream, vector["path"], vector.get("query", "")) == vector["expected"]


@pytest.mark.parametrize(
    ("protocol", "path"),
    [
        ("anthropic", "/v1/messages"),
        ("chat", "/v1/chat/completions"),
        ("responses", "/v1/responses"),
    ],
)
def test_upstream_url_can_skip_endpoint_path(protocol, path):
    upstream = {
        "protocol": protocol,
        "base_url": "https://api.example.test/custom/endpoint/",
        "append_endpoint_path": False,
    }
    assert upstream_url(upstream, path) == "https://api.example.test/custom/endpoint"


@pytest.mark.parametrize("prefix", ["api/v3", "api/compatible/v1", "v1"])
@pytest.mark.parametrize(
    "suffix", ["embeddings", "models", "responses/id/cancel", "messages/count_tokens"]
)
def test_all_client_prefixes_normalize(prefix, suffix):
    app = SimpleNamespace(state=SimpleNamespace(config=None, store=None, management=None))
    request = ProxyRequest(app, None, prefix + "/" + suffix, {})
    assert request.path == "/v1/" + suffix


@pytest.mark.parametrize("base", ["https://example.test/v1", "https://example.test/api/v1/"])
def test_anthropic_base_rejects_v1_suffix(base):
    with pytest.raises(ValidationError, match="appends /v1/messages"):
        Upstream(name="test", protocol="anthropic", base_url=base)


@pytest.mark.parametrize(
    "base",
    [
        "https://example.test/v1?key={token}",
        "https://{api_key}.example.test/v1",
        "https://example.test/v1#frag",
        "https://user:pass@example.test/v1",
    ],
)
def test_base_url_rejects_other_placeholders_fragments_and_credentials(base):
    with pytest.raises(ValidationError):
        Upstream(name="test", protocol="chat", base_url=base)


def test_base_url_keeps_query_and_strips_path_slash():
    upstream = Upstream(
        name="test", protocol="chat", base_url="https://example.test/v1/?key={api_key}"
    )
    assert upstream.base_url == "https://example.test/v1?key={api_key}"


def test_endpoint_path_defaults_on_and_allows_anthropic_v1_when_off():
    default = Upstream(name="default", protocol="chat", base_url="https://example.test/v1")
    exact = Upstream(
        name="exact",
        protocol="anthropic",
        base_url="https://example.test/v1",
        append_endpoint_path=False,
    )
    assert default.append_endpoint_path is True
    assert exact.append_endpoint_path is False


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
def test_upstream_auth_headers(protocol):
    headers = upstream_headers(
        {
            "protocol": protocol,
            "api_key": "secret",
            "headers": {"authorization": "wrong", "x-api-key": "gateway-plan-key"},
        },
        {
            "authorization": "Bearer ovgw_fake",
            "x-api-key": "wrong",
            "anthropic-version": "2023-06-01",
        },
    )
    assert headers["Authorization"] == "Bearer secret"
    assert "authorization" not in headers
    # A configured x-api-key reaches Bearer upstreams; Anthropic's own header replaces it.
    assert headers.get("x-api-key") == ("secret" if protocol == "anthropic" else "gateway-plan-key")
    assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.parametrize(
    ("key_header", "base_url", "expected"),
    [
        ("api-key", "https://example.test/chat", {"api-key": "secret"}),
        ("x-api-key", "https://example.test/chat", {"x-api-key": "secret"}),
        ("authorization", "https://example.test/chat?key={api_key}", {}),
        ("api-key", "https://example.test/{api_key}/chat", {}),
    ],
)
def test_key_header_and_url_placeholder(key_header, base_url, expected):
    headers = upstream_headers(
        {
            "protocol": "chat",
            "base_url": base_url,
            "api_key": "secret",
            "key_header": key_header,
            "headers": {"Authorization": "x", key_header.upper(): "y", "custom": "1"},
        },
        {},
    )
    auth = {
        k: v for k, v in headers.items() if k.lower() in {"authorization", "api-key", "x-api-key"}
    }
    assert auth == expected
    assert headers["custom"] == "1"
