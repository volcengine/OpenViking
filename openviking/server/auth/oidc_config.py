# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OIDC (OpenID Connect) authentication plugin configuration."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from openviking.server.auth.identity_mapping import IdentityMappingConfig


class OIDCConfig(BaseModel):
    """OIDC authentication plugin configuration."""

    # Basic OIDC configuration
    issuer: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    # Token validation
    audience: Optional[str] = None
    # JWKS configuration (auto-discovery from .well-known/openid-configuration if not set)
    jwks_uri: Optional[str] = None
    # Token location
    token_location: Literal["header", "query"] = "header"
    token_header_name: str = "Authorization"
    token_header_prefix: str = "Bearer "
    # Identity mapping
    identity: IdentityMappingConfig = Field(default_factory=IdentityMappingConfig)

    model_config = {"extra": "forbid"}

    @field_validator("issuer")
    @classmethod
    def validate_issuer(cls, v: str) -> str:
        """Validate issuer URL."""
        v = v.rstrip("/")
        if not v:
            raise ValueError("issuer cannot be empty")
        return v
