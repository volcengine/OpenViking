# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest

from openviking.parse.network import (
    ParserNetworkContext,
    ParserNetworkOperation,
    create_parser_httpx_client,
    open_parser_url,
    overlay_parser_subprocess_env,
    register_parser_network_adapter,
    reset_parser_network_adapter,
)


class _Adapter:
    def __init__(self, *, supported: bool = True, transport=None) -> None:
        self.supported = supported
        self.transport = transport if transport is not None else object()
        self.opener = Mock()
        self.opener.open.return_value = object()
        self.contexts: list[ParserNetworkContext] = []

    def supports(self, context: ParserNetworkContext) -> bool:
        self.contexts.append(context)
        return self.supported

    def create_httpx_transport(self, context: ParserNetworkContext):
        return self.transport

    def create_urllib_opener(self, context: ParserNetworkContext):
        return self.opener

    def subprocess_env_overlay(self, context: ParserNetworkContext):
        return {"HTTPS_PROXY": "http://proxy.example:8118"}


@pytest.fixture(autouse=True)
def _reset_adapter():
    reset_parser_network_adapter()
    yield
    reset_parser_network_adapter()


def _context(operation=ParserNetworkOperation.HTTP_RESOURCE):
    return ParserNetworkContext(operation, "https://example.com/resource")


def test_network_import_does_not_load_parser_implementations() -> None:
    script = """
import sys
import openviking.parse.network

forbidden = {
    "openviking.parse.parsers.pdf",
    "openviking.parse.parsers.html",
    "openviking.parse.directory_scan",
    "openviking.parse.vlm",
}
loaded = forbidden.intersection(sys.modules)
if loaded:
    raise SystemExit(f"eager parser imports: {sorted(loaded)}")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_parse_public_exports_remain_available_lazily() -> None:
    import openviking.parse as parse

    assert "PDFParser" in dir(parse)
    assert parse.ParseMode.__name__ == "ParseMode"


def test_context_requires_typed_operation_and_non_empty_url() -> None:
    with pytest.raises(TypeError, match="ParserNetworkOperation"):
        ParserNetworkContext("http_resource", "https://example.com")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty"):
        ParserNetworkContext(ParserNetworkOperation.HTTP_RESOURCE, "  ")


def test_httpx_transport_is_injected_without_dropping_client_kwargs() -> None:
    adapter = _Adapter()
    register_parser_network_adapter(adapter)
    hooks = {"request": [object()]}

    client = create_parser_httpx_client(
        _context(),
        client_cls=lambda **kwargs: SimpleNamespace(kwargs=kwargs),  # type: ignore[arg-type]
        timeout=30,
        follow_redirects=True,
        event_hooks=hooks,
        trust_env=True,
    )

    assert client.kwargs["transport"] is adapter.transport
    assert client.kwargs["trust_env"] is False
    assert client.kwargs["event_hooks"] is hooks
    assert client.kwargs["timeout"] == 30
    assert client.kwargs["follow_redirects"] is True


def test_adapter_cannot_replace_explicit_transport() -> None:
    register_parser_network_adapter(_Adapter())

    with pytest.raises(RuntimeError, match="explicit transport"):
        create_parser_httpx_client(
            _context(),
            client_cls=lambda **kwargs: kwargs,  # type: ignore[arg-type]
            transport=object(),
        )


async def test_request_hook_blocks_private_redirect_before_transport() -> None:
    private_url = "http://127.0.0.1/admin"
    transport_requests: list[str] = []
    validated_requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        transport_requests.append(str(request.url))
        if str(request.url) == private_url:
            pytest.fail("private redirect reached the network transport")
        return httpx.Response(302, headers={"location": private_url}, request=request)

    async def validate_request(request: httpx.Request) -> None:
        url = str(request.url)
        validated_requests.append(url)
        if url == private_url:
            raise PermissionError("private target rejected")

    register_parser_network_adapter(_Adapter(transport=httpx.MockTransport(handler)))
    client = create_parser_httpx_client(
        _context(),
        follow_redirects=True,
        event_hooks={"request": [validate_request]},
        trust_env=False,
    )

    async with client:
        with pytest.raises(PermissionError, match="private target rejected"):
            await client.get(_context().source_url)

    assert transport_requests == [_context().source_url]
    assert validated_requests == [_context().source_url, private_url]


def test_urllib_opener_is_scoped_to_archive_operation() -> None:
    adapter = _Adapter()
    register_parser_network_adapter(adapter)
    context = _context(ParserNetworkOperation.GIT_ARCHIVE)
    request = object()

    result = open_parser_url(context, request, timeout=1800)  # type: ignore[arg-type]

    assert result is adapter.opener.open.return_value
    adapter.opener.open.assert_called_once_with(request, timeout=1800)


def test_subprocess_overlay_preserves_auth_env_without_mutating_it() -> None:
    adapter = _Adapter()
    register_parser_network_adapter(adapter)
    base = {"GIT_CONFIG_COUNT": "1", "SECRET": "kept"}

    result = overlay_parser_subprocess_env(
        _context(ParserNetworkOperation.GIT_COMMAND),
        base,
    )

    assert result == {
        "GIT_CONFIG_COUNT": "1",
        "SECRET": "kept",
        "HTTPS_PROXY": "http://proxy.example:8118",
    }
    assert base == {"GIT_CONFIG_COUNT": "1", "SECRET": "kept"}


def test_unsupported_adapter_preserves_default_behavior() -> None:
    register_parser_network_adapter(_Adapter(supported=False))
    base = {"EXISTING": "value"}
    client = create_parser_httpx_client(
        _context(),
        client_cls=lambda **kwargs: SimpleNamespace(kwargs=kwargs),  # type: ignore[arg-type]
        trust_env=False,
    )

    assert client.kwargs == {"trust_env": False}
    assert overlay_parser_subprocess_env(_context(ParserNetworkOperation.GIT_COMMAND), base) is base
    with patch("openviking.parse.network.urllib.request.urlopen", return_value="direct") as direct:
        assert (
            open_parser_url(
                _context(ParserNetworkOperation.GIT_ARCHIVE), "https://example.com/archive.zip"
            )
            == "direct"
        )
    direct.assert_called_once()


def test_registration_is_idempotent_but_replacement_is_rejected() -> None:
    adapter = _Adapter()
    register_parser_network_adapter(adapter)
    register_parser_network_adapter(adapter)

    with pytest.raises(RuntimeError, match="already registered"):
        register_parser_network_adapter(_Adapter())
