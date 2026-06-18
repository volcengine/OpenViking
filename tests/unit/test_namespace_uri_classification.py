# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared Viking URI namespace/content classification."""

import pytest

from openviking.core.namespace import (
    canonical_session_uri,
    canonicalize_uri,
    classify_uri,
    context_type_for_uri,
    is_content_namespace_root_uri,
    is_content_root_uri,
    is_session_uri,
    legacy_session_uri,
    owner_space_for_uri,
    visible_roots,
)
from openviking.core.uri_validation import validate_content_target_uri
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidURIError
from openviking_cli.session.user_id import UserIdentifier


def test_context_type_for_uri_uses_path_segments():
    assert context_type_for_uri("viking://user/alice/memories/entities/m1.md") == "memory"
    assert context_type_for_uri("viking://user/memories/entities/m1.md") == "memory"
    assert context_type_for_uri("viking://user/alice/skills/demo") == "skill"
    assert context_type_for_uri("viking://user/skills/demo") == "skill"
    assert (
        context_type_for_uri(
            "viking://user/support_bot/peers/web-visitor-alice/memories/profile.md"
        )
        == "memory"
    )
    assert (
        context_type_for_uri("viking://user/support_bot/peers/web-visitor-alice/resources/faq.md")
        == "resource"
    )
    assert context_type_for_uri("viking://agent/code-agent/memories/profile.md") == "memory"
    assert context_type_for_uri("viking://agent/code-agent/resources/faq.md") == "resource"
    assert context_type_for_uri("viking://agent/code-agent/skills/demo") == "skill"
    assert context_type_for_uri("viking://resources/memories-report.md") == "resource"
    assert context_type_for_uri("viking://user/alice/resources/skills-report.md") == "resource"


def test_peer_content_target_uri_rejects_invalid_peer_id():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="support_bot"),
        role=Role.USER,
    )

    with pytest.raises(InvalidURIError, match="Invalid peer_id"):
        validate_content_target_uri(
            "viking://user/support_bot/peers/web+visitor+alice/resources/demo",
            ctx,
            kind="resource",
        )


def test_user_content_target_uri_rejects_invalid_user_id():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="support_bot"),
        role=Role.ROOT,
    )

    with pytest.raises(InvalidURIError, match="Invalid user_id"):
        validate_content_target_uri(
            "viking://user/team:alice/resources/demo",
            ctx,
            kind="resource",
        )


def test_exact_memory_and_skill_root_detection():
    assert classify_uri("viking://user/alice/memories/preferences/prefs.md").is_memory
    assert classify_uri("viking://user/alice/memories").is_memory_root
    assert classify_uri("viking://user/memories").is_memory_root
    assert not classify_uri("viking://user/alice/memories/preferences").is_memory_root

    assert classify_uri("viking://user/alice/skills/demo/SKILL.md").is_skill
    assert classify_uri("viking://user/alice/skills/demo").is_skill_root
    assert classify_uri("viking://user/skills/demo").is_skill_root
    assert not classify_uri("viking://user/alice/skills").is_skill_root
    assert not classify_uri("viking://user/alice/skills/demo/assets").is_skill_root


def test_owner_space_for_uri_uses_user_only():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.ROOT,
    )

    assert owner_space_for_uri("viking://user/alice/memories", ctx) == "alice"
    assert owner_space_for_uri("viking://user/alice/skills/demo", ctx) == "alice"
    assert owner_space_for_uri("viking://resources/readme.md", ctx) == ""


def test_session_uri_helpers_use_user_namespace():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )

    assert canonical_session_uri(ctx) == "viking://user/alice/sessions"
    assert canonical_session_uri(ctx, "s1") == "viking://user/alice/sessions/s1"
    assert canonicalize_uri("viking://user/sessions/s1", ctx) == ("viking://user/alice/sessions/s1")
    assert canonicalize_uri("user/sessions/s1", ctx) == "viking://user/alice/sessions/s1"
    assert canonicalize_uri("viking://session/s1", ctx) == "viking://user/alice/sessions/s1"
    assert (
        canonicalize_uri("viking://session/s1/history/archive_001/messages.jsonl", ctx)
        == "viking://user/alice/sessions/s1/history/archive_001/messages.jsonl"
    )
    assert legacy_session_uri("s1") == "viking://session/s1"
    assert is_session_uri("viking://user/alice/sessions/s1")
    assert is_session_uri("viking://user/sessions/s1")
    assert is_session_uri("viking://session/s1")
    assert "viking://session" not in visible_roots(ctx)


def test_current_user_short_content_roots_are_canonicalized_from_content_segments():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )

    assert canonicalize_uri("viking://user/resources", ctx) == "viking://user/alice/resources"
    assert (
        canonicalize_uri("viking://user/resources/docs", ctx)
        == "viking://user/alice/resources/docs"
    )
    assert (
        validate_content_target_uri(
            "viking://user/resources",
            ctx,
            kind="resource",
            field_name="parent",
        )
        == "viking://user/alice/resources"
    )
    assert canonicalize_uri("viking://user/alice/resources", ctx) == (
        "viking://user/alice/resources"
    )
    assert is_content_namespace_root_uri("viking://user/resources", ctx)
    assert is_content_namespace_root_uri("viking://user/resources/", ctx)
    assert is_content_namespace_root_uri("viking://resources", ctx)
    assert is_content_root_uri("viking://resources", ctx, kind="resource")
    assert not is_content_namespace_root_uri("viking://user/resources/docs", ctx)

