# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for account namespace policy and canonical URI resolution."""

from openviking.core.namespace import NamespacePolicy, NamespaceResolver
from openviking_cli.session.user_id import UserIdentifier


def _user() -> UserIdentifier:
    return UserIdentifier("acct", "alice", "bot")


def test_default_namespace_roots_are_account_shared_for_session() -> None:
    user = _user()
    resolver = NamespaceResolver(NamespacePolicy())

    assert user.canonical_user_root(resolver.policy) == "viking://user/alice"
    assert user.canonical_agent_root(resolver.policy) == "viking://agent/bot"
    assert resolver.session_root("sess-001") == "viking://session/sess-001"


def test_user_scope_can_nest_by_agent() -> None:
    user = _user()
    resolver = NamespaceResolver(NamespacePolicy(isolate_user_scope_by_agent=True))

    assert resolver.canonicalize_uri("viking://user/memories/preferences", user) == (
        "viking://user/alice/agent/bot/memories/preferences"
    )
    assert resolver.canonicalize_uri("viking://user/alice", user) == "viking://user/alice/agent/bot"


def test_agent_scope_canonicalizes_legacy_hash_root() -> None:
    user = _user()
    resolver = NamespaceResolver(NamespacePolicy())

    legacy_uri = f"viking://agent/{user.agent_space_name()}/memories/cases"
    assert resolver.canonicalize_uri(legacy_uri, user) == "viking://agent/bot/memories/cases"


def test_agent_scope_can_nest_by_user() -> None:
    user = _user()
    resolver = NamespaceResolver(NamespacePolicy(isolate_agent_scope_by_user=True))

    assert resolver.canonicalize_uri("viking://agent/skills", user) == (
        "viking://agent/bot/user/alice/skills"
    )
    assert resolver.canonicalize_uri("viking://agent/bot", user) == "viking://agent/bot/user/alice"


def test_session_legacy_user_scoped_uri_is_canonicalized() -> None:
    user = _user()
    resolver = NamespaceResolver(NamespacePolicy())

    assert resolver.canonicalize_uri(
        "viking://session/alice/sess-001/history/archive_001",
        user,
    ) == "viking://session/sess-001/history/archive_001"


def test_session_scope_is_account_visible() -> None:
    alice = UserIdentifier("acct", "alice", "bot")
    bob = UserIdentifier("acct", "bob", "bot")
    resolver = NamespaceResolver(NamespacePolicy())

    assert resolver.is_visible("viking://session/shared-001", alice) is True
    assert resolver.is_visible("viking://session/shared-001", bob) is True
    assert resolver.is_visible("viking://user/alice/memories/preferences", bob) is False
