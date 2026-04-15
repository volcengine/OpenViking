# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for network_guard SSRF protection utilities."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openviking.utils.network_guard import (
    _is_public_ip,
    _normalize_host,
    _resolve_host_addresses,
    build_httpx_request_validation_hooks,
    ensure_public_remote_target,
    extract_remote_host,
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
        assert result == set()

    def test_returns_empty_set_for_unicode_error(self) -> None:
        # A hostname that triggers UnicodeError in getaddrinfo
        result = _resolve_host_addresses("\udcff.invalid")
        assert result == set()

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
        assert result == set()


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
        mock_resolve.return_value = {resolved_ip}
        with pytest.raises(PermissionDeniedError, match="non-public address"):
            ensure_public_remote_target(source)

    # -- Rejection: DNS rebinding with mixed results --

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_rejects_when_any_resolved_address_is_non_public(self, mock_resolve) -> None:
        """DNS rebinding: even if some IPs are public, one private IP is enough to reject."""
        mock_resolve.return_value = {"8.8.8.8", "127.0.0.1"}
        with pytest.raises(PermissionDeniedError, match="non-public address"):
            ensure_public_remote_target("http://rebinding.attacker.com/path")

    # -- Pass-through: valid public targets --

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_allows_public_http_url(self, mock_resolve) -> None:
        mock_resolve.return_value = {"151.101.1.67"}
        ensure_public_remote_target("https://github.com/repo.git")  # should not raise

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_allows_public_git_ssh(self, mock_resolve) -> None:
        mock_resolve.return_value = {"140.82.121.4"}
        ensure_public_remote_target("git@github.com:user/repo.git")  # should not raise

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_allows_when_dns_returns_empty(self, mock_resolve) -> None:
        """Unresolvable host is allowed through (fail-open for DNS)."""
        mock_resolve.return_value = set()
        ensure_public_remote_target("http://new-host.example.com/path")  # should not raise

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_allows_multiple_public_addresses(self, mock_resolve) -> None:
        mock_resolve.return_value = {"8.8.8.8", "8.8.4.4"}
        ensure_public_remote_target("http://dns-rr.example.com/path")  # should not raise


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
