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
# Keep the ordinary-content boundary aligned with Rust PROTECTED_ROOTS.
PROTECTED_ROOTS = ("privacy", "sessions")


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
class PolicyPathScope:
    path: str | None
    recursive: bool


def _scope_contains(scope: PolicyPathScope, path: str | None) -> bool:
    return scope.path == path or (
        scope.recursive
        and (scope.path is None or (path is not None and path.startswith(scope.path + "/")))
    )


def _scope_intersection(a: PolicyPathScope, b: PolicyPathScope) -> PolicyPathScope | None:
    if a.path == b.path:
        return PolicyPathScope(a.path, a.recursive and b.recursive)
    if a.recursive and _scope_contains(a, b.path):
        return b
    if b.recursive and _scope_contains(b, a.path):
        return a
    return None


def _finish_scopes(
    includes: list[PolicyPathScope], excludes: list[PolicyPathScope]
) -> PolicyScopePlan:
    exact = {s.path for s in excludes if not s.recursive}
    recursive = {s.path for s in excludes if s.recursive}

    def covered(scope: PolicyPathScope) -> bool:
        if None in recursive or scope.path in recursive:
            return True
        if not scope.recursive and scope.path in exact:
            return True
        parts = scope.path.split("/") if scope.path is not None else []
        return any("/".join(parts[:i]) in recursive for i in range(1, len(parts)))

    def ordered(scopes):
        return tuple(sorted(set(scopes), key=lambda s: (s.path or "", s.recursive)))

    remaining = [s for s in includes if not covered(s)]
    if not remaining:
        return PolicyScopePlan("denied", (), ())
    return PolicyScopePlan("allowed", ordered(remaining), ordered(excludes))


@dataclass(frozen=True)
class PolicyScopePlan:
    status: Literal["allowed", "denied"]
    includes: tuple[PolicyPathScope, ...]
    excludes: tuple[PolicyPathScope, ...]

    def allows(self, path: str) -> bool:
        """Membership requires both the includes and excludes.

        Paths the policy grammar cannot represent fail closed, matching
        ``is_global_policy_path_allowed``, so enumeration never aborts on an
        ordinary filename the validator rejects.
        """
        try:
            validate_global_policy_path(path)
        except GlobalPolicyError:
            return False
        return (
            self.status == "allowed"
            and any(_scope_contains(s, path) for s in self.includes)
            and not any(_scope_contains(s, path) for s in self.excludes)
        )

    def restrict_scope(self, path: str | None, *, recursive: bool = True) -> PolicyScopePlan:
        request = _validated_scope(path, recursive)
        if self.status != "allowed":
            return PolicyScopePlan("denied", (), ())
        return _finish_scopes(
            [s for item in self.includes if (s := _scope_intersection(item, request)) is not None],
            [s for item in self.excludes if (s := _scope_intersection(item, request)) is not None],
        )

    def has_allowed_descendant(self, path: str | None) -> bool:
        try:
            restricted = self.restrict_scope(path)
        except GlobalPolicyError:
            return False
        return any(s.recursive or s.path != path for s in restricted.includes)


def _validated_scope(path: str | None, recursive: bool) -> PolicyPathScope:
    if not isinstance(recursive, bool) or (path is None and not recursive):
        raise GlobalPolicyError("The owner root requires recursive=True")
    if path is not None:
        validate_global_policy_path(path)
    return PolicyPathScope(path, recursive)


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

    def restrict_scope(self, path: str | None = None, *, recursive: bool = True) -> PolicyScopePlan:
        """Clip one user's ordinary-content scope; apply includes AND excludes.

        This computes a request plan, not filesystem existence or final ACL authorization.
        """
        request = _validated_scope(path, recursive)
        if self.status != "valid" or self._native is None:
            return PolicyScopePlan("denied", (), ())
        includes = [request] if self.default == "allow" else []
        excludes = []
        for rule in self.rules:
            if rule.effect == "allow" and self.default == "allow":
                continue
            overlap = _scope_intersection(request, PolicyPathScope(rule.path, rule.recursive))
            if overlap is not None:
                (includes if rule.effect == "allow" else excludes).append(overlap)
        for root in PROTECTED_ROOTS:
            overlap = _scope_intersection(request, PolicyPathScope(root, True))
            if overlap is not None:
                excludes.append(overlap)
        return _finish_scopes(includes, excludes)


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
