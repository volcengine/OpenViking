# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for MCP upload URL host trust."""

from __future__ import annotations

from openviking.server import dependencies
from openviking.server.config import ServerConfig
from openviking.server.mcp_endpoint import (
    _is_loopback_authority,
    _request_url_ctx,
    _resolve_public_base_url,
)


def _set_config(monkeypatch, **kwargs) -> ServerConfig:
    config = ServerConfig(**kwargs)
    monkeypatch.setattr(dependencies, "_server_config", config)
    return config


def test_mcp_upload_url_does_not_trust_forwarded_host_without_public_base_url(monkeypatch):
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)
    _set_config(monkeypatch, host="127.0.0.1", port=1933, public_base_url=None)
    token = _request_url_ctx.set(
        {
            "x_forwarded_proto": "https",
            "x_forwarded_host": "attacker.example",
            "host": "openviking.example",
        }
    )
    try:
        base_url, source = _resolve_public_base_url()
    finally:
        _request_url_ctx.reset(token)

    assert base_url == "http://127.0.0.1:1933"
    assert source == "listen"


def test_mcp_upload_url_does_not_trust_external_host_header_without_public_base_url(monkeypatch):
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)
    _set_config(monkeypatch, host="0.0.0.0", port=1933, public_base_url=None)
    token = _request_url_ctx.set(
        {
            "x_forwarded_proto": None,
            "x_forwarded_host": None,
            "host": "attacker.example",
        }
    )
    try:
        base_url, source = _resolve_public_base_url()
    finally:
        _request_url_ctx.reset(token)

    assert base_url == "http://0.0.0.0:1933"
    assert source == "listen"


def test_mcp_upload_url_keeps_explicit_public_base_url_authoritative(monkeypatch):
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)
    _set_config(monkeypatch, public_base_url="https://openviking.example")
    token = _request_url_ctx.set(
        {
            "x_forwarded_proto": "https",
            "x_forwarded_host": "attacker.example",
            "host": "attacker.example",
        }
    )
    try:
        base_url, source = _resolve_public_base_url()
    finally:
        _request_url_ctx.reset(token)

    assert base_url == "https://openviking.example"
    assert source == "config"


def test_mcp_upload_url_allows_loopback_host_for_local_clients(monkeypatch):
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)
    _set_config(monkeypatch, host="127.0.0.1", port=1933, public_base_url=None)
    token = _request_url_ctx.set(
        {
            "x_forwarded_proto": None,
            "x_forwarded_host": None,
            "host": "localhost:1933",
        }
    )
    try:
        base_url, source = _resolve_public_base_url()
    finally:
        _request_url_ctx.reset(token)

    assert base_url == "http://localhost:1933"
    assert source == "host"


def test_loopback_authority_rejects_url_authority_confusion():
    assert _is_loopback_authority("localhost:1933")
    assert _is_loopback_authority("127.0.0.1:1933")
    assert _is_loopback_authority("[::1]:1933")
    assert not _is_loopback_authority("localhost:1933@attacker.example")
    assert not _is_loopback_authority("localhost/path")
    assert not _is_loopback_authority("localhost?next=attacker")
    assert not _is_loopback_authority("localhost#attacker")
    assert not _is_loopback_authority("localhost:not-a-port")
    assert not _is_loopback_authority("[::1]extra")
