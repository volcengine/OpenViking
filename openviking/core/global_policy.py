# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Thin Python facade for the native Rust GLOBAL policy parser.

The Rust module only interprets policy documents. It does not resolve identities,
grant operations, or change existing authorization behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

GLOBAL_POLICY_INVALID = "GLOBAL_POLICY_INVALID"
GLOBAL_POLICY_SCHEMA_UNSUPPORTED = "GLOBAL_POLICY_SCHEMA_UNSUPPORTED"


class GlobalPolicyError(ValueError):
    """A policy cannot be safely interpreted and must not grant access."""

    code = GLOBAL_POLICY_INVALID


class UnsupportedGlobalPolicySchemaError(GlobalPolicyError):
    """The document uses an unsupported integer schema version."""

    code = GLOBAL_POLICY_SCHEMA_UNSUPPORTED


@dataclass(frozen=True)
class GlobalPolicyRule:
    effect: Literal["allow", "deny"]
    path: str
    recursive: bool


@dataclass(frozen=True)
class GlobalAccessPolicy:
    """Validated parser output. The body is preserved, not sanitized or executed.

    A non-valid ``status`` represents a fail-closed snapshot built from source
    that was missing, unreadable or rejected. Such snapshots deny every path.
    """

    default: Literal["allow", "deny"]
    rules: tuple[GlobalPolicyRule, ...]
    body: str = field(repr=False)
    schema_version: int = 1
    policy_hash: str = field(default="", repr=False, compare=False)
    _native: Any = field(default=None, repr=False, compare=False)
    status: str = "valid"
    last_error: str | None = None

    def allows_path(self, path: str) -> bool:
        """Evaluate one owner-relative path, failing closed for non-valid policies."""
        return is_global_policy_path_allowed(self, path)


def _native_types():
    from openviking.pyagfs import get_user_global_policy_types

    return get_user_global_policy_types()


def validate_global_policy_path(path: str) -> str:
    """Validate a canonical, literal path relative to one user's root."""
    NativePolicy, NativeInvalid, NativeUnsupported = _native_types()
    try:
        return NativePolicy.validate_path(path)
    except NativeUnsupported as exc:
        raise UnsupportedGlobalPolicySchemaError(str(exc)) from exc
    except NativeInvalid as exc:
        raise GlobalPolicyError(str(exc)) from exc


def parse_global_policy(raw: str | bytes) -> GlobalAccessPolicy:
    """Parse a complete v1 policy or raise; never return a partial policy."""
    NativePolicy, NativeInvalid, NativeUnsupported = _native_types()
    try:
        native = NativePolicy.parse(raw)
    except NativeUnsupported as exc:
        raise UnsupportedGlobalPolicySchemaError(str(exc)) from exc
    except NativeInvalid as exc:
        raise GlobalPolicyError(str(exc)) from exc
    rules = tuple(
        GlobalPolicyRule(effect=effect, path=path, recursive=recursive)
        for effect, path, recursive in native.rules
    )
    return GlobalAccessPolicy(
        default=native.default,
        rules=rules,
        body=native.body,
        schema_version=native.schema_version,
        policy_hash=native.policy_hash(),
        _native=native,
    )


def global_policy_from_source(
    raw: str | bytes | None,
    *,
    read_error: str | None = None,
) -> GlobalAccessPolicy:
    """Validate and parse policy text that the caller already read.

    This helper never touches a filesystem. Callers read the source through the
    ``VikingFS``-owned AGFS client, passing the account context required by
    encrypted backends, and hand the resulting bytes to this helper. Missing,
    unreadable, invalid and unsupported documents return a fail-closed snapshot
    instead of raising.
    """
    if read_error is not None:
        return GlobalAccessPolicy(
            default="deny",
            rules=(),
            body="",
            status="unreadable",
            last_error=f"GLOBAL_POLICY_UNAVAILABLE: {read_error}",
        )
    if raw is None:
        return GlobalAccessPolicy(
            default="deny",
            rules=(),
            body="",
            status="missing",
            last_error="GLOBAL_POLICY_MISSING: policy file not found",
        )
    try:
        return parse_global_policy(raw)
    except UnsupportedGlobalPolicySchemaError as exc:
        return GlobalAccessPolicy(
            default="deny",
            rules=(),
            body="",
            status="unsupported_schema",
            last_error=f"{exc.code}: {exc}",
        )
    except GlobalPolicyError as exc:
        return GlobalAccessPolicy(
            default="deny",
            rules=(),
            body="",
            status="invalid",
            last_error=f"{exc.code}: {exc}",
        )


def is_global_policy_path_allowed(policy: GlobalAccessPolicy | Any | None, path: str) -> bool:
    """Evaluate ordinary-content rules for one owner-relative path, failing closed."""
    if policy is None:
        return False
    try:
        validate_global_policy_path(path)
    except GlobalPolicyError:
        return False
    native = policy._native if isinstance(policy, GlobalAccessPolicy) else policy
    if native is None:
        return False
    return bool(native.allows_path(path))
