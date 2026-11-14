# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Telegram delivery preserves link destinations while formatting labels."""

from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.telegram import TelegramChannel
from vikingbot.config.schema import SessionKey, TelegramChannelConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a__b__c",
        "https://example.com/?a=_one_&b=_two_",
        "https://example.com/~~draft~~",
        "https://example.com/**draft**",
        'https://example.com/?q="quoted"',
        "https://example.com/?a=one&b=two",
    ],
)
async def test_send_preserves_link_destination_and_formats_label(url, monkeypatch):
    send_message = AsyncMock()
    channel = TelegramChannel(TelegramChannelConfig(token="123:test"), MessageBus())
    monkeypatch.setattr(
        channel, "_app", SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    )
    message = OutboundMessage(
        session_key=SessionKey(type="telegram", channel_id="123", chat_id="42"),
        content=f"[**report**]({url}) and [other](https://example.org/)",
    )

    await channel.send(message)

    send_message.assert_awaited_once_with(
        chat_id=42,
        text=(
            f'<a href="{escape(url, quote=True)}"><b>report</b></a> and '
            '<a href="https://example.org/">other</a>'
        ),
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_send_keeps_code_literal_while_formatting_links(monkeypatch):
    send_message = AsyncMock()
    channel = TelegramChannel(TelegramChannelConfig(token="123:test"), MessageBus())
    monkeypatch.setattr(
        channel, "_app", SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    )
    message = OutboundMessage(
        session_key=SessionKey(type="telegram", channel_id="123", chat_id="42"),
        content=(
            "`[literal](https://example.com/a__b__c)`\n"
            "```\n[block](https://example.com/_a_)\n```\n"
            "**bold** [read _report_](https://example.org/~~draft~~)"
        ),
    )

    await channel.send(message)

    send_message.assert_awaited_once_with(
        chat_id=42,
        text=(
            "<code>[literal](https://example.com/a__b__c)</code>\n"
            "<pre><code>[block](https://example.com/_a_)\n</code></pre>\n"
            '<b>bold</b> <a href="https://example.org/~~draft~~">'
            "read <i>report</i></a>"
        ),
        parse_mode="HTML",
    )
