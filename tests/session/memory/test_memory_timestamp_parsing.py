# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.session.memory.memory_updater import MessageRange
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.utils import deserialize_full, serialize_with_metadata


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
        memory=SimpleNamespace(eager_prefetch=False),
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
            _message(created_at="2026-04-17T09:10:11.1234567+08:00"),
            _message(
                created_at="2026-04-17T09:12:13.7654321+08:00",
                role="assistant",
            ),
        ]
    )

    assert msg_range._first_message_time() == "2026-04-17"
    assert msg_range._first_message_time_with_weekday() == "2026-04-17 (Friday)"


def test_deserialize_full_parses_memory_metadata_timestamps_with_z_suffix():
    full_content = serialize_with_metadata(
        {
            "content": "memory body",
            "created_at": "2026-04-17T01:26:14.481Z",
            "updated_at": "2026-04-17T09:10:11.1234567+08:00",
        }
    )

    body, metadata = deserialize_full(full_content)

    assert body == "memory body"
    assert metadata is not None
    assert metadata["created_at"].isoformat() == "2026-04-17T01:26:14.481000+00:00"
    assert metadata["updated_at"].isoformat() == "2026-04-17T09:10:11.123456+08:00"
