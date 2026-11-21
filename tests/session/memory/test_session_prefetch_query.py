# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider


def _provider(messages):
    # Query construction does not require a configured model or storage.
    provider = SessionExtractContextProvider.__new__(SessionExtractContextProvider)
    provider.messages = messages
    return provider


def test_prefetch_query_keeps_user_parts_and_peer_names_without_assistant_replies():
    messages = [
        Message(id="1", role="assistant", parts=[TextPart(text="Try a mountain bike.")]),
        Message(
            id="2",
            role="user",
            peer_id="alice",
            parts=[TextPart(text="My commuter bike"), TextPart(text="needs new lights.")],
        ),
        Message(id="3", role="assistant", parts=[TextPart(text="Also buy a racing helmet.")]),
        Message(id="4", role="user", parts=[TextPart(text="I prefer rechargeable lights.")]),
    ]

    query = _provider(messages)._build_prefetch_search_query()

    assert query == "alice: My commuter bike needs new lights. user: I prefer rechargeable lights."
    assert messages[0].content == "Try a mountain bike."
    assert messages[2].content == "Also buy a racing helmet."


@pytest.mark.parametrize("user_parts", [None, [], [TextPart(text="")]])
def test_prefetch_query_does_not_fall_back_to_assistant_content(user_parts):
    messages = [Message(id="1", role="assistant", parts=[TextPart(text="ASSISTANT_ONLY")])]
    if user_parts is not None:
        messages.append(Message(id="2", role="user", parts=user_parts))

    assert _provider(messages)._build_prefetch_search_query() == ""


def test_prefetch_query_preserves_user_text_and_total_size_limits():
    messages = [
        Message(id=str(i), role="user", parts=[TextPart(text=chr(97 + i) * 1500)]) for i in range(8)
    ]
    messages.append(Message(id="assistant", role="assistant", parts=[TextPart(text="SENTINEL")]))

    query = _provider(messages)._build_prefetch_search_query()

    assert len(query) == 5000
    assert query.startswith("user: " + "a" * 997 + "...")
    assert "a" * 998 not in query
    assert "SENTINEL" not in query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_text,expected_query",
    [("I finished my novel.", "user: I finished my novel."), ("", "conversation")],
)
async def test_prefetch_search_excludes_assistant_but_extraction_keeps_full_conversation(
    monkeypatch, user_text, expected_query
):
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: SimpleNamespace(memory=None),
    )
    messages = [
        Message(id="1", role="user", parts=[TextPart(text=user_text)]),
        Message(
            id="2", role="assistant", parts=[TextPart(text="The Silent Patient is a thriller.")]
        ),
    ]
    provider = SessionExtractContextProvider(messages=messages)
    directory = "viking://user/alice/memories/entities"
    schema = SimpleNamespace(
        stage="user",
        directory=directory,
        operation_mode="merge",
        filename_has_variables=lambda: True,
    )
    provider._registry = SimpleNamespace(list_all=lambda **kwargs: [schema])
    provider._isolation_handler = SimpleNamespace(
        allows_schema=lambda item: True,
        get_read_scope=lambda: SimpleNamespace(user_ids=["alice"], peer_ids=[]),
        render_schema_directories=lambda item: [directory],
    )
    provider.search_files = AsyncMock(return_value=[])

    prefetched = await provider.prefetch()

    provider.search_files.assert_awaited_once_with(query=expected_query, search_uris=[directory])
    assert "The Silent Patient is a thriller." in prefetched[0]["content"]
    assert provider.messages == messages
