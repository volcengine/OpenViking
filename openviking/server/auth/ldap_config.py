# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LDAP authentication plugin configuration."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from openviking.server.auth.identity_mapping import IdentityMappingConfig


class LDAPConfig(BaseModel):
    """LDAP authentication plugin configuration."""

    # LDAP server configuration
    host: str
    port: int = 389
    use_ssl: bool = False
    use_starttls: bool = False
    # Bind credentials for searching users
    bind_dn: Optional[str] = None
    bind_password: Optional[str] = None
    # User search configuration
    base_dn: str
    user_search_filter: str = "(uid=%s)"
    user_search_base: Optional[str] = None
    # Attribute mapping for user info
    username_attribute: str = "uid"
    email_attribute: str = "mail"
    name_attribute: str = "cn"
    memberof_attribute: str = "memberOf"
    # Direct bind pattern (alternative to search+bind)
    user_dn_pattern: Optional[str] = None
    # Identity mapping
    identity: IdentityMappingConfig = Field(default_factory=IdentityMappingConfig)
    # Optional: require root API key for admin operations
    require_root_api_key_for_admin: bool = False

    model_config = {"extra": "forbid"}

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        """Validate host."""
        v = v.strip()
        if not v:
            raise ValueError("host cannot be empty")
        return v

    @field_validator("base_dn")
    @classmethod
    def validate_base_dn(cls, v: str) -> str:
        """Validate base DN."""
        v = v.strip()
        if not v:
            raise ValueError("base_dn cannot be empty")
        return v
