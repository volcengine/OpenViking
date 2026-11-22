# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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
        output_language_override=None,
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


def test_extract_context_uses_configured_timezone_for_archive_dates():
    context = ExtractContext(
        [_message(created_at="2026-11-21T18:18:09.765Z")],
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    assert context.get_first_message_time_from_ranges("0") == "2026-11-22"
    assert context.get_first_message_time_with_weekday_from_ranges("0") == "2026-11-22 (Sunday)"
    assert (
        context.get_year("0"),
        context.get_month("0"),
        context.get_day("0"),
    ) == ("2026", "11", "22")
    assert context.get_timestamp_from_ranges("0") == "20261122021809"
    assert context.get_session_timestamp() == "20261122021809"


def test_extract_context_timezone_crosses_month_and_year_boundaries():
    month_context = ExtractContext(
        [_message(created_at="2026-11-30T17:00:00Z")],
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    assert (
        month_context.get_year("0"),
        month_context.get_month("0"),
        month_context.get_day("0"),
    ) == ("2026", "12", "01")

    year_context = ExtractContext(
        [_message(created_at="2026-12-31T16:30:00Z")],
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    assert (
        year_context.get_year("0"),
        year_context.get_month("0"),
        year_context.get_day("0"),
    ) == ("2027", "01", "01")


def test_extract_context_timestamp_uses_selected_range_message():
    context = ExtractContext(
        [
            _message(created_at="2026-11-21T18:18:09.765Z", text="first"),
            _message(
                created_at="2026-11-22T03:04:05.000Z",
                role="assistant",
                text="second",
            ),
        ],
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    assert context.get_timestamp_from_ranges("1") == "20261122110405"


def test_extract_context_archive_dates_default_to_utc():
    context = ExtractContext(
        [_message(created_at="2026-11-21T18:18:09.765Z")],
        timezone=timezone.utc,
    )

    assert context.get_first_message_time_from_ranges("0") == "2026-11-21"
    assert context.get_day("0") == "21"
    assert context.get_timestamp_from_ranges("0") == "20261121181809"


def test_extract_context_defaults_to_configured_memory_timezone(monkeypatch):
    config = SimpleNamespace(memory=SimpleNamespace(timezone="Asia/Shanghai"))
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", lambda: config)

    context = ExtractContext([_message(created_at="2026-11-21T18:18:09.765Z")])

    assert context.timezone == ZoneInfo("Asia/Shanghai")
    assert context.get_day("0") == "22"


def test_extract_context_without_config_defaults_to_utc(monkeypatch):
    def raise_missing_config():
        raise FileNotFoundError("no ov.conf")

    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", raise_missing_config)

    context = ExtractContext([])

    assert context.timezone == timezone.utc


def test_conversation_message_uses_configured_timezone(monkeypatch):
    config = SimpleNamespace(
        output_language_override=None,
        memory=SimpleNamespace(
            eager_prefetch=False,
            prefetch_search_topn=5,
            link_enabled=True,
            timezone="Asia/Shanghai",
        ),
        language_fallback="en",
    )
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: config,
    )

    provider = SessionExtractContextProvider(
        messages=[
            _message(created_at="2026-11-21T18:18:09.765Z", text="first"),
            _message(
                created_at="2026-11-21T18:31:09.000Z",
                role="assistant",
                text="second",
            ),
        ]
    )

    message = provider._build_conversation_message()

    assert "Session Time:** 2026-11-22 02:18 - 2026-11-22 02:31" in message["content"]
    assert "(Sunday)" in message["content"]
    assert provider.get_extract_context().get_day("0") == "22"


def test_extraction_context_chunks_long_text_and_preserves_decimal_numbers(stub_provider_config):
    text = (
        "周一 "
        + "需求沟通。" * 20
        + "上下文用量out：21.9K、in：110.2k、缓存读取501.0k，先简单记录下来。 "
        + "周二 面试三个新员工：王军优势是agent算法，田伟优势是分布式后端架构，"
        + "金然优势是Agent架构全栈，缺点是缺乏CPU服务器底层调优经验 "
        + "周三 query-builder的输出直接进行判断会触发错误，原因不明。"
    )
    extract_context = ExtractContext(
        [
            Message(
                id="msg-long",
                role="user",
                parts=[TextPart(text=text)],
                created_at="2026-06-01T08:56:37.382Z",
            )
        ]
    )

    extraction_messages = extract_context.messages
    merged = "".join(message.content for message in extraction_messages)

    assert len(extraction_messages) > 1
    assert merged == text
    assert all(message.id.startswith("msg-long#chunk_") for message in extraction_messages)
    assert "21.9K" in merged
    assert "110.2k" in merged
    assert "501.0k" in merged
    assert not any(message.content.startswith(("9K", "2k")) for message in extraction_messages)


def test_peer_id_routes_peer_memory_for_all_role_selected_types(stub_provider_config):
    messages = [
        Message(
            id="msg-peer",
            role="user",
            parts=[TextPart(text="I am Alice. Please contact me by email for invoices.")],
            peer_id="web-visitor-alice",
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
        allowed_peer_ids={"web-visitor-alice"},
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
    assert profile_uris == ["viking://user/support_bot/peers/web-visitor-alice/memories/profile.md"]

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
        "viking://user/support_bot/peers/web-visitor-alice/memories/tools/email.md"
    ]


def test_peer_id_range_does_not_route_without_allowed_peer_ids(stub_provider_config):
    messages = [
        Message(
            id="msg-peer",
            role="user",
            parts=[TextPart(text="I am Alice. Please contact me by email for invoices.")],
            peer_id="web-visitor-alice",
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
