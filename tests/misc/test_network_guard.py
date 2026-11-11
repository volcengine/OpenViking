# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for network_guard SSRF protection utilities."""

from __future__ import annotations

import asyncio
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from openviking.parse.network import (
    ParserNetworkContext,
    ParserNetworkOperation,
    create_parser_httpx_client,
    open_parser_url,
    register_parser_network_adapter,
    reset_parser_network_adapter,
)
from openviking.utils.network_guard import (
    _get_allowed_code_hosting_domains,
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

    def test_generic_code_hosting_domains_are_allowlisted(self) -> None:
        domains = [
            "gitcode.example.com",
            "gitee.example.com",
            "bitbucket.example.com",
            "codeberg.example.com",
            "gitea.example.com",
            "atomgit.example.com",
            "sourcehut.example.com",
        ]
        code_config = SimpleNamespace(code_hosting_domains=domains)
        config = SimpleNamespace(code=code_config)

        with patch(
            "openviking.utils.network_guard.get_openviking_config",
            return_value=config,
        ):
            assert _get_allowed_code_hosting_domains() == set(domains)

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

    @pytest.mark.parametrize("override", ["none", "code_host", "private_networks"])
    @pytest.mark.parametrize(
        ("resolved_ip", "private_allowed"),
        [
            ("127.0.0.1", False),
            ("10.0.0.1", True),
            ("172.16.0.1", True),
            ("192.168.1.1", True),
            ("0.0.0.0", False),
            ("::1", False),
            ("fe80::1", False),
            ("fc00::1", True),
            ("::ffff:127.0.0.1", False),
            ("::ffff:10.0.0.1", True),
            ("169.254.169.254", False),
            ("100.100.100.200", False),
            ("fd00:ec2::254", False),
            ("224.0.0.1", False),
        ],
    )
    def test_enforces_resolved_address_policy(
        self, monkeypatch, resolved_ip, private_allowed, override
    ):
        host = "git.example.com"
        monkeypatch.setattr(
            "openviking.utils.network_guard._resolve_host_addresses", lambda _host: {resolved_ip}
        )
        monkeypatch.setattr(
            "openviking.utils.network_guard._get_allowed_code_hosting_domains",
            lambda: {host} if override == "code_host" else set(),
        )
        monkeypatch.setattr(
            "openviking.utils.network_guard._is_allow_private_networks",
            lambda: override == "private_networks",
        )
        if private_allowed and override != "none":
            ensure_public_remote_target(f"https://{host}/org/repo")
        else:
            with pytest.raises(PermissionDeniedError, match="non-public address"):
                ensure_public_remote_target(f"https://{host}/org/repo")

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
    def test_allows_azure_devops_domain_from_platform_specific_config(self, mock_resolve) -> None:
        mock_resolve.return_value = {"10.0.0.1"}
        ensure_public_remote_target("git@ssh.dev.azure.com:v3/org/project/repo")  # should not raise

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_rejects_when_dns_returns_empty(self, mock_resolve) -> None:
        """Unresolvable host cannot be verified as public."""
        mock_resolve.return_value = set()
        with pytest.raises(PermissionDeniedError, match="could not be resolved"):
            ensure_public_remote_target("http://new-host.example.com/path")

    @patch("openviking.utils.network_guard._resolve_host_addresses")
    def test_allows_multiple_public_addresses(self, mock_resolve) -> None:
        mock_resolve.return_value = {"8.8.8.8", "8.8.4.4"}
        ensure_public_remote_target("http://dns-rr.example.com/path")  # should not raise


# ── build_httpx_request_validation_hooks ─────────────────────────────────────


class TestBuildHttpxRequestValidationHooks:
    """Verify optional validation and enforcement during parser downloads."""

    def test_returns_none_when_no_validator(self) -> None:
        assert build_httpx_request_validation_hooks(None) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("client_kind", ["httpx", "urllib"])
    @pytest.mark.parametrize("use_adapter", [False, True])
    async def test_parser_adapter_preserves_redirect_validation(
        self, monkeypatch, client_kind, use_adapter
    ):
        validated = []
        downloaded = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                downloaded.append(self.path)
                if self.path == "/archive":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"archive bytes")
                else:
                    self.send_response(302)
                    target = "/archive" if self.path == "/good" else "/admin"
                    self.send_header("Location", target)
                    self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        source = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setenv("no_proxy", "*")

        def validate(url):
            validated.append(url)
            if url == source + "/admin":
                raise PermissionDeniedError("blocked")

        if use_adapter:
            register_parser_network_adapter(
                SimpleNamespace(
                    supports=lambda context: True,
                    create_httpx_transport=lambda context: httpx.AsyncHTTPTransport(),
                    create_urllib_opener=lambda context: urllib.request.build_opener(
                        urllib.request.ProxyHandler({})
                    ),
                )
            )

        async def fetch(path):
            url = source + path
            if client_kind == "httpx":
                async with create_parser_httpx_client(
                    ParserNetworkContext(ParserNetworkOperation.HTTP_RESOURCE, url),
                    follow_redirects=True,
                    trust_env=False,
                    event_hooks=build_httpx_request_validation_hooks(validate),
                ) as client:
                    return (await client.get(url)).content

            def fetch_sync():
                with open_parser_url(
                    ParserNetworkContext(ParserNetworkOperation.GIT_ARCHIVE, url),
                    url,
                    request_validator=validate,
                    timeout=5,
                ) as response:
                    return response.read()

            return await asyncio.to_thread(fetch_sync)

        try:
            assert await fetch("/good") == b"archive bytes"
            assert validated == [source + "/good", source + "/archive"]
            assert downloaded == ["/good", "/archive"]
            validated.clear()
            downloaded.clear()
            with pytest.raises(PermissionDeniedError, match="blocked"):
                await fetch("/document.txt")
            assert validated == [source + "/document.txt", source + "/admin"]
            assert downloaded == ["/document.txt"]
        finally:
            reset_parser_network_adapter()
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)
