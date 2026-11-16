# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for identity types (openviking/server/identity.py)."""

from openviking.server.identity import (
    ACCOUNT_USER_ASSIGNABLE_ROLES,
    ACCOUNT_USER_CREATION_ROLES,
    RequestContext,
    ResolvedIdentity,
    Role,
)
from openviking_cli.session.user_id import UserIdentifier


def test_role_values():
    """Built-in roles should expose stable string values."""
    assert str(Role.ROOT) == "root"
    assert str(Role.ADMIN) == "admin"
    assert str(Role.USER) == "user"
    assert str(Role.GLOBAL) == "global"


def test_role_from_string():
    """Role should be constructable from string."""
    assert Role("root") == Role.ROOT
    assert Role("admin") == Role.ADMIN
    assert Role("user") == Role.USER
    assert Role("global") == Role.GLOBAL


def test_account_user_role_constants():
    """Creation and role-change targets are defined once for all providers."""
    assert ACCOUNT_USER_CREATION_ROLES == (Role.USER, Role.ADMIN, Role.GLOBAL)
    assert ACCOUNT_USER_ASSIGNABLE_ROLES == (Role.USER, Role.ADMIN)


def test_global_identity_parses_and_accepts_only_global_tokens():
    """GLOBAL is a real identity role but never crosses into other token roles."""
    assert Role.parse(Role.GLOBAL) == Role.GLOBAL
    global_role = Role.parse(Role.GLOBAL)
    assert global_role.allows_token_role(Role.GLOBAL)
    assert not global_role.allows_token_role(Role.USER)
    assert not global_role.allows_token_role(Role.ADMIN)
    assert not global_role.allows_token_role(Role.ROOT)
    assert not Role.parse(Role.USER).allows_token_role(Role.GLOBAL)
    assert not Role.parse(Role.ADMIN).allows_token_role(Role.GLOBAL)
    assert not Role.parse(Role.ROOT).allows_token_role(Role.GLOBAL)


def test_builtin_token_role_allowlist():
    """Token downgrade decisions are table lookups, not privilege ranks."""
    root_role = Role.parse(Role.ROOT)
    admin_role = Role.parse(Role.ADMIN)
    user_role = Role.parse(Role.USER)
    assert root_role.allows_token_role(Role.ROOT)
    assert root_role.allows_token_role(Role.ADMIN)
    assert root_role.allows_token_role(Role.USER)
    assert admin_role.allows_token_role(Role.ADMIN)
    assert admin_role.allows_token_role(Role.USER)
    assert not admin_role.allows_token_role(Role.ROOT)
    assert user_role.allows_token_role(Role.USER)
    assert not user_role.allows_token_role(Role.ADMIN)
    assert not hasattr(Role, "_CUSTOM_RANK")
    assert not hasattr(Role, "rank")


def test_custom_role_value():
    """Custom roles should behave like strings and be registered explicitly."""
    role = Role("reviewer")
    assert str(role) == "reviewer"
    assert role == "reviewer"

    try:
        Role.register("reviewer")
        assert Role.parse("reviewer") == "reviewer"
        assert Role("reviewer").allows_token_role("reviewer")
        assert not Role("reviewer").allows_token_role(Role.ADMIN)

        Role.register("operator", allowed_token_roles={Role.ADMIN, Role.USER})
        operator_role = Role.parse("operator")
        assert operator_role.allows_token_role("operator")
        assert operator_role.allows_token_role(Role.USER)
        assert not operator_role.allows_token_role(Role.ROOT)
    finally:
        Role._TOKEN_ROLE_ALLOWLIST.pop("reviewer", None)
        Role._TOKEN_ROLE_ALLOWLIST.pop("operator", None)


def test_resolved_identity_defaults():
    """ResolvedIdentity optional fields should default to None."""
    identity = ResolvedIdentity(role=Role.ROOT)
    assert identity.role == Role.ROOT
    assert identity.account_id is None
    assert identity.user_id is None


def test_resolved_identity_with_all_fields():
    """ResolvedIdentity should hold all fields."""
    identity = ResolvedIdentity(
        role=Role.USER,
        account_id="acme",
        user_id="bob",
    )
    assert identity.role == Role.USER
    assert identity.account_id == "acme"
    assert identity.user_id == "bob"


def test_request_context_account_id_property():
    """RequestContext.account_id should delegate to user.account_id."""
    user = UserIdentifier("acme", "bob")
    ctx = RequestContext(user=user, role=Role.USER)
    assert ctx.account_id == "acme"
    assert ctx.role == Role.USER
    assert ctx.user.account_id == "acme"
