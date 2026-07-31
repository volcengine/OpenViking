# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for identity types (openviking/server/identity.py)."""

from openviking.server.identity import (
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


def test_role_from_string():
    """Role should be constructable from string."""
    assert Role("root") == Role.ROOT
    assert Role("admin") == Role.ADMIN
    assert Role("user") == Role.USER


def test_custom_role_value():
    """Custom roles should behave like strings and support registered ranks."""
    role = Role("reviewer")
    assert str(role) == "reviewer"
    assert role == "reviewer"

    try:
        Role.register("reviewer", 1)
        assert Role("reviewer").rank == 1
    finally:
        Role._CUSTOM_RANK.pop("reviewer", None)


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
