# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for network_guard SSRF protection utilities."""

from __future__ import annotations

import asyncio
import ssl
import time
from collections.abc import Iterable
from unittest.mock import AsyncMock, patch

import httpcore
import httpx
import pytest

from openviking.utils.httpx_transport import PinnedAddressHTTPTransport
from openviking.utils.network_guard import (
    ValidatedAsyncHTTPTransport,
    _is_public_ip,
    _normalize_host,
    _resolve_host_addresses,
    build_httpx_request_validation_hooks,
    build_httpx_secure_transport,
    ensure_public_remote_target,
    extract_remote_host,
    normalize_verified_addresses,
)
from openviking_cli.exceptions import PermissionDeniedError

# ── extract_remote_host ──────────────────────────────────────────────────────


class TestExtractRemoteHost:
    """Verify host extraction from URLs and git SSH addresses."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("https://example.com/repo.git", "example.com"),
            ("http://example.com:8080/path", "example.com"),
            ("https://sub.domain.example.com/foo", "sub.domain.example.com"),
            ("ftp://files.example.org/data.zip", "files.example.org"),
        ],
    )
    def test_extracts_host_from_http_urls(self, source: str, expected: str) -> None:
        assert extract_remote_host(source) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("git@github.com:user/repo.git", "github.com"),
            ("git@gitlab.com:group/project.git", "gitlab.com"),
            ("git@[::1]:user/repo.git", "::1"),
        ],
    )
    def test_extracts_host_from_git_ssh(self, source: str, expected: str) -> None:
        assert extract_remote_host(source) == expected

    def test_git_ssh_missing_colon_returns_none(self) -> None:
        assert extract_remote_host("git@github.com") is None

    def test_url_without_hostname_returns_none(self) -> None:
        assert extract_remote_host("/just/a/path") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_remote_host("") is None

    def test_strips_brackets_from_ipv6_host(self) -> None:
        result = extract_remote_host("http://[::1]:8080/path")
        assert result == "::1"


# ── _normalize_host ──────────────────────────────────────────────────────────


class TestNormalizeHost:
    """Verify trailing-dot stripping and lowercasing."""

    def test_strips_trailing_dot(self) -> None:
        assert _normalize_host("example.com.") == "example.com"

    def test_lowercases_host(self) -> None:
        assert _normalize_host("EXAMPLE.COM") == "example.com"

    def test_strips_dot_and_lowercases(self) -> None:
        assert _normalize_host("Example.COM.") == "example.com"


# ── _is_public_ip ───────────────────────────────────────────────────────────


class TestIsPublicIP:
    """Verify classification of public vs non-public IPs."""

    @pytest.mark.parametrize(
        "address",
        [
            "8.8.8.8",
            "1.1.1.1",
            "151.101.1.67",
            "2607:f8b0:4004:800::200e",  # Google IPv6
        ],
    )
    def test_public_addresses_are_global(self, address: str) -> None:
        assert _is_public_ip(address) is True

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.1.1",
            "0.0.0.0",
            "169.254.1.1",  # link-local
            "::1",
            "fe80::1",  # IPv6 link-local
            "fc00::1",  # IPv6 ULA
            "::ffff:127.0.0.1",  # IPv4-mapped IPv6 loopback
            "::ffff:10.0.0.1",  # IPv4-mapped IPv6 private
            "::ffff:192.168.1.1",  # IPv4-mapped IPv6 private
        ],
    )
    def test_non_public_addresses_are_not_global(self, address: str) -> None:
        assert _is_public_ip(address) is False

    def test_invalid_address_returns_false(self) -> None:
        assert _is_public_ip("not-an-ip") is False

    def test_empty_string_returns_false(self) -> None:
        assert _is_public_ip("") is False


# ── _resolve_host_addresses ──────────────────────────────────────────────────


class TestResolveHostAddresses:
    """Verify DNS resolution wrapper behavior."""

    def test_returns_empty_set_for_unresolvable_host(self) -> None:
        result = _resolve_host_addresses("this.host.definitely.does.not.exist.invalid")
        assert result == ()

    def test_returns_empty_set_for_unicode_error(self) -> None:
        # A hostname that triggers UnicodeError in getaddrinfo
        result = _resolve_host_addresses("\udcff.invalid")
        assert result == ()

    @patch("openviking.utils.network_guard.socket.getaddrinfo")
    def test_strips_ipv6_scope_id(self, mock_getaddrinfo) -> None:
        import socket

        mock_getaddrinfo.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("fe80::1%eth0", 0, 0, 0)),
        ]
        result = _resolve_host_addresses("some-host")
        assert "fe80::1" in result
        assert "fe80::1%eth0" not in result

    @patch("openviking.utils.network_guard.socket.getaddrinfo")
    def test_skips_non_inet_families(self, mock_getaddrinfo) -> None:
        mock_getaddrinfo.return_value = [
            (999, 1, 0, "", ("1.2.3.4", 0)),  # unknown AF
        ]
        result = _resolve_host_addresses("some-host")
        assert result == ()

    @patch("openviking.utils.network_guard.socket.getaddrinfo")
    def test_preserves_resolver_order_and_removes_duplicates(self, mock_getaddrinfo) -> None:
        import socket

        mock_getaddrinfo.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2606:4700::1111", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2606:4700::1111", 0, 0, 0)),
        ]

        assert _resolve_host_addresses("dual-stack.example") == (
            "2606:4700::1111",
            "1.1.1.1",
        )


# ── ensure_public_remote_target ──────────────────────────────────────────────


class TestEnsurePublicRemoteTarget:
    """End-to-end SSRF protection tests."""

    # -- Rejection: no valid host --

    def test_rejects_empty_source(self) -> None:
        with pytest.raises(PermissionDeniedError, match="valid destination host"):
            ensure_public_remote_target("")

    def test_rejects_bare_path(self) -> None:
        with pytest.raises(PermissionDeniedError, match="valid destination host"):
            ensure_public_remote_target("/etc/passwd")

    def test_rejects_git_ssh_without_colon(self) -> None:
        with pytest.raises(PermissionDeniedError, match="valid destination host"):
            ensure_public_remote_target("git@github.com")

    # -- Rejection: localhost variants --

    @pytest.mark.parametrize(
        "source",
        [
            "http://localhost/path",
            "http://localhost.localdomain/path",
            "http://LOCALHOST/path",
            "http://sub.localhost/path",
            "http://anything.localhost/path",
        ],
    )
    def test_rejects_localhost_variants(self, source: str) -> None:
        with pytest.raises(PermissionDeniedError, match="non-public"):
            ensure_public_remote_target(source)

    def test_rejects_localhost_with_trailing_dot(self) -> None:
        with pytest.raises(PermissionDeniedError, match="non-public"):
            ensure_public_remote_target("http://localhost./path")

    # -- Rejection: non-public resolved IPs --

    @pytest.mark.parametrize(
        ("source", "resolved_ip"),
        [
            ("http://evil.attacker.com/path", "127.0.0.1"),
            ("http://evil.attacker.com/path", "10.0.0.1"),
            ("http://evil.attacker.com/path", "172.16.0.1"),
            ("http://evil.attacker.com/path", "192.168.1.1"),
            ("http://evil.attacker.com/path", "0.0.0.0"),
            ("http://evil.attacker.com/path", "::1"),
            ("http://evil.attacker.com/path", "fe80::1"),
            ("http://evil.attacker.com/path", "::ffff:127.0.0.1"),
            ("http://evil.attacker.com/path", "::ffff:10.0.0.1"),
            ("http://evil.attacker.com/path", "169.254.169.254"),  # AWS metadata
        ],
    )
    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_rejects_non_public_resolved_addresses(
        self, mock_resolve, source: str, resolved_ip: str
    ) -> None:
        mock_resolve.return_value = (resolved_ip,)
        with pytest.raises(PermissionDeniedError, match="non-public address"):
            ensure_public_remote_target(source)

    # -- Rejection: DNS rebinding with mixed results --

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_rejects_when_any_resolved_address_is_non_public(self, mock_resolve) -> None:
        """DNS rebinding: even if some IPs are public, one private IP is enough to reject."""
        mock_resolve.return_value = ("8.8.8.8", "127.0.0.1")
        with pytest.raises(PermissionDeniedError, match="non-public address"):
            ensure_public_remote_target("http://rebinding.attacker.com/path")

    # -- Pass-through: valid public targets --

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_allows_public_http_url(self, mock_resolve) -> None:
        mock_resolve.return_value = ("151.101.1.67",)
        assert ensure_public_remote_target("https://github.com/repo.git") == ("151.101.1.67",)

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_allows_public_git_ssh(self, mock_resolve) -> None:
        mock_resolve.return_value = ("140.82.121.4",)
        assert ensure_public_remote_target("git@github.com:user/repo.git") == ("140.82.121.4",)

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_configured_code_hosting_domain_does_not_bypass_address_check(
        self, mock_resolve
    ) -> None:
        mock_resolve.return_value = ("127.0.0.1",)
        with pytest.raises(PermissionDeniedError, match="non-public address"):
            ensure_public_remote_target("git@ssh.dev.azure.com:v3/org/project/repo")

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_rejects_when_dns_returns_empty(self, mock_resolve) -> None:
        mock_resolve.return_value = ()
        with pytest.raises(PermissionDeniedError, match="could not resolve"):
            ensure_public_remote_target("http://new-host.example.com/path")

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_allows_multiple_public_addresses(self, mock_resolve) -> None:
        mock_resolve.return_value = ("8.8.8.8", "8.8.4.4")
        assert ensure_public_remote_target("http://dns-rr.example.com/path") == (
            "8.8.8.8",
            "8.8.4.4",
        )

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_preserves_both_address_families(self, mock_resolve) -> None:
        mock_resolve.return_value = ("2606:4700:4700::1111", "1.1.1.1")
        assert ensure_public_remote_target("https://dual-stack.example/") == (
            "2606:4700:4700::1111",
            "1.1.1.1",
        )


class TestNormalizeVerifiedAddresses:
    def test_accepts_legacy_single_address_result(self) -> None:
        assert normalize_verified_addresses("8.8.8.8") == ("8.8.8.8",)

    def test_canonicalizes_and_deduplicates_addresses(self) -> None:
        assert normalize_verified_addresses(("2606:4700:4700:0::1111", "2606:4700:4700::1111")) == (
            "2606:4700:4700::1111",
        )

    def test_rejects_empty_address_collection(self) -> None:
        with pytest.raises(PermissionDeniedError, match="no verified addresses"):
            normalize_verified_addresses(())

    def test_rejects_non_ip_address(self) -> None:
        with pytest.raises(PermissionDeniedError, match="invalid address"):
            normalize_verified_addresses(("example.com",))

    def test_rejects_non_string_address(self) -> None:
        with pytest.raises(PermissionDeniedError, match="invalid address"):
            normalize_verified_addresses((8,))  # type: ignore[list-item]


# ── build_httpx_request_validation_hooks ─────────────────────────────────────


class TestBuildHttpxRequestValidationHooks:
    """Verify httpx hook construction."""

    def test_returns_none_when_no_validator(self) -> None:
        assert build_httpx_request_validation_hooks(None) is None

    def test_returns_request_hook_dict(self) -> None:
        def dummy_validator(url: str) -> None:
            pass

        hooks = build_httpx_request_validation_hooks(dummy_validator)
        assert hooks is not None
        assert "request" in hooks
        assert len(hooks["request"]) == 1

    @pytest.mark.asyncio
    async def test_hook_calls_validator_with_url(self) -> None:
        calls: list[str] = []

        def tracking_validator(url: str) -> None:
            calls.append(url)

        hooks = build_httpx_request_validation_hooks(tracking_validator)
        assert hooks is not None

        mock_request = AsyncMock()
        mock_request.url = "http://example.com/test"

        hook_fn = hooks["request"][0]
        await hook_fn(mock_request)

        assert calls == ["http://example.com/test"]

    @pytest.mark.asyncio
    async def test_hook_propagates_validator_exception(self) -> None:
        def failing_validator(url: str) -> None:
            raise PermissionDeniedError("blocked")

        hooks = build_httpx_request_validation_hooks(failing_validator)
        assert hooks is not None

        mock_request = AsyncMock()
        mock_request.url = "http://evil.com"

        with pytest.raises(PermissionDeniedError, match="blocked"):
            await hooks["request"][0](mock_request)


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "url": str(request.url),
                "host": request.headers["host"],
                "sni_hostname": request.extensions.get("sni_hostname"),
            }
        )
        return httpx.Response(200, request=request, content=b"ok")

    async def aclose(self) -> None:
        self.closed = True


class _ReadFailureRecordingTransport(_RecordingTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await super().handle_async_request(request)
        raise httpx.ReadError("response interrupted", request=request)


class _RecordingHTTPStream(httpcore.AsyncMockStream):
    def __init__(self) -> None:
        super().__init__([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"])
        self.writes: list[bytes] = []
        self.server_hostname: str | None = None

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)

    @property
    def closed(self) -> bool:
        return self._closed

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.server_hostname = server_hostname
        return self


class _ScriptedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        reachable_addresses: set[str],
        *,
        immediate_failures: set[str] | None = None,
    ) -> None:
        self.reachable_addresses = reachable_addresses
        self.immediate_failures = immediate_failures or set()
        self.attempts: list[str] = []
        self.cancelled: list[str] = []
        self.streams: list[_RecordingHTTPStream] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.attempts.append(host)
        if host in self.immediate_failures:
            raise httpcore.ConnectError("address refused connection")
        if host in self.reachable_addresses:
            await asyncio.sleep(0)
            stream = _RecordingHTTPStream()
            self.streams.append(stream)
            return stream
        try:
            await asyncio.sleep(timeout if timeout is not None else 60.0)
        except asyncio.CancelledError:
            self.cancelled.append(host)
            raise
        raise httpcore.ConnectTimeout("address unavailable")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("Unix sockets are not used by this transport")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class TestValidatedAsyncHTTPTransport:
    def test_builder_returns_none_without_validator(self) -> None:
        assert build_httpx_secure_transport(None) is None

    def test_pinned_transport_rejects_a_hostname_address(self) -> None:
        with pytest.raises(ValueError, match="not an IP literal"):
            PinnedAddressHTTPTransport(("example.com",))

    @pytest.mark.asyncio
    async def test_validates_before_delegating_the_original_request_once(self) -> None:
        inner = _RecordingTransport()
        validated: list[str] = []
        transport = ValidatedAsyncHTTPTransport(
            lambda url: validated.append(url) or "8.8.8.8",
            transport=inner,
        )
        request = httpx.Request("GET", "https://example.com/private")

        await transport.handle_async_request(request)

        assert validated == ["https://example.com/private"]
        assert inner.requests == [
            {
                "url": "https://example.com/private",
                "host": "example.com",
                "sni_hostname": None,
            }
        ]
        assert str(request.url) == "https://example.com/private"

    @pytest.mark.asyncio
    async def test_validates_and_pins_each_redirect_request_independently(self) -> None:
        inner = _RecordingTransport()
        validated = []

        def validator(url: str) -> str:
            validated.append(url)
            return "8.8.8.8" if "first.example" in url else "1.1.1.1"

        transport = ValidatedAsyncHTTPTransport(validator, transport=inner)
        await transport.handle_async_request(httpx.Request("GET", "https://first.example/"))
        await transport.handle_async_request(httpx.Request("GET", "https://second.example/next"))

        assert validated == ["https://first.example/", "https://second.example/next"]
        assert [request["url"] for request in inner.requests] == [
            "https://first.example/",
            "https://second.example/next",
        ]

    @pytest.mark.asyncio
    async def test_blackholed_first_address_does_not_block_reachable_second_address(self) -> None:
        backend = _ScriptedNetworkBackend({"2001:4860:4860::8888"})
        transport = PinnedAddressHTTPTransport(
            ("192.0.2.1", "2001:4860:4860::8888"),
            network_backend=backend,
        )

        started_at = time.monotonic()
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(1.0, connect=0.1),
        ) as client:
            response = await client.get("https://dual-stack.example/resource")
        elapsed = time.monotonic() - started_at

        assert response.status_code == 200
        assert response.text == "ok"
        assert elapsed < 0.1
        assert backend.attempts == ["192.0.2.1", "2001:4860:4860::8888"]
        assert backend.cancelled == ["192.0.2.1"]
        assert len(backend.streams) == 1
        assert backend.streams[0].server_hostname == "dual-stack.example"
        request_bytes = b"".join(backend.streams[0].writes)
        assert request_bytes.count(b"GET /resource HTTP/1.1") == 1
        assert b"host: dual-stack.example" in request_bytes.lower()

    @pytest.mark.asyncio
    async def test_all_blackholed_addresses_share_one_connect_deadline(self) -> None:
        backend = _ScriptedNetworkBackend(set())
        transport = PinnedAddressHTTPTransport(
            ("192.0.2.1", "192.0.2.2", "192.0.2.3"),
            network_backend=backend,
        )

        started_at = time.monotonic()
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(1.0, connect=0.05),
        ) as client:
            with pytest.raises(httpx.ConnectTimeout):
                await client.get("https://multi-address.example/resource")
        elapsed = time.monotonic() - started_at

        assert elapsed < 0.12
        assert backend.attempts == ["192.0.2.1", "192.0.2.2", "192.0.2.3"]

    @pytest.mark.asyncio
    async def test_immediate_failure_starts_next_address_without_stagger_delay(self) -> None:
        backend = _ScriptedNetworkBackend(
            {"2001:4860:4860::8888"},
            immediate_failures={"192.0.2.1"},
        )
        transport = PinnedAddressHTTPTransport(
            ("192.0.2.1", "2001:4860:4860::8888"),
            network_backend=backend,
        )

        started_at = time.monotonic()
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://dual-stack.example/resource")
        elapsed = time.monotonic() - started_at

        assert response.status_code == 200
        assert elapsed < 0.1
        assert backend.attempts == ["192.0.2.1", "2001:4860:4860::8888"]

    @pytest.mark.asyncio
    async def test_racing_connections_sends_a_post_request_only_once(self) -> None:
        backend = _ScriptedNetworkBackend({"192.0.2.1", "2001:4860:4860::8888"})
        transport = PinnedAddressHTTPTransport(
            ("192.0.2.1", "2001:4860:4860::8888"),
            network_backend=backend,
            stagger_delay=0.0,
        )

        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(
                "https://dual-stack.example/resource",
                content=b"payload",
            )

            assert response.status_code == 200
            assert len(backend.streams) == 2
            written_streams = [stream for stream in backend.streams if stream.writes]
            assert len(written_streams) == 1
            request_bytes = b"".join(written_streams[0].writes)
            assert request_bytes.count(b"POST /resource HTTP/1.1") == 1
            assert request_bytes.count(b"payload") == 1
            loser_streams = [stream for stream in backend.streams if not stream.writes]
            assert len(loser_streams) == 1
            assert loser_streams[0].closed is True

    @pytest.mark.asyncio
    async def test_cancelling_request_cancels_all_connection_attempts(self) -> None:
        backend = _ScriptedNetworkBackend(set())
        transport = PinnedAddressHTTPTransport(
            ("192.0.2.1", "2001:4860:4860::8888"),
            network_backend=backend,
            stagger_delay=0.0,
        )
        async with httpx.AsyncClient(transport=transport, timeout=None) as client:
            request_task = asyncio.create_task(client.get("https://dual-stack.example/resource"))
            while len(backend.attempts) < 2:
                await asyncio.sleep(0)

            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task

        assert sorted(backend.cancelled) == ["192.0.2.1", "2001:4860:4860::8888"]

    @pytest.mark.asyncio
    async def test_does_not_retry_after_a_non_connection_failure(self) -> None:
        inner = _ReadFailureRecordingTransport()
        transport = ValidatedAsyncHTTPTransport(
            lambda _url: ("192.0.2.1", "2001:4860:4860::8888"),
            transport=inner,
        )
        request = httpx.Request("POST", "https://dual-stack.example/resource")

        with pytest.raises(httpx.ReadError, match="response interrupted"):
            await transport.handle_async_request(request)

        assert [recorded["url"] for recorded in inner.requests] == [
            "https://dual-stack.example/resource"
        ]
        assert str(request.url) == "https://dual-stack.example/resource"

    @pytest.mark.asyncio
    async def test_httpx_redirects_keep_original_urls_while_each_hop_is_pinned(self) -> None:
        connected_urls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            connected_urls.append(str(request.url))
            if request.url.host == "first.example":
                return httpx.Response(
                    302,
                    headers={"location": "https://second.example/final"},
                )
            return httpx.Response(200, content=b"ok")

        def validator(url: str) -> str:
            return "8.8.8.8" if "first.example" in url else "1.1.1.1"

        transport = ValidatedAsyncHTTPTransport(
            validator,
            transport=httpx.MockTransport(handler),
        )
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=True,
        ) as client:
            response = await client.get("https://first.example/start")

        assert connected_urls == [
            "https://first.example/start",
            "https://second.example/final",
        ]
        assert str(response.url) == "https://second.example/final"

    @pytest.mark.asyncio
    @patch("openviking.utils.network_guard._resolve_host_addresses")
    async def test_rejects_private_redirect_target_before_delegating(self, mock_resolve) -> None:
        mock_resolve.return_value = {"169.254.169.254"}
        inner = _RecordingTransport()
        transport = ValidatedAsyncHTTPTransport(
            ensure_public_remote_target,
            transport=inner,
        )

        with pytest.raises(PermissionDeniedError, match="non-public address"):
            await transport.handle_async_request(
                httpx.Request("GET", "http://redirected.example/latest/meta-data/")
            )

        assert inner.requests == []

    @pytest.mark.asyncio
    async def test_closes_wrapped_transport(self) -> None:
        inner = _RecordingTransport()
        transport = ValidatedAsyncHTTPTransport(lambda _url: "8.8.8.8", transport=inner)

        await transport.aclose()

        assert inner.closed is True
