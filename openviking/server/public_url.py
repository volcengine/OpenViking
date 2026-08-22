# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Validation and resolution for URLs published by the server.

Public deployments must not derive security-sensitive URLs from request
headers.  This module keeps the small amount of parsing shared by the app,
MCP endpoint, and OAuth metadata routes in one place.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from openviking.server.config import ServerConfig


PUBLIC_BASE_URL_ENV = "OPENVIKING_PUBLIC_BASE_URL"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_loopback_bind_host(host: str) -> bool:
    """Return whether a bind host is restricted to the local machine."""
    return str(host or "").strip().lower().strip("[]") in _LOOPBACK_HOSTS


def validate_http_origin(value: str, *, field_name: str, require_https: bool) -> str:
    """Normalize an absolute origin, rejecting paths, credentials, and wildcards."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} must not be empty")
    if "*" in raw:
        raise ValueError(f"{field_name} must not contain a wildcard")

    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must use http or https")
    if require_https and parsed.scheme != "https":
        raise ValueError(f"{field_name} must use https for a public deployment")
    if not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{field_name} must be an absolute origin without credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not include a path, query, or fragment")
    if not parsed.hostname:
        raise ValueError(f"{field_name} must include a host")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def resolve_configured_public_base_url(
    config: "ServerConfig", *, environ: Optional[dict[str, str]] = None
) -> tuple[Optional[str], Optional[str]]:
    """Resolve the explicit public URL only; never inspect request headers."""
    environment = os.environ if environ is None else environ
    value = str(environment.get(PUBLIC_BASE_URL_ENV, "") or "").strip()
    source = "env" if value else None
    if not value:
        value = str(config.public_base_url or "").strip()
        source = "config" if value else None
    if not value:
        return None, None
    return (
        validate_http_origin(
            value,
            field_name=(PUBLIC_BASE_URL_ENV if source == "env" else "server.public_base_url"),
            require_https=not is_loopback_bind_host(config.host),
        ),
        source,
    )


def validate_public_deployment_config(config: "ServerConfig", oauth_config: object = None) -> None:
    """Fail closed for externally reachable server and OAuth URL settings."""
    is_public = not is_loopback_bind_host(config.host)
    public_base_url, _ = resolve_configured_public_base_url(config)

    origins = list(config.cors_origins)
    if is_public and not origins:
        raise ValueError("server.cors_origins must list explicit origins for a public deployment")
    for index, origin in enumerate(origins):
        # Keep existing localhost development configurations working while the
        # default remains an empty allowlist. Wildcards are never accepted once
        # the server can receive non-local traffic.
        if not is_public and origin == "*":
            continue
        validate_http_origin(
            origin,
            field_name=f"server.cors_origins[{index}]",
            require_https=False,
        )
    if is_public and public_base_url is None:
        raise ValueError(
            "server.public_base_url or OPENVIKING_PUBLIC_BASE_URL is required for a public deployment"
        )

    if oauth_config is None or not getattr(oauth_config, "enabled", False):
        return
    issuer = str(getattr(oauth_config, "issuer", "") or "").strip()
    if not issuer:
        return
    normalized_issuer = validate_http_origin(
        issuer,
        field_name="oauth.issuer",
        require_https=is_public,
    )
    if public_base_url is not None and normalized_issuer != public_base_url:
        raise ValueError(
            "oauth.issuer must match the effective configured public base URL when OAuth is enabled"
        )
