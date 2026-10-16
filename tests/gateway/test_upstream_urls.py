"""Shared SDK URL vectors and protocol authentication."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openviking_gateway.models import Upstream
from openviking_gateway.proxy import ProxyRequest, upstream_headers, upstream_url

VECTORS = json.loads(
    (Path(__file__).parents[1] / "fixtures/gateway_upstream_urls.json").read_text()
)


@pytest.mark.parametrize("vector", VECTORS, ids=lambda value: value["name"])
def test_upstream_url_vectors(vector):
    upstream = {"protocol": vector["protocol"], "base_url": vector["base_url"]}
    assert upstream_url(upstream, vector["path"]) == vector["expected"]


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
