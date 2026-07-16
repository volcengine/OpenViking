# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.session import Session


class _Message:
    def __init__(self, tokens: int) -> None:
        self.estimated_tokens = tokens

    def to_dict(self) -> dict[str, object]:
        return {"role": "user", "parts": []}


@pytest.mark.asyncio
async def test_get_session_context_returns_newest_archive_abstracts_within_budget():
    session = Session(viking_fs=None, session_id="budget-unit")
    session._collect_session_context_components = AsyncMock(
        return_value={
            "latest_archive": {
                "archive_id": "archive_003",
                "overview": "latest overview",
                "overview_tokens": 20,
            },
            "pre_archive_abstracts": [
                {"archive_id": "archive_003", "abstract": "third", "tokens": 4},
                {"archive_id": "archive_002", "abstract": "second", "tokens": 4},
                {"archive_id": "archive_001", "abstract": "first", "tokens": 4},
            ],
            "total_archives": 3,
            "failed_archives": 0,
            "messages": [_Message(tokens=10)],
        }
    )

    context = await session.get_session_context(token_budget=38)

    assert context["pre_archive_abstracts"] == [
        {"archive_id": "archive_003", "abstract": "third"},
        {"archive_id": "archive_002", "abstract": "second"},
    ]
    assert context["estimatedTokens"] == 38
    assert context["stats"] == {
        "totalArchives": 3,
        "includedArchives": 2,
        "droppedArchives": 1,
        "failedArchives": 0,
        "activeTokens": 10,
        "archiveTokens": 28,
    }
