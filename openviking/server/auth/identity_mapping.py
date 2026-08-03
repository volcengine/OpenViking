# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Identity mapping configuration for external auth providers (OIDC, LDAP)."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from openviking.server.identity import Role

logger = logging.getLogger(__name__)

# Mapping source types
SourceType = Literal["claim", "attribute", "dn_attribute", "fixed", "composite"]

# Account mode types
AccountMode = Literal["team", "organization"]


class MappingSource(BaseModel):
    """Source configuration for identity mapping."""

    source: SourceType
    # For claim/attribute/dn_attribute
    claim: Optional[str] = None
    attribute: Optional[str] = None
    # For fixed
    value: Optional[str] = None
    # For composite
    parts: Optional[List[Union["MappingSource", Dict[str, Any]]]] = None
    # Optional prefix
    prefix: Optional[str] = None
    # Optional regex extraction
    regex: Optional[str] = None
    regex_group: int = 1
    # Optional value mapping
    mapping: Optional[Dict[str, str]] = None
    # Optional fallback
    fallback: Optional[str] = None
    # Optional normalization (lowercase, etc.)
    normalize: Optional[Literal["lowercase", "uppercase", "trim"]] = None
    # For list sources (try claims in order)
    claims: Optional[List[str]] = None

    model_config = {"extra": "forbid"}


# Forward reference update
MappingSource.model_rebuild()


class AccountMappingConfig(BaseModel):
    """Account ID mapping configuration."""

    mode: AccountMode = "organization"
    # Delegate all other fields to MappingSource
    source: SourceType = "fixed"
    claim: Optional[str] = None
    attribute: Optional[str] = None
    value: Optional[str] = "default"
    parts: Optional[List[Union[MappingSource, Dict[str, Any]]]] = None
    prefix: Optional[str] = None
    regex: Optional[str] = None
    regex_group: int = 1
    mapping: Optional[Dict[str, str]] = None
    fallback: Optional[str] = "default"
    normalize: Optional[Literal["lowercase", "uppercase", "trim"]] = None
    claims: Optional[List[str]] = None

    model_config = {"extra": "forbid"}


class UserMappingConfig(BaseModel):
    """User ID mapping configuration."""

    source: SourceType = "claim"
    claim: Optional[str] = "sub"
    attribute: Optional[str] = "uid"
    value: Optional[str] = None
    parts: Optional[List[Union[MappingSource, Dict[str, Any]]]] = None
    prefix: Optional[str] = None
    regex: Optional[str] = None
    regex_group: int = 1
    mapping: Optional[Dict[str, str]] = None
    fallback: Optional[str] = None
    normalize: Optional[Literal["lowercase", "uppercase", "trim"]] = None
    claims: Optional[List[str]] = None

    model_config = {"extra": "forbid"}


class RoleMappingConfig(BaseModel):
    """Role mapping configuration."""

    source: Literal["claim", "attribute", "group_membership", "fixed"] = "claim"
    # For claim/attribute
    claim: Optional[str] = None
    attribute: Optional[str] = None
    # For fixed
    value: Optional[str] = Role.USER
    # For group_membership
    group_mapping: Optional[Dict[str, str]] = None
    # Value mapping
    mapping: Optional[Dict[str, str]] = None
    # Fallback
    default: str = Role.USER

    model_config = {"extra": "forbid"}


class IdentityMappingConfig(BaseModel):
    """Complete identity mapping configuration."""

    account_id: AccountMappingConfig = Field(default_factory=AccountMappingConfig)
    user_id: UserMappingConfig = Field(default_factory=UserMappingConfig)
    role: RoleMappingConfig = Field(default_factory=RoleMappingConfig)

    model_config = {"extra": "forbid"}


class IdentityMapper:
    """Identity mapper that applies mapping rules to external identities."""

    def __init__(self, config: IdentityMappingConfig):
        self.config = config

    def _extract_value(
        self,
        source: Union[MappingSource, AccountMappingConfig, UserMappingConfig],
        claims: Dict[str, Any],
        attributes: Optional[Dict[str, str]] = None,
        dn: Optional[str] = None,
        groups: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Extract value from source configuration."""
        # Handle different source types
        value: Optional[str] = None

        if source.source == "fixed":
            value = source.value if hasattr(source, "value") and source.value else None
        elif source.source == "claim":
            # Try multiple claims if provided
            if hasattr(source, "claims") and source.claims:
                for claim in source.claims:
                    if claim in claims:
                        v = claims.get(claim)
                        if v is not None:
                            value = str(v)
                            logger.debug("Found value from claim '%s': %s", claim, value[:50] if len(value) > 50 else value)
                            break
            elif hasattr(source, "claim") and source.claim:
                v = claims.get(source.claim)
                if v is not None:
                    value = str(v)
                    logger.debug("Found value from claim '%s': %s", source.claim, value[:50] if len(value) > 50 else value)
        elif source.source == "attribute" and attributes is not None:
            if hasattr(source, "attribute") and source.attribute:
                value = attributes.get(source.attribute)
                if value:
                    logger.debug("Found value from attribute '%s': %s", source.attribute, value[:50] if len(value) > 50 else value)
        elif source.source == "dn_attribute" and dn is not None:
            # Extract attribute from DN (e.g., "ou=engineering,dc=example,dc=com" -> "engineering")
            if hasattr(source, "attribute") and source.attribute:
                attr = source.attribute.lower()
                pattern = rf"{attr}=([^,]+)"
                match = re.search(pattern, dn, re.IGNORECASE)
                if match:
                    value = match.group(1)
                    logger.debug("Found value from DN attribute '%s': %s", source.attribute, value)
        elif source.source == "composite" and hasattr(source, "parts") and source.parts:
            # Build composite value from parts
            parts: List[str] = []
            for part in source.parts:
                if isinstance(part, dict) and "literal" in part:
                    parts.append(str(part["literal"]))
                else:
                    part_source = MappingSource.model_validate(part) if isinstance(part, dict) else part
                    part_value = self._extract_value(part_source, claims, attributes, dn, groups)
                    if part_value is not None:
                        parts.append(part_value)
            value = "".join(parts) if parts else None
            if value:
                logger.debug("Composite value: %s", value[:50] if len(value) > 50 else value)

        # Apply fallback if needed
        if value is None and hasattr(source, "fallback"):
            value = source.fallback
            if value:
                logger.debug("Using fallback value: %s", value)

        if value is None:
            logger.debug("No value extracted from source: %s", source.source)
            return None

        # Apply regex extraction
        if hasattr(source, "regex") and source.regex:
            try:
                match = re.search(source.regex, value)
                if match:
                    group_idx = source.regex_group if hasattr(source, "regex_group") else 1
                    if len(match.groups()) >= group_idx:
                        value = match.group(group_idx)
                        logger.debug("Applied regex, extracted: %s", value)
            except re.error as e:
                logger.warning("Invalid regex pattern '%s': %s", source.regex, e)

        # Apply value mapping
        if hasattr(source, "mapping") and source.mapping and value in source.mapping:
            old_value = value
            value = source.mapping[value]
            logger.debug("Mapped value '%s' to '%s'", old_value, value)

        # Apply prefix
        if hasattr(source, "prefix") and source.prefix:
            value = f"{source.prefix}{value}"

        # Apply normalization
        if hasattr(source, "normalize") and source.normalize and value:
            if source.normalize == "lowercase":
                value = value.lower()
            elif source.normalize == "uppercase":
                value = value.upper()
            elif source.normalize == "trim":
                value = value.strip()

        return value

    def map_account_id(
        self,
        claims: Dict[str, Any],
        attributes: Optional[Dict[str, str]] = None,
        dn: Optional[str] = None,
        groups: Optional[List[str]] = None,
    ) -> str:
        """Map account ID from external identity."""
        # Log available data for debugging
        if claims:
            logger.debug("Available claims for account_id mapping: %s", list(claims.keys()))
        if attributes:
            logger.debug("Available attributes for account_id mapping: %s", list(attributes.keys()))
        if dn:
            logger.debug("DN available for account_id mapping: %s", dn)
        
        value = self._extract_value(
            self.config.account_id, claims, attributes, dn, groups
        )
        if value is None:
            raise ValueError(
                f"Could not map account_id from external identity. "
                f"Available claims: {list(claims.keys()) if claims else []}, "
                f"Available attributes: {list(attributes.keys()) if attributes else []}"
            )
        logger.debug("Mapped account_id: %s", value)
        return value

    def map_user_id(
        self,
        claims: Dict[str, Any],
        attributes: Optional[Dict[str, str]] = None,
        dn: Optional[str] = None,
        groups: Optional[List[str]] = None,
    ) -> str:
        """Map user ID from external identity."""
        # Log available data for debugging
        if claims:
            logger.debug("Available claims for user_id mapping: %s", list(claims.keys()))
        if attributes:
            logger.debug("Available attributes for user_id mapping: %s", list(attributes.keys()))
        
        value = self._extract_value(
            self.config.user_id, claims, attributes, dn, groups
        )
        if value is None:
            raise ValueError(
                f"Could not map user_id from external identity. "
                f"Available claims: {list(claims.keys()) if claims else []}, "
                f"Available attributes: {list(attributes.keys()) if attributes else []}"
            )
        logger.debug("Mapped user_id: %s", value)
        return value

    def map_role(
        self,
        claims: Dict[str, Any],
        attributes: Optional[Dict[str, str]] = None,
        dn: Optional[str] = None,
        groups: Optional[List[str]] = None,
    ) -> Role:
        """Map role from external identity."""
        role_config = self.config.role

        if role_config.source == "fixed" and role_config.value:
            logger.debug("Using fixed role: %s", role_config.value)
            return Role(role_config.value)

        if groups:
            logger.debug("Available groups for role mapping: %s", groups[:5])  # Log first 5 groups

        value: Optional[str] = None
        if role_config.source in ("claim", "attribute"):
            if role_config.source == "claim" and role_config.claim:
                v = claims.get(role_config.claim)
                if v is not None:
                    value = str(v)
            elif role_config.source == "attribute" and attributes and role_config.attribute:
                value = attributes.get(role_config.attribute)

            if value is not None:
                if role_config.mapping and value in role_config.mapping:
                    role_str = role_config.mapping[value]
                    logger.debug("Mapped role from value '%s': %s", value, role_str)
                    return Role(role_str)
                # Try direct role name
                try:
                    logger.debug("Trying direct role name: %s", value.lower())
                    return Role(value.lower())
                except (ValueError, AttributeError):
                    logger.debug("Direct role mapping failed for value '%s'", value)
                    pass

        if role_config.source == "group_membership" and groups and role_config.group_mapping:
            # Check group membership in order
            logger.debug("Checking group membership for role mapping")
            for group, role_str in role_config.group_mapping.items():
                if group in groups:
                    logger.debug("Found role '%s' from group membership: %s", role_str, group)
                    return Role(role_str)

        logger.debug("Using default role: %s", role_config.default)
        return Role(role_config.default)
