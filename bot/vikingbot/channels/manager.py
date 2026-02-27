"""Channel manager for coordinating chat channels."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.base import BaseChannel
from vikingbot.config.schema import Config, ChannelsConfig


class ChannelManager:
    """
    Manages chat channels and coordinates message routing.

    Responsibilities:
    - Initialize enabled channels (Telegram, WhatsApp, etc.)
    - Start/stop channels
    - Route outbound messages
    """

    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None

        self._init_channels()

    def _init_channels(self) -> None:
        """Initialize channels based on config."""
        from vikingbot.config.schema import ChannelType

        channels_config = self.config.channels_config
        all_channel_configs = channels_config.get_all_channels()
        workspace_path = self.config.workspace_path

        for channel_config in all_channel_configs:
            if not channel_config.enabled:
                continue

            try:
                channel = None
                if channel_config.type == ChannelType.TELEGRAM:
                    from vikingbot.channels.telegram import TelegramChannel

                    channel = TelegramChannel(
                        channel_config,
                        self.bus,
                        groq_api_key=self.config.providers.groq.api_key,
                    )

                elif channel_config.type == ChannelType.FEISHU:
                    from vikingbot.channels.feishu import FeishuChannel

                    channel = FeishuChannel(
                        channel_config,
                        self.bus,
                        workspace_path=workspace_path,
                    )

                elif channel_config.type == ChannelType.DISCORD:
                    from vikingbot.channels.discord import DiscordChannel

                    channel = DiscordChannel(
                        channel_config,
                        self.bus,
                        workspace_path=workspace_path,
                    )

                elif channel_config.type == ChannelType.WHATSAPP:
                    from vikingbot.channels.whatsapp import WhatsAppChannel

                    channel = WhatsAppChannel(
                        channel_config,
                        self.bus,
                        workspace_path=workspace_path,
                    )

                elif channel_config.type == ChannelType.MOCHAT:
                    from vikingbot.channels.mochat import MochatChannel

                    channel = MochatChannel(
                        channel_config,
                        self.bus,
                        workspace_path=workspace_path,
                    )

                elif channel_config.type == ChannelType.DINGTALK:
                    from vikingbot.channels.dingtalk import DingTalkChannel

                    channel = DingTalkChannel(
                        channel_config,
                        self.bus,
                        workspace_path=workspace_path,
                    )

                elif channel_config.type == ChannelType.EMAIL:
                    from vikingbot.channels.email import EmailChannel

                    channel = EmailChannel(
                        channel_config,
                        self.bus,
                        workspace_path=workspace_path,
                    )

                elif channel_config.type == ChannelType.SLACK:
                    from vikingbot.channels.slack import SlackChannel

                    channel = SlackChannel(
                        channel_config,
                        self.bus,
                        workspace_path=workspace_path,
                    )

                elif channel_config.type == ChannelType.QQ:
                    from vikingbot.channels.qq import QQChannel

                    channel = QQChannel(
                        channel_config,
                        self.bus,
                        workspace_path=workspace_path,
                    )

                if channel:
                    self.channels[channel.config.channel_key()] = channel
                    logger.info(f"Channel enabled: {channel.name}")

            except ImportError as e:
                logger.warning(f"Channel {channel_config.type} not available: {e}")

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """Start a channel and log any exceptions."""
        try:
            await channel.start()
        except Exception as e:
            logger.exception(f"Failed to start channel {name}: {e}")

    async def start_all(self) -> None:
        """Start all channels and the outbound dispatcher."""
        if not self.channels:
            logger.warning("No channels enabled")
            return

        # Start outbound dispatcher
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        # Start channels
        tasks = []
        for name, channel in self.channels.items():
            logger.info(f"Starting {name} channel...")
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        # Wait for all to complete (they should run forever)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """Stop all channels and the dispatcher."""
        logger.info("Stopping all channels...")

        # Stop dispatcher
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # Stop all channels
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info(f"Stopped {name} channel")
            except Exception as e:
                logger.exception(f"Error stopping {name}: {e}")

    async def _dispatch_outbound(self) -> None:
        """Dispatch outbound messages to the appropriate channel."""
        logger.info("Outbound dispatcher started")

        while True:
            try:
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)

                # Try exact match first
                channel = self.channels.get(msg.session_key.channel_key())
                if channel:
                    try:
                        await channel.send(msg)
                    except Exception as e:
                        logger.exception(f"Error sending to {msg.session_key}: {e}")
                else:
                    logger.warning(
                        f"Unknown channel: {msg.session_key}. Available: {list(self.channels.keys())}"
                    )

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        """Get status of all channels."""
        return {
            name: {"enabled": True, "running": channel.is_running}
            for name, channel in self.channels.items()
        }

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.channels.keys())
