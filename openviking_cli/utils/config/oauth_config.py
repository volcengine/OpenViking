# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OAuth 2.1 configuration.

All issued tokens (access, refresh, authorization code, OTP) are opaque random
strings stored as SHA-256 hashes in ``{workspace}/oauth.db``. Access tokens
carry an ``ovat_`` prefix used as a fast-path discriminator in the bearer
auth path.
"""

from typing import Optional

from pydantic import BaseModel, Field


class OAuthConfig(BaseModel):
    """OAuth 2.1 server configuration.

    OAuth is layered on top of `AuthMode.API_KEY`: when enabled, the existing
    `Authorization: Bearer <token>` path is augmented with a prefix
    discriminator (``ovat_``) that resolves OAuth-issued tokens via SQLite
    lookup to the same `ResolvedIdentity` shape. Disabled by default — turning
    it on registers `/oauth/*` routes and the well-known metadata endpoints.
    """

    enabled: bool = Field(
        default=False,
        description="Enable OAuth 2.1 endpoints (DCR, authorize, token, well-known metadata).",
    )

    issuer: Optional[str] = Field(
        default=None,
        description=(
            "Public issuer URL (e.g. 'https://ov.example.com'). Resolution order: the "
            "OPENVIKING_PUBLIC_BASE_URL environment variable wins; otherwise this field; "
            "otherwise the request's X-Forwarded-Proto/Host headers; otherwise the Host "
            "header. Strongly recommended to set one of (env var, this field) when deployed "
            "behind a reverse proxy or any host other than localhost."
        ),
    )

    access_token_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=24 * 3600,
        description="Lifetime of issued opaque access tokens in seconds.",
    )

    refresh_token_ttl_seconds: int = Field(
        default=30 * 24 * 3600,
        ge=3600,
        le=365 * 24 * 3600,
        description="Lifetime of refresh tokens in seconds.",
    )

    auth_code_ttl_seconds: int = Field(
        default=300,
        ge=30,
        le=600,
        description="Lifetime of authorization codes in seconds (RFC 6749 recommends short).",
    )

    otp_ttl_seconds: int = Field(
        default=300,
        ge=60,
        le=600,
        description="Lifetime of one-time passcodes in seconds.",
    )

    db_filename: str = Field(
        default="oauth.db",
        description="SQLite database filename (relative to OpenVikingConfig.storage.workspace).",
    )

    model_config = {"extra": "forbid"}
