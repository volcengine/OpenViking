# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Session commit memory extraction scope tests."""

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory_policy import MemoryPolicy
from openviking.session.session import _resolve_memory_extraction_scope
from openviking_cli.session.user_id import UserIdentifier


def _ctx(actor_peer_id: str | None = None) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acct", "support_bot"),
        role=Role.USER,
        actor_peer_id=actor_peer_id,
    )


def _message(peer_id: str | None = None) -> Message:
    return Message(
        id=f"msg-{peer_id or 'self'}",
        role="user",
        parts=[TextPart("remember this")],
        peer_id=peer_id,
    )


def test_owner_memory_extraction_scope_uses_message_peer_ids():
    scope = _resolve_memory_extraction_scope(
        _ctx(),
        MemoryPolicy(peer_enabled=True),
        [_message("alice"), _message("bob"), _message()],
        config_session_skill_extraction_enabled=True,
    )

    assert scope.allow_self_memory is True
    assert scope.allowed_peer_ids == {"alice", "bob"}
    assert scope.include_session_skills is True


def test_owner_memory_extraction_scope_includes_assistant_peer_ids():
    assistant_message = _message("assistant-bot")
    assistant_message.role = "assistant"

    scope = _resolve_memory_extraction_scope(
        _ctx(),
        MemoryPolicy(peer_enabled=True),
        [_message("alice"), assistant_message],
        config_session_skill_extraction_enabled=True,
    )

    assert scope.allow_self_memory is True
    assert scope.allowed_peer_ids == {"alice", "assistant-bot"}
    assert scope.include_session_skills is True


def test_actor_memory_extraction_scope_still_uses_policy_and_messages():
    scope = _resolve_memory_extraction_scope(
        _ctx("alice"),
        MemoryPolicy(peer_enabled=True),
        [_message("bob"), _message()],
        config_session_skill_extraction_enabled=True,
    )

    assert scope.allow_self_memory is True
    assert scope.allowed_peer_ids == {"bob"}
    assert scope.include_session_skills is True
