# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for identity types (openviking/server/identity.py)."""

import pytest

from openviking.server.identity import Role


def test_global_identity_parses_and_accepts_only_global_tokens():
    """GLOBAL is a real identity role but never crosses into other token roles."""
    global_role = Role.parse("global")
    assert global_role == Role.GLOBAL
    assert global_role.allows_token_role(Role.GLOBAL)
    for role in (Role.USER, Role.ADMIN, Role.ROOT):
        assert not global_role.allows_token_role(role)
        assert not Role.parse(role).allows_token_role(Role.GLOBAL)


def test_builtin_token_role_allowlist():
    """Token downgrade decisions are table lookups, not privilege ranks."""
    permitted = {
        "root": {"root", "admin", "user"},
        "admin": {"admin", "user"},
        "user": {"user"},
    }
    assert (Role.ROOT, Role.ADMIN, Role.USER) == ("root", "admin", "user")
    for role, modes in permitted.items():
        registered = Role.parse(role)
        assert str(registered) == role
        assert {
            candidate for candidate in permitted if registered.allows_token_role(candidate)
        } == modes


def test_custom_role_value():
    """Custom roles should behave like strings and be registered explicitly."""
    role = Role("reviewer")
    assert str(role) == "reviewer"
    assert role == "reviewer"

    with pytest.raises(ValueError, match="Unknown or inactive identity role"):
        Role.parse("reviewer")

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
