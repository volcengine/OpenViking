# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Canonical owner-bound references to individual privacy fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

from openviking.core.identifiers import validate_user_id
from openviking.privacy.validation import (
    validate_privacy_field_name,
    validate_privacy_identifier,
)

_REFERENCE_RE = re.compile(
    r"^\{\{[ \t]*ov_privacy:(?P<owner>[^\s:{}]+):viking://~/privacy/"
    r"(?P<category>[^/\s]+)/(?P<target>[^#\s]+)#/values/"
    r"(?P<field>[^}\s]+)[ \t]*\}\}$"
)
_FIELD_URI_RE = re.compile(
    r"^viking://(?P<scope>~|user/(?P<owner>[^/]+))/privacy/"
    r"(?P<category>[^/]+)/(?P<target>[^#]+)#/values/(?P<field>.+)$"
)
_TARGET_URI_RE = re.compile(
    r"^viking://(?P<scope>~|user/(?P<owner>[^/]+))/privacy/"
    r"(?P<category>[^/]+)/(?P<target>[^#]+)$"
)


@dataclass(frozen=True)
class PrivacyFieldReference:
    owner_user_id: str
    category: str
    target_key: str
    field: str

    def render(self) -> str:
        return build_privacy_field_reference(
            self.owner_user_id,
            self.category,
            self.target_key,
            self.field,
        )


@dataclass(frozen=True)
class PrivacyTargetReference:
    owner_user_id: str
    category: str
    target_key: str


def build_privacy_field_reference(
    owner_user_id: str,
    category: str,
    target_key: str,
    field: str,
) -> str:
    """Build the canonical reference without exposing the referenced value."""
    validation_error = validate_user_id(owner_user_id)
    if validation_error:
        raise ValueError(validation_error)
    validate_privacy_identifier(category, "category")
    validate_privacy_identifier(target_key, "target_key")
    validate_privacy_field_name(field, location="field")

    return (
        f"{{{{ov_privacy:{owner_user_id}:viking://~/privacy/"
        f"{quote(category, safe='')}/{quote(target_key, safe='')}"
        f"#/values/{quote(field, safe='')}}}}}"
    )


def parse_privacy_field_reference(value: str) -> PrivacyFieldReference | None:
    """Parse a reference, tolerating only whitespace next to the outer braces.

    Rendering remains canonical and whitespace-free. Whitespace inside owner,
    URI, target, or field tokens is invalid and must be percent-encoded when it
    belongs to a path segment.
    """
    match = _REFERENCE_RE.fullmatch(value)
    if match is None:
        return None
    owner = match.group("owner")
    if validate_user_id(owner):
        return None
    decoded = {
        name: unquote(match.group(name))
        for name in ("category", "target", "field")
    }
    if any(not item for item in decoded.values()):
        return None
    return PrivacyFieldReference(
        owner_user_id=owner,
        category=decoded["category"],
        target_key=decoded["target"],
        field=decoded["field"],
    )


def parse_privacy_field_selector(
    value: str,
    *,
    current_user_id: str,
) -> PrivacyFieldReference | None:
    """Parse either an owner-bound reference or a field-level Privacy URI."""
    reference = parse_privacy_field_reference(value)
    if reference is not None:
        return reference
    match = _FIELD_URI_RE.fullmatch(value)
    if match is None:
        return None
    owner = current_user_id if match.group("scope") == "~" else match.group("owner")
    if not owner or validate_user_id(owner):
        return None
    return PrivacyFieldReference(
        owner_user_id=owner,
        category=unquote(match.group("category")),
        target_key=unquote(match.group("target")),
        field=unquote(match.group("field")),
    )


def parse_privacy_target_selector(
    value: str,
    *,
    current_user_id: str,
) -> PrivacyTargetReference | None:
    """Parse a target-level current-user or explicit-user Privacy URI."""
    match = _TARGET_URI_RE.fullmatch(value)
    if match is None:
        return None
    owner = current_user_id if match.group("scope") == "~" else match.group("owner")
    if not owner or validate_user_id(owner):
        return None
    return PrivacyTargetReference(
        owner_user_id=owner,
        category=unquote(match.group("category")),
        target_key=unquote(match.group("target")),
    )
