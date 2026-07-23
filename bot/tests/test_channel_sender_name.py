# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for channel sender names."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.bus.queue import MessageBus  # noqa: E402
from vikingbot.channels.whatsapp import WhatsAppChannel  # noqa: E402
from vikingbot.config.schema import WhatsAppChannelConfig  # noqa: E402


@pytest.mark.asyncio
async def test_whatsapp_sender_name_falls_back_to_sender_id():
    bus = MessageBus()
    channel = WhatsAppChannel(WhatsAppChannelConfig(), bus)

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "sender": "123456789@s.whatsapp.net",
                "content": "hello",
            }
        )
    )

    inbound = await bus.consume_inbound()
    assert inbound.sender_name == "123456789"
