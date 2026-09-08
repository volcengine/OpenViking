# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for LlamaParse REST translation."""

from __future__ import annotations

import io
import ipaddress
import json
from typing import Any

import httpx
import pytest

from openviking_llamaparse_bridge import llamaparse
from openviking_llamaparse_bridge.config import Settings
from openviking_llamaparse_bridge.llamaparse import (
    LlamaParseClient,
    LlamaParseError,
    _resolve_host_addresses,
)


def _client(
    settings: Settings,
    api_handler: Any,
    download_handler: Any | None = None,
) -> tuple[LlamaParseClient, httpx.AsyncClient, httpx.AsyncClient]:
    api = httpx.AsyncClient(
        base_url=settings.llama_base_url,
        headers={"Authorization": f"Bearer {settings.llama_api_key}"},
        transport=httpx.MockTransport(api_handler),
    )
    downloads = httpx.AsyncClient(transport=httpx.MockTransport(download_handler or api_handler))
    return LlamaParseClient(settings, api_client=api, download_client=downloads), api, downloads


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str, _: int) -> set[ipaddress.IPv4Address]:
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address("8.8.8.8")}

    monkeypatch.setattr(llamaparse, "_resolve_host_addresses", resolve)


async def test_upload_file_maps_the_understanding_upload(settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        assert request.method == "POST"
        assert request.url.path == "/api/v1/beta/files"
        assert dict(request.url.params) == {
            "organization_id": "org-1",
            "project_id": "project-1",
        }
        assert request.headers["authorization"] == "Bearer llx-test"
        assert b'name="purpose"' in body
        assert b"parse" in body
        assert b'filename="report.pdf"' in body
        assert b"file bytes" in body
        return httpx.Response(200, json={"id": "file-1", "name": "report.pdf"})

    client, api, downloads = _client(settings, handler)
    try:
        result = await client.upload_file(
            "report.pdf", io.BytesIO(b"file bytes"), "application/pdf"
        )
    finally:
        await api.aclose()
        await downloads.aclose()

    assert result["id"] == "file-1"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ({"file_id": "file-1"}, {"file_id": "file-1"}),
        (
            {"source_url": "https://documents.test/report.pdf"},
            {"source_url": "https://documents.test/report.pdf"},
        ),
    ],
)
async def test_create_job_maps_sources_and_parse_defaults(
    settings: Settings, source: dict[str, str], expected: dict[str, str]
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        assert request.url.path == "/api/v2/parse"
        assert request.url.params["organization_id"] == "org-1"
        assert request.url.params["project_id"] == "project-1"
        assert payload["tier"] == "agentic"
        assert payload["version"] == "latest"
        assert payload["client_name"] == "openviking-understanding-bridge"
        assert payload["output_options"]["images_to_save"] == ["embedded", "layout"]
        assert payload["processing_options"]["cost_optimizer"] == {"enable": True}
        assert {key: payload[key] for key in expected} == expected
        return httpx.Response(200, json={"id": "job-1", "status": "PENDING"})

    client, api, downloads = _client(settings, handler)
    try:
        result = await client.create_job(**source)
    finally:
        await api.aclose()
        await downloads.aclose()

    assert result["id"] == "job-1"


async def test_parse_options_are_merged_without_mutating_settings(settings: Settings) -> None:
    options = {
        "client_name": "custom-client",
        "output_options": {"images_to_save": ["screenshot"]},
        "processing_options": {"ocr_parameters": {"languages": ["en"]}},
    }
    custom = Settings(
        **{
            **settings.__dict__,
            "parse_options": options,
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        assert payload["client_name"] == "custom-client"
        assert payload["output_options"]["images_to_save"] == [
            "screenshot",
            "embedded",
            "layout",
        ]
        assert payload["processing_options"]["ocr_parameters"] == {"languages": ["en"]}
        assert payload["processing_options"]["cost_optimizer"] == {"enable": True}
        return httpx.Response(200, json={"id": "job-1"})

    client, api, downloads = _client(custom, handler)
    try:
        await client.create_job(file_id="file-1")
    finally:
        await api.aclose()
        await downloads.aclose()

    assert options == {
        "client_name": "custom-client",
        "output_options": {"images_to_save": ["screenshot"]},
        "processing_options": {"ocr_parameters": {"languages": ["en"]}},
    }


async def test_get_job_requests_result_expansions(settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(b"/api/v2/parse/job%2Fone")
        assert request.url.params["expand"] == "markdown_full,markdown,images_content_metadata"
        return httpx.Response(200, json={"job": {"status": "COMPLETED"}})

    client, api, downloads = _client(settings, handler)
    try:
        result = await client.get_job("job/one", include_result=True)
    finally:
        await api.aclose()
        await downloads.aclose()

    assert result["job"]["status"] == "COMPLETED"


@pytest.mark.parametrize(
    ("response", "expected_status", "message"),
    [
        (httpx.Response(429, json={"detail": "rate limited"}), 429, "rate limited"),
        (
            httpx.Response(400, json={"error": {"message": "bad request"}}),
            400,
            "bad request",
        ),
        (
            httpx.Response(422, json={"detail": [{"msg": "invalid tier"}]}),
            422,
            "invalid tier",
        ),
        (httpx.Response(503, text="upstream unavailable"), 503, "upstream unavailable"),
        (httpx.Response(200, text="not json"), 502, "invalid JSON"),
        (httpx.Response(200, json=[]), 502, "invalid response object"),
    ],
)
async def test_upstream_errors_remain_actionable(
    settings: Settings, response: httpx.Response, expected_status: int, message: str
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return response

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match=message) as error:
            await client.get_job("job-1")
    finally:
        await api.aclose()
        await downloads.aclose()

    assert error.value.status_code == expected_status


async def test_create_job_rejects_missing_or_ambiguous_source(settings: Settings) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid source reached LlamaParse")

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(ValueError, match="exactly one"):
            await client.create_job()
        with pytest.raises(ValueError, match="exactly one"):
            await client.create_job(file_id="file-1", source_url="https://documents.test/a.pdf")
    finally:
        await api.aclose()
        await downloads.aclose()


async def test_download_uses_a_client_without_the_llama_api_key(settings: Settings) -> None:
    async def api_handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("asset request used the LlamaParse API client")

    async def download_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://assets.test/image.png?signature=secret"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"image")

    client, api, downloads = _client(settings, api_handler, download_handler)
    try:
        content = await client.download_asset("https://assets.test/image.png?signature=secret")
    finally:
        await api.aclose()
        await downloads.aclose()

    assert content == b"image"


async def test_download_rejects_insecure_asset_url(settings: Settings) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"private data")

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match="HTTPS"):
            await client.download_asset("http://169.254.169.254/latest/meta-data")
    finally:
        await api.aclose()
        await downloads.aclose()


async def test_asset_host_resolution_returns_ip_addresses() -> None:
    addresses = await _resolve_host_addresses("127.0.0.1", 443)

    assert addresses == {ipaddress.ip_address("127.0.0.1")}


async def test_download_rejects_redirect_to_private_address(
    settings: Settings,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "assets.test":
            return httpx.Response(302, headers={"Location": "https://127.0.0.1/secret"})
        return httpx.Response(200, content=b"private data")

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match="public address"):
            await client.download_asset("https://assets.test/image.png")
    finally:
        await api.aclose()
        await downloads.aclose()


async def test_download_validates_and_follows_relative_redirect(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "/new"}, request=request)
        return httpx.Response(200, content=b"image", request=request)

    client, api, downloads = _client(settings, handler)
    try:
        content = await client.download_asset("https://assets.test/old")
    finally:
        await api.aclose()
        await downloads.aclose()

    assert content == b"image"
    assert [request.url.path for request in requests] == ["/old", "/new"]
    assert all("authorization" not in request.headers for request in requests)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://127.0.0.1/secret", "public address"),
        ("https://user:password@assets.test/file", "must not contain credentials"),
    ],
)
async def test_download_rejects_unsafe_asset_destinations(
    settings: Settings, url: str, message: str
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("unsafe destination was requested")

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match=message):
            await client.download_asset(url)
    finally:
        await api.aclose()
        await downloads.aclose()


async def test_download_rejects_redirect_without_location(settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, request=request)

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match="redirect has no location"):
            await client.download_asset("https://assets.test/image.png")
    finally:
        await api.aclose()
        await downloads.aclose()


async def test_download_rejects_too_many_redirects(settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"}, request=request)

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match="too many redirects"):
            await client.download_asset("https://assets.test/image.png")
    finally:
        await api.aclose()
        await downloads.aclose()


async def test_download_returns_asset_http_error(settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "signature expired"}, request=request)

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match="signature expired") as error:
            await client.download_asset("https://assets.test/image.png")
    finally:
        await api.aclose()
        await downloads.aclose()

    assert error.value.status_code == 403


async def test_download_rejects_invalid_port(settings: Settings) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid URL was requested")

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match="asset URL is invalid"):
            await client.download_asset("https://assets.test:invalid/image.png")
    finally:
        await api.aclose()
        await downloads.aclose()


@pytest.mark.parametrize(
    ("error", "expected_status", "message"),
    [
        (httpx.ReadTimeout("timeout"), 504, "timed out"),
        (httpx.ConnectError("offline"), 502, "request failed"),
    ],
)
async def test_network_errors_are_translated(
    settings: Settings, error: Exception, expected_status: int, message: str
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise error

    client, api, downloads = _client(settings, handler)
    try:
        with pytest.raises(LlamaParseError, match=message) as raised:
            await client.get_job("job-1")
    finally:
        await api.aclose()
        await downloads.aclose()

    assert raised.value.status_code == expected_status


async def test_client_closes_owned_connections(settings: Settings) -> None:
    client = LlamaParseClient(settings)

    await client.aclose()

    assert client._api.is_closed  # noqa: SLF001
    assert client._downloads.is_closed  # noqa: SLF001
