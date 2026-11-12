# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Slack message text reaches VikingBot in its user-visible form."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.slack import SlackChannel
from vikingbot.config.schema import SlackChannelConfig


@pytest.mark.parametrize(
    ("event_type", "channel_type", "wire_text", "expected"),
    [
        (
            "message",
            "im",
            "fetch <https://example.com/search?q=viking&amp;page=2>",
            "fetch https://example.com/search?q=viking&page=2",
        ),
        (
            "message",
            "im",
            'grep "a &lt; b" log.txt &gt; out.txt &amp;&amp; echo ok',
            'grep "a < b" log.txt > out.txt && echo ok',
        ),
        (
            "app_mention",
            "channel",
            "<@U_BOT> inspect <https://example.com/s?a=1&amp;b=2|docs &amp; runbook>",
            "inspect docs & runbook (https://example.com/s?a=1&b=2)",
        ),
        (
            "message",
            "im",
            "keep &lt;https://a.example&gt; and &amp;lt; as typed",
            "keep <https://a.example> and &lt; as typed",
        ),
        (
            "message",
            "im",
            "ask <@U_OTHER> in <#C123> <!here> &amp; wait",
            "ask <@U_OTHER> in <#C123> <!here> & wait",
        ),
    ],
)
@pytest.mark.asyncio
async def test_slack_delivers_user_visible_text(event_type, channel_type, wire_text, expected):
    bus = MessageBus()
    channel = SlackChannel(SlackChannelConfig(bot_token="xoxb-test"), bus)
    channel._bot_user_id = "U_BOT"
    channel._web_client = AsyncMock()
    request = SimpleNamespace(
        type="events_api",
        envelope_id="envelope",
        payload={
            "event": {
                "type": event_type,
                "text": wire_text,
                "user": "U_USER",
                "channel": "D123" if channel_type == "im" else "C123",
                "channel_type": channel_type,
                "ts": "1234567890.000001",
            }
        },
    )

    await channel._on_socket_request(AsyncMock(), request)

    assert bus.inbound_size == 1
    assert (await bus.consume_inbound()).content == expected
