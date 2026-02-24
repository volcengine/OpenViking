# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for identity types (openviking/server/identity.py)."""

from openviking.server.identity import RequestContext, ResolvedIdentity, Role
from openviking_cli.session.user_id import UserIdentifier


def test_role_values():
    """Role enum should have correct string values."""
    assert Role.ROOT.value == "root"
    assert Role.ADMIN.value == "admin"
    assert Role.USER.value == "user"


def test_role_from_string():
    """Role should be constructable from string."""
    assert Role("root") == Role.ROOT
    assert Role("admin") == Role.ADMIN
    assert Role("user") == Role.USER


def test_resolved_identity_defaults():
    """ResolvedIdentity optional fields should default to None."""
    identity = ResolvedIdentity(role=Role.ROOT)
    assert identity.role == Role.ROOT
    assert identity.account_id is None
    assert identity.user_id is None
    assert identity.agent_id is None


def test_resolved_identity_with_all_fields():
    """ResolvedIdentity should hold all fields."""
    identity = ResolvedIdentity(
        role=Role.USER,
        account_id="acme",
        user_id="bob",
        agent_id="my-agent",
    )
    assert identity.role == Role.USER
    assert identity.account_id == "acme"
    assert identity.user_id == "bob"
    assert identity.agent_id == "my-agent"


def test_request_context_account_id_property():
    """RequestContext.account_id should delegate to user.account_id."""
    user = UserIdentifier("acme", "bob", "agent1")
    ctx = RequestContext(user=user, role=Role.USER)
    assert ctx.account_id == "acme"
    assert ctx.role == Role.USER
    assert ctx.user.account_id == "acme"
