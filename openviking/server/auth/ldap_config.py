# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LDAP authentication plugin configuration."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from openviking.server.auth.identity_mapping import (
    IdentityMappingConfig,
    UserMappingConfig,
)


def _ldap_default_identity() -> IdentityMappingConfig:
    """LDAP-specific default identity mapping.

    LDAP entries carry directory attributes rather than JWT claims, so the
    generic ``IdentityMappingConfig`` defaults (``user_id <- claim:sub``)
    cannot work. The documented out-of-the-box contract is:

    * ``account_id`` -> fixed "default"
    * ``user_id``    -> LDAP attribute ``uid``

    Operators can override any field via the ``identity`` config block.
    """
    return IdentityMappingConfig(
        user_id=UserMappingConfig(
            source="attribute",
            claim=None,
            attribute="uid",
        ),
    )


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
    # Direct bind pattern (alternative to search+bind)
    user_dn_pattern: Optional[str] = None
    # Identity mapping (LDAP-specific defaults: user_id <- uid, role=user)
    identity: IdentityMappingConfig = Field(
        default_factory=_ldap_default_identity
    )

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
