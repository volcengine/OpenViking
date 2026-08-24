# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared Viking URI namespace/content classification."""

import re

import pytest

from openviking.core.namespace import (
    NamespaceShapeError,
    canonical_session_uri,
    classify_uri,
    context_type_for_uri,
    is_content_root_uri,
    is_session_uri,
    owner_space_for_uri,
    resolve_request_uri,
    resolve_uri,
    visible_roots,
)
from openviking.core.uri_validation import (
    validate_content_target_uri,
    validate_request_viking_uri,
)
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidURIError
from openviking_cli.session.user_id import UserIdentifier


def test_context_type_for_uri_uses_path_segments():
    assert context_type_for_uri("viking://user/alice/memories/entities/m1.md") == "memory"
    assert context_type_for_uri("viking://user/memories/entities/m1.md") == "resource"
    assert context_type_for_uri("viking://user/alice/skills/demo") == "skill"
    assert context_type_for_uri("viking://user/skills/demo") == "resource"
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
    assert context_type_for_uri("viking://agent/skills") == "skill"
    assert context_type_for_uri("viking://agent/skills/demo") == "skill"
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
    assert not classify_uri("viking://user/memories").is_memory_root
    assert not classify_uri("viking://user/alice/memories/preferences").is_memory_root

    assert classify_uri("viking://user/alice/skills/demo/SKILL.md").is_skill
    assert classify_uri("viking://user/alice/skills/demo").is_skill_root
    assert not classify_uri("viking://user/skills/demo").is_skill_root
    assert classify_uri("viking://agent/skills").is_skill_namespace
    assert classify_uri("viking://agent/skills/demo").is_skill_root
    assert not classify_uri("viking://user/alice/skills").is_skill_root
    assert not classify_uri("viking://user/alice/skills/demo/assets").is_skill_root


def test_owner_space_for_uri_uses_user_only():
    assert owner_space_for_uri("viking://user/alice/memories") == "alice"
    assert owner_space_for_uri("viking://user/alice/skills/demo") == "alice"
    assert owner_space_for_uri("viking://resources/readme.md") == ""


def test_session_uri_helpers_use_user_namespace():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )

    assert canonical_session_uri(ctx) == "viking://user/alice/sessions"
    assert canonical_session_uri(ctx, "s1") == "viking://user/alice/sessions/s1"
    assert resolve_uri("viking://user/sessions/s1").uri == "viking://user/sessions/s1"
    # The uid-less 'sessions' shorthand no longer expands; it fails closed with a
    # hint pointing at the '~' home alias.
    with pytest.raises(NamespaceShapeError, match=re.escape("viking://~/sessions/s1")):
        resolve_request_uri("viking://user/sessions/s1", ctx)
    assert (
        resolve_request_uri("viking://session/s1", ctx)
        == "viking://user/alice/sessions/s1"
    )
    assert (
        resolve_request_uri(
            "viking://session/s1/history/archive_001/messages.jsonl", ctx
        )
        == "viking://user/alice/sessions/s1/history/archive_001/messages.jsonl"
    )
    assert is_session_uri("viking://user/alice/sessions/s1")
    assert not is_session_uri("viking://user/sessions/s1")
    assert is_session_uri("viking://session/s1")
    roots = visible_roots(ctx)
    assert "viking://session" not in roots
    assert "viking://agent" not in roots
    assert "viking://agent/skills" in roots


def test_request_boundary_rejects_reserved_user_root_shorthand():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )

    with pytest.raises(NamespaceShapeError, match=re.escape("viking://~/resources")):
        resolve_request_uri("viking://user/resources", ctx)
    with pytest.raises(NamespaceShapeError, match=re.escape("viking://~/resources/docs")):
        resolve_request_uri("viking://user/resources/docs", ctx)
    with pytest.raises(InvalidURIError, match=re.escape("viking://~/resources")):
        validate_content_target_uri(
            "viking://user/resources",
            ctx,
            kind="resource",
            field_name="parent",
        )
    # Account-shared resources are untouched by the removal.
    assert (
        validate_content_target_uri(
            "viking://resources/docs/",
            ctx,
            kind="resource",
            field_name="parent",
        )
        == "viking://resources/docs"
    )
    assert resolve_uri("viking://user/alice/resources").uri == "viking://user/alice/resources"
    # Self-id escape: a caller literally named 'resources' keeps the literal URI
    # as their own canonical root.
    collision_ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="resources"),
        role=Role.USER,
    )
    assert (
        resolve_request_uri("viking://user/resources", collision_ctx)
        == "viking://user/resources"
    )
    admin_ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="admin"),
        role=Role.ADMIN,
    )
    with pytest.raises(NamespaceShapeError, match=re.escape("viking://~/resources")):
        resolve_request_uri("viking://user/resources", admin_ctx)
    with pytest.raises(NamespaceShapeError, match=re.escape("viking://~/skills")):
        resolve_request_uri("viking://user/skills", admin_ctx)
    # ROOT requests never enter current-user resolution, so the literal parse stands.
    root_ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="root-actor"),
        role=Role.ROOT,
    )
    assert (
        resolve_request_uri("viking://user/resources", root_ctx)
        == "viking://user/resources"
    )
    assert is_content_root_uri("viking://resources", kind="resource")


@pytest.mark.parametrize(
    "segment", ["memories", "resources", "skills", "peers", "privacy", "sessions"]
)
@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
def test_reserved_user_root_shorthand_rejection_message(segment, role):
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=role,
    )

    with pytest.raises(NamespaceShapeError) as excinfo:
        resolve_request_uri(f"viking://user/{segment}", ctx)
    message = str(excinfo.value)
    assert f"viking://~/{segment}" in message
    assert f"viking://user/{{user_id}}/{segment}" in message

    with pytest.raises(NamespaceShapeError) as nested:
        resolve_request_uri(f"viking://user/{segment}/nested/leaf.md", ctx)
    nested_message = str(nested.value)
    assert f"viking://~/{segment}/nested/leaf.md" in nested_message
    assert f"viking://user/{{user_id}}/{segment}/nested/leaf.md" in nested_message


def test_bare_user_uri_stays_the_user_space_container():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )

    assert resolve_request_uri("viking://user", ctx) == "viking://user"
    assert resolve_request_uri("viking://user/", ctx) == "viking://user"
    assert resolve_uri("viking://user").is_container is True
    assert resolve_uri("viking://user").owner_user_id is None


def test_unreserved_user_root_segment_keeps_canonical_meaning():
    # The generic namespace parser stays canonical-first for user id segments: an
    # unreserved segment under viking://user/ is a peer user id, never a shorthand.
    # The '~' home alias does not weaken that rule -- it is a reserved token that
    # cannot be a valid user id (see identifiers.validate_user_id), and it is only
    # recognized as segment 0, so it never competes with a canonical user segment.
    # Callers that expose a current-user workspace dialect (such as MCP) must still
    # opt into that policy at their boundary instead of guessing from file
    # extensions or server state.
    assert resolve_uri("viking://user/notes/todo.md").uri == "viking://user/notes/todo.md"
    assert (
        resolve_uri("viking://user/bob/zeus-persona.md").uri
        == "viking://user/bob/zeus-persona.md"
    )
    # A valid canonical user id may itself end in a common file extension.
    assert (
        resolve_uri("viking://user/writer.md/memories/profile.md").uri
        == "viking://user/writer.md/memories/profile.md"
    )
    assert (
        resolve_uri("viking://user/alice.smith/memories/preferences/p.md").uri
        == "viking://user/alice.smith/memories/preferences/p.md"
    )
    assert (
        resolve_uri("viking://user/bob@corp.com/resources/r.md").uri
        == "viking://user/bob@corp.com/resources/r.md"
    )
    # The current user's own canonical form remains unchanged.
    assert (
        resolve_uri("viking://user/alice/notes/todo.md").uri
        == "viking://user/alice/notes/todo.md"
    )


def test_home_alias_expands_to_current_user_root_at_request_boundary():
    for role in (Role.USER, Role.ADMIN):
        ctx = RequestContext(
            user=UserIdentifier(account_id="acct", user_id="alice"),
            role=role,
        )

        assert resolve_request_uri("viking://~", ctx) == "viking://user/alice"
        assert resolve_request_uri("viking://~/", ctx) == "viking://user/alice"
        assert (
            resolve_request_uri("viking://~/memories/preferences/p.md", ctx)
            == "viking://user/alice/memories/preferences/p.md"
        )
        assert (
            resolve_request_uri("viking://~/resources/docs/", ctx)
            == "viking://user/alice/resources/docs"
        )
        assert (
            validate_request_viking_uri("viking://~/resources/docs", ctx)
            == "viking://user/alice/resources/docs"
        )

    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )
    expanded = resolve_request_uri("viking://~/memories", ctx)
    # The expanded alias is field-for-field identical to the explicit form.
    assert resolve_uri(expanded) == resolve_uri("viking://user/alice/memories")
    assert context_type_for_uri(resolve_request_uri("viking://~/memories/m.md", ctx)) == "memory"
    assert is_content_root_uri(resolve_request_uri("viking://~/resources", ctx), kind="resource")
    assert (
        validate_content_target_uri("viking://~/resources", ctx, kind="resource")
        == "viking://user/alice/resources"
    )


def test_home_alias_fails_closed_without_current_user_resolution():
    root_ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="root-actor"),
        role=Role.ROOT,
    )

    # Root-role requests skip current-user resolution, so the alias never becomes
    # a literal '~' namespace -- it is rejected instead of guessing a user.
    with pytest.raises(NamespaceShapeError, match="Home alias URI is not canonical"):
        resolve_request_uri("viking://~/resources/docs", root_ctx)
    with pytest.raises(InvalidURIError, match="Home alias URI is not canonical"):
        validate_request_viking_uri("viking://~/resources/docs", root_ctx)

    # Every internal consumer of the canonical parser is protected the same way.
    with pytest.raises(NamespaceShapeError, match="Home alias URI is not canonical"):
        resolve_uri("viking://~")
    with pytest.raises(NamespaceShapeError, match="Home alias URI is not canonical"):
        resolve_uri("viking://~/x")
    assert not is_content_root_uri("viking://~/resources", kind="resource")


def test_home_alias_is_only_recognized_as_first_segment():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )

    # '~' is not a valid user id, so it can never shadow a real user namespace.
    with pytest.raises(NamespaceShapeError, match="Invalid user_id"):
        resolve_request_uri("viking://user/~/x", ctx)

    # Anywhere else it stays a literal path segment.
    assert resolve_request_uri("viking://resources/~/x", ctx) == "viking://resources/~/x"
    assert resolve_request_uri("viking://user/alice/~/x", ctx) == "viking://user/alice/~/x"
