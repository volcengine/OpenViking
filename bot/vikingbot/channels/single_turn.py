# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Single-turn channel - no extra output, just the result."""

import asyncio
from pathlib import Path
from typing import Any
import json

from loguru import logger

from vikingbot.bus.events import InboundMessage, OutboundMessage, OutboundEventType
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.base import BaseChannel
from vikingbot.config.schema import SessionKey, BaseChannelConfig


class SingleTurnChannelConfig(BaseChannelConfig):
    """Configuration for SingleTurnChannel."""

    enabled: bool = True
    type: Any = "cli"
    _channel_id: str = "default"

    def channel_id(self) -> str:
        return self._channel_id


class SingleTurnChannel(BaseChannel):
    """
    Single-turn channel for one-off messages.

    Only outputs the final result, no extra messages, no thinking/tool call display.
    Only error-level logs are shown.
    """

    name: str = "single_turn"

    def __init__(
        self,
        config: BaseChannelConfig,
        bus: MessageBus,
        workspace_path: Path | None = None,
        message: str = "",
        session_id: str = "default",
        markdown: bool = True,
        eval: bool = False,
    ):
        super().__init__(config, bus, workspace_path)
        self.message = message
        self.session_id = session_id
        self.markdown = markdown
        self._response_received = asyncio.Event()
        self._last_response: str | None = None
        self._eval = eval

    async def start(self) -> None:
        """Start the single-turn channel - send message and wait for response."""
        self._running = True

        # Send the message
        msg = InboundMessage(
            session_key=SessionKey(
                type="cli",
                channel_id=self.config.channel_id(),
                chat_id=self.session_id,
            ),
            sender_id="default",
            content=self.message,
        )
        await self.bus.publish_inbound(msg)

        # Wait for response with timeout
        try:
            await asyncio.wait_for(self._response_received.wait(), timeout=3000.0)
            if self._last_response:
                from vikingbot.cli.commands import console
                from rich.markdown import Markdown
                from rich.text import Text

                content = self._last_response or ""
                body = Markdown(content) if self.markdown else Text(content)
                console.print(body)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for response")

    async def stop(self) -> None:
        """Stop the single-turn channel."""
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message - store final response for later retrieval."""
        if msg.is_normal_message:
            if self._eval:
                output = {
                    "text": msg.content,
                    "token_usage": msg.token_usage,
                    "time_cost": msg.time_cost,
                }
                msg.content = json.dumps(output, ensure_ascii=False)
            self._last_response = msg.content
            self._response_received.set()
