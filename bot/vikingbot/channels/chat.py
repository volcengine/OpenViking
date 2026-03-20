# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Chat channel for interactive mode."""

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

from loguru import logger
from rich.style import Style

from vikingbot.bus.events import InboundMessage, OutboundMessage, OutboundEventType
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.base import BaseChannel
from vikingbot.config.schema import SessionKey, BaseChannelConfig


class ChatChannelConfig(BaseChannelConfig):
    """Configuration for ChatChannel."""

    enabled: bool = True
    type: Any = "cli"
    _channel_id: str = "default"

    def channel_id(self) -> str:
        return self._channel_id


class ChatChannel(BaseChannel):
    """
    Chat channel for interactive mode.

    This channel supports:
    - Interactive mode (prompt-based)
    - Displays thinking steps and tool calls
    """

    name: str = "chat"

    def __init__(
        self,
        config: BaseChannelConfig,
        bus: MessageBus,
        workspace_path: Path | None = None,
        session_id: str = "default",
        markdown: bool = True,
        logs: bool = False,
        sender: str | None = None,
    ):
        super().__init__(config, bus, workspace_path)
        self.session_id = session_id
        self.markdown = markdown
        self.logs = logs
        self.sender = sender
        self._response_received = asyncio.Event()
        self._last_response: str | None = None

    async def start(self) -> None:
        """Start the chat channel."""
        self._running = True

        # Interactive mode only
        await self._run_interactive()

    async def stop(self) -> None:
        """Stop the chat channel."""
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message - display thinking events and store final response."""
        from vikingbot.cli.commands import console
        from rich.markdown import Markdown
        from rich.text import Text

        if msg.is_normal_message:
            self._last_response = msg.content
            self._response_received.set()
            # Print Bot: response
            console.print()
            content = msg.content or ""
            console.print("[bold red]Bot:[/bold red]")
            from rich.markdown import Markdown
            from rich.text import Text

            body = (
                Markdown(content, style="red")
                if self.markdown
                else Text(content, style=Style(color="red"))
            )

            console.print(body)
            console.print()
        else:
            # Handle thinking events
            if msg.event_type == OutboundEventType.REASONING:
                # Truncate long reasoning
                content = msg.content.strip()
                if content:
                    if len(content) > 100:
                        content = content[:100] + "..."
                    console.print(f"  [dim]Think: {content}[/dim]")
            elif msg.event_type == OutboundEventType.TOOL_CALL:
                console.print(f"  [dim]├─ Calling: {msg.content}[/dim]")
            elif msg.event_type == OutboundEventType.TOOL_RESULT:
                # Truncate long tool results
                content = msg.content
                if len(content) > 150:
                    content = content[:150] + "..."
                console.print(f"  [dim]└─ Result: {content}[/dim]")

    async def _run_interactive(self) -> None:
        """Run in interactive mode."""
        from vikingbot.cli.commands import (
            _flush_pending_tty_input,
            _init_prompt_session,
            _is_exit_command,
            _restore_terminal,
            __logo__,
            console,
            _read_interactive_input_async,
        )

        _init_prompt_session()

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        while self._running:
            try:
                _flush_pending_tty_input()

                user_input = await _read_interactive_input_async()
                command = user_input.strip()

                if not command:
                    continue

                if _is_exit_command(command):
                    _restore_terminal()
                    console.print("\nGoodbye!")
                    break

                # Reset and send message
                self._response_received.clear()
                self._last_response = None

                sender_id = self.sender or "user"
                msg = InboundMessage(
                    session_key=SessionKey(
                        type="cli",
                        channel_id=self.config.channel_id(),
                        chat_id=self.session_id,
                    ),
                    sender_id=sender_id,
                    content=user_input,
                )
                await self.bus.publish_inbound(msg)

                # Wait for response
                await self._response_received.wait()

            except KeyboardInterrupt:
                _restore_terminal()
                console.print("\nGoodbye!")
                break
            except EOFError:
                _restore_terminal()
                console.print("\nGoodbye!")
                break
