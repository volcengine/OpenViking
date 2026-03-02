# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Stdio channel for vikingbot - communicates via stdin/stdout."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from vikingbot.bus.events import InboundMessage, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.base import BaseChannel
from vikingbot.config.schema import SessionKey, BaseChannelConfig, ChannelType


class StdioChannelConfig(BaseChannelConfig):
    """Configuration for StdioChannel."""

    enabled: bool = True
    type: Any = "stdio"

    def channel_id(self) -> str:
        return "stdio"


class StdioChannel(BaseChannel):
    """
    Stdio channel for vikingbot.

    This channel communicates via stdin/stdout using JSON messages:
    - Reads JSON messages from stdin
    - Publishes them to the MessageBus
    - Subscribes to outbound messages and writes them to stdout
    """

    name: str = "stdio"

    def __init__(
        self, config: BaseChannelConfig, bus: MessageBus, workspace_path: Path | None = None
    ):
        super().__init__(config, bus, workspace_path)
        self._response_queue: asyncio.Queue[str] = asyncio.Queue()

    async def start(self) -> None:
        """Start the stdio channel."""
        self._running = True
        logger.info("Starting stdio channel")

        # Start reader and writer tasks
        reader_task = asyncio.create_task(self._read_stdin())
        writer_task = asyncio.create_task(self._write_stdout())

        # Send ready signal
        await self._send_json({"type": "ready"})

        try:
            await asyncio.gather(reader_task, writer_task)
        except asyncio.CancelledError:
            self._running = False
            reader_task.cancel()
            writer_task.cancel()
            await asyncio.gather(reader_task, writer_task, return_exceptions=True)

    async def stop(self) -> None:
        """Stop the stdio channel."""
        self._running = False
        logger.info("Stopping stdio channel")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message via stdout."""
        if msg.is_normal_message:
            await self._send_json({
                "type": "response",
                "content": msg.content,
            })
        else:
            # For thinking events, just send the content as-is
            await self._send_json({
                "type": "event",
                "event_type": msg.event_type.value if hasattr(msg.event_type, "value") else str(msg.event_type),
                "content": msg.content,
            })

    async def _send_json(self, data: dict[str, Any]) -> None:
        """Send JSON data to stdout."""
        try:
            line = json.dumps(data, ensure_ascii=False)
            print(line, flush=True)
        except Exception as e:
            logger.exception(f"Failed to send JSON: {e}")

    async def _read_stdin(self) -> None:
        """Read lines from stdin and publish to bus."""
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # Read a line from stdin
                line = await loop.run_in_executor(None, sys.stdin.readline)

                if not line:
                    # EOF
                    self._running = False
                    break

                line = line.strip()
                if not line:
                    continue

                # Parse the input
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    # Treat as simple text message
                    request = {"type": "message", "content": line}

                await self._handle_request(request)

            except Exception as e:
                logger.exception(f"Error reading from stdin: {e}")
                await self._send_json({
                    "type": "error",
                    "message": str(e),
                })

    async def _write_stdout(self) -> None:
        """Write responses from the queue to stdout."""
        while self._running:
            try:
                # Wait for a response with timeout
                content = await asyncio.wait_for(
                    self._response_queue.get(),
                    timeout=0.5,
                )
                await self._send_json({
                    "type": "response",
                    "content": content,
                })
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.exception(f"Error writing to stdout: {e}")

    async def _handle_request(self, request: dict[str, Any]) -> None:
        """Handle an incoming request."""
        request_type = request.get("type", "message")

        if request_type == "ping":
            await self._send_json({"type": "pong"})

        elif request_type == "message":
            content = request.get("content", "")
            chat_id = request.get("chat_id", "default")
            sender_id = request.get("sender_id", "user")

            # Create and publish inbound message
            msg = InboundMessage(
                session_key=SessionKey(
                    type="stdio",
                    channel_id=self.channel_id,
                    chat_id=chat_id,
                ),
                sender_id=sender_id,
                content=content,
            )
            await self.bus.publish_inbound(msg)

        elif request_type == "quit":
            await self._send_json({"type": "bye"})
            self._running = False

        else:
            await self._send_json({
                "type": "error",
                "message": f"Unknown request type: {request_type}",
            })
