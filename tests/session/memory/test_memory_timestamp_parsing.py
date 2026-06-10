# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile, MemoryTypeSchema, ResolvedOperation
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext, MessageRange
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier


def _message(*, created_at: str, role: str = "user", text: str = "hello") -> Message:
    return Message(
        id=f"msg-{role}",
        role=role,
        parts=[TextPart(text=text)],
        created_at=created_at,
    )


@pytest.fixture
def stub_provider_config(monkeypatch):
    config = SimpleNamespace(
        memory=SimpleNamespace(eager_prefetch=False, prefetch_search_topn=5, link_enabled=True),
        language_fallback="en",
    )
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: config,
    )


def test_conversation_message_accepts_z_suffix_timestamps(stub_provider_config):
    provider = SessionExtractContextProvider(
        messages=[
            _message(created_at="2026-04-17T01:26:14.481Z", text="first"),
            _message(
                created_at="2026-04-17T02:31:09.000Z",
                role="assistant",
                text="second",
            ),
        ]
    )

    message = provider._build_conversation_message()

    assert "Session Time:** 2026-04-17 01:26 - 2026-04-17 02:31" in message["content"]
    assert "(Friday)" in message["content"]


def test_message_range_accepts_extended_fractional_seconds():
    msg_range = MessageRange(
        [
            [
                _message(created_at="2026-04-17T09:10:11.1234567+08:00"),
                _message(
                    created_at="2026-04-17T09:12:13.7654321+08:00",
                    role="assistant",
                ),
            ]
        ]
    )

    assert msg_range._first_message_time() == "2026-04-17"
    assert msg_range._first_message_time_with_weekday() == "2026-04-17 (Friday)"


def test_message_range_uses_peer_id_when_present():
    msg_range = MessageRange(
        [
            [
                Message(
                    id="msg-peer",
                    role="user",
                    parts=[TextPart(text="invoice follow-up")],
                    peer_id="web:visitor:alice",
                )
            ]
        ]
    )

    assert "[web:visitor:alice]: invoice follow-up" in msg_range.pretty_print()
    assert "[default]: invoice follow-up" not in msg_range.pretty_print()


def test_peer_id_routes_peer_memory_for_all_role_selected_types(stub_provider_config):
    messages = [
        Message(
            id="msg-peer",
            role="user",
            parts=[TextPart(text="I am Alice. Please contact me by email for invoices.")],
            peer_id="web:visitor:alice",
        )
    ]
    extract_context = ExtractContext(messages)
    ctx = RequestContext(
        user=UserIdentifier("acct", "support_bot"),
        role=Role.USER,
    )
    handler = MemoryIsolationHandler(
        ctx,
        extract_context,
        allowed_peer_ids={"web:visitor:alice"},
    )
    role_scope = handler.get_read_scope()
    fields = {"ranges": "0"}
    handler.fill_identity_fields(fields, role_scope)

    profile_schema = MemoryTypeSchema(
        memory_type="profile",
        directory="viking://user/{{ user_space }}/memories",
        filename_template="profile.md",
        fields=[],
    )
    profile_uris = handler.calculate_memory_uris(
        profile_schema,
        ResolvedOperation(memory_fields=fields, memory_type="profile", uris=[]),
        extract_context,
    )
    assert profile_uris == ["viking://user/support_bot/peers/web:visitor:alice/memories/profile.md"]

    tool_schema = MemoryTypeSchema(
        memory_type="tools",
        directory="viking://user/{{ user_space }}/memories/tools",
        filename_template="{{ tool_name }}.md",
        fields=[],
    )
    tool_fields = dict(fields, tool_name="email")
    tool_uris = handler.calculate_memory_uris(
        tool_schema,
        ResolvedOperation(memory_fields=tool_fields, memory_type="tools", uris=[]),
        extract_context,
    )
    assert tool_uris == [
        "viking://user/support_bot/peers/web:visitor:alice/memories/tools/email.md"
    ]


def test_peer_id_range_does_not_route_without_allowed_peer_ids(stub_provider_config):
    messages = [
        Message(
            id="msg-peer",
            role="user",
            parts=[TextPart(text="I am Alice. Please contact me by email for invoices.")],
            peer_id="web:visitor:alice",
        )
    ]
    extract_context = ExtractContext(messages)
    ctx = RequestContext(
        user=UserIdentifier("acct", "support_bot"),
        role=Role.USER,
    )
    handler = MemoryIsolationHandler(ctx, extract_context)
    role_scope = handler.get_read_scope()
    fields = {"ranges": "0"}
    handler.fill_identity_fields(fields, role_scope)

    profile_schema = MemoryTypeSchema(
        memory_type="profile",
        directory="viking://user/{{ user_space }}/memories",
        filename_template="profile.md",
        fields=[],
    )
    profile_uris = handler.calculate_memory_uris(
        profile_schema,
        ResolvedOperation(memory_fields=fields, memory_type="profile", uris=[]),
        extract_context,
    )

    assert profile_uris == []


def test_deserialize_full_parses_memory_metadata_timestamps_with_z_suffix():
    mf = MemoryFile(
        content="memory body",
        extra_fields={
            "created_at": "2026-04-17T01:26:14.481Z",
            "updated_at": "2026-04-17T09:10:11.1234567+08:00",
        },
    )
    full_content = MemoryFileUtils.write(mf)

    result = MemoryFileUtils.read(full_content)

    assert result.content == "memory body"
    assert result.extra_fields is not None
    assert result.extra_fields["created_at"].isoformat() == "2026-04-17T01:26:14.481000+00:00"
    assert result.extra_fields["updated_at"].isoformat() == "2026-04-17T09:10:11.123456+08:00"
