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
    """Validated parser output. The body is preserved, not sanitized or executed."""

    default: Literal["allow", "deny"]
    rules: tuple[GlobalPolicyRule, ...]
    body: str = field(repr=False)
    schema_version: int = 1
    policy_hash: str = field(default="", repr=False, compare=False)
    _native: Any = field(default=None, repr=False, compare=False)


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


def user_global_policy_from_file(fs: Any, path: str) -> Any:
    """Create a native ``UserGlobalPolicy`` that reads ``fs.read(path)`` once.

    The returned snapshot is immutable. Reuse, expiry and eviction are managed
    by the caller. A missing, unreadable or invalid policy denies every path.
    """
    NativePolicy, NativeInvalid, NativeUnsupported = _native_types()
    try:
        return NativePolicy.from_file(fs, path)
    except NativeUnsupported as exc:
        raise UnsupportedGlobalPolicySchemaError(str(exc)) from exc
    except NativeInvalid as exc:
        raise GlobalPolicyError(str(exc)) from exc


def user_global_policy_from_loader(loader: Any) -> Any:
    """Create a native ``UserGlobalPolicy`` backed by a zero-argument loader.

    ``loader`` is called once and must return the policy text as
    ``str``/``bytes`` or ``None`` when no policy exists. The returned snapshot is
    immutable; reuse and expiry are managed by the caller.
    """
    NativePolicy, NativeInvalid, NativeUnsupported = _native_types()
    try:
        return NativePolicy.from_loader(loader)
    except NativeUnsupported as exc:
        raise UnsupportedGlobalPolicySchemaError(str(exc)) from exc
    except NativeInvalid as exc:
        raise GlobalPolicyError(str(exc)) from exc


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
