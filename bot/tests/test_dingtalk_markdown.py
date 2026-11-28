# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for DingTalk Markdown table delivery."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.bus.events import OutboundMessage
from vikingbot.channels.dingtalk import DingTalkChannel, _adapt_markdown_for_dingtalk
from vikingbot.config.schema import SessionKey


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        (
            "| Name | Age |\n|---|---|\n| Alice | 30 |",
            "> **Name**: Alice\n> **Age**: 30",
        ),
        (
            "| Rule | Meaning |\n|---|---|\n| `foo\\|bar` | alternative |",
            "> **Rule**: `foo\\|bar`\n> **Meaning**: alternative",
        ),
        (
            "| Rule | Meaning |\n|---|---|\n|| alternative |",
            "> **Rule**:\n> **Meaning**: alternative",
        ),
        (
            "| Rule | Meaning |\n|---|---|\n| regex ||",
            "> **Rule**: regex\n> **Meaning**:",
        ),
        (
            "Rule | Meaning\n---|---\nregex | alternative",
            "> **Rule**: regex\n> **Meaning**: alternative",
        ),
    ],
)
def test_markdown_tables_become_labeled_blocks(markdown, expected):
    assert _adapt_markdown_for_dingtalk(markdown) == expected


def test_multiple_rows_remain_separate():
    markdown = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |"

    assert _adapt_markdown_for_dingtalk(markdown) == (
        "> **Name**: Alice\n> **Age**: 30\n\n> **Name**: Bob\n> **Age**: 25"
    )


@pytest.mark.parametrize(
    "markdown",
    [
        "A normal reply without a table.",
        "```markdown\n| Name | Age |\n|---|---|\n| Alice | 30 |\n```",
        "| not | a table |",
        "| Name | Age |\n|---|---|\nonly one cell",
    ],
)
def test_non_tables_are_unchanged(markdown):
    assert _adapt_markdown_for_dingtalk(markdown) == markdown


@pytest.mark.asyncio
async def test_send_adapts_table_before_building_sample_markdown_payload():
    channel = object.__new__(DingTalkChannel)
    channel.config = SimpleNamespace(client_id="robot")
    channel._get_access_token = AsyncMock(return_value="token")
    channel._http = SimpleNamespace(
        post=AsyncMock(return_value=SimpleNamespace(status_code=200, text="ok"))
    )
    message = OutboundMessage(
        session_key=SessionKey(type="dingtalk", channel_id="robot", chat_id="user"),
        content="| Name | Age |\n|---|---|\n| Alice | 30 |",
    )

    await channel.send(message)

    request = channel._http.post.await_args.kwargs["json"]
    assert request["msgKey"] == "sampleMarkdown"
    assert json.loads(request["msgParam"])["text"] == ("> **Name**: Alice\n> **Age**: 30")
