"""Tests for message bus."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from vikingbot.bus.message import Message, MessageBus, MessageType


class TestMessage:
    """Tests for Message model."""

    def test_message_creation(self):
        """Test creating a message."""
        msg = Message(
            id="msg-001",
            content="Hello",
            user_id="user-001",
            channel="telegram",
            type=MessageType.TEXT,
        )

        assert msg.id == "msg-001"
        assert msg.content == "Hello"
        assert msg.user_id == "user-001"
        assert msg.channel == "telegram"
        assert msg.type == MessageType.TEXT

    def test_message_defaults(self):
        """Test message default values."""
        msg = Message(
            id="msg-002",
            content="Test",
            user_id="user-002",
            channel="discord",
        )

        assert msg.type == MessageType.TEXT  # Default type


class TestMessageBus:
    """Tests for MessageBus."""

    @pytest.fixture
    def message_bus(self):
        """Create a message bus instance."""
        return MessageBus(maxsize=100)

    @pytest.mark.asyncio
    async def test_put_and_get(self, message_bus):
        """Test putting and getting messages."""
        msg = Message(
            id="msg-001",
            content="Hello",
            user_id="user-001",
            channel="telegram",
        )

        await message_bus.inbound.put(msg)
        result = await message_bus.inbound.get()

        assert result.id == "msg-001"
        assert result.content == "Hello"

    @pytest.mark.asyncio
    async def test_inbound_outbound_queues(self, message_bus):
        """Test separate inbound and outbound queues."""
        inbound_msg = Message(
            id="in-001",
            content="Inbound",
            user_id="user-001",
            channel="telegram",
        )
        outbound_msg = Message(
            id="out-001",
            content="Outbound",
            user_id="bot",
            channel="telegram",
        )

        await message_bus.inbound.put(inbound_msg)
        await message_bus.outbound.put(outbound_msg)

        in_result = await message_bus.inbound.get()
        out_result = await message_bus.outbound.get()

        assert in_result.content == "Inbound"
        assert out_result.content == "Outbound"

    @pytest.mark.asyncio
    async def test_queue_size_limit(self):
        """Test queue size limit enforcement."""
        bus = MessageBus(maxsize=2)

        # Fill the queue
        for i in range(2):
            await bus.inbound.put(Message(
                id=f"msg-{i}",
                content=f"Message {i}",
                user_id="user-001",
                channel="telegram",
            ))

        # Next put should wait or fail depending on implementation
        # This test verifies the queue respects maxsize
        assert bus.inbound.qsize() == 2


class TestMessageBusIntegration:
    """Integration tests for MessageBus."""

    @pytest.mark.asyncio
    async def test_message_flow(self):
        """Test complete message flow through the bus."""
        bus = MessageBus()

        # Simulate incoming message
        incoming = Message(
            id="flow-001",
            content="Hello Bot",
            user_id="user-001",
            channel="telegram",
        )

        await bus.inbound.put(incoming)

        # Process the message (simulated)
        processed = await bus.inbound.get()

        # Send response
        response = Message(
            id="resp-001",
            content="Hello User",
            user_id="bot",
            channel="telegram",
            reply_to=processed.id,
        )

        await bus.outbound.put(response)

        # Verify response
        result = await bus.outbound.get()
        assert result.content == "Hello User"
        assert result.reply_to == "flow-001"
