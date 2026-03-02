"""Tests for Telegram channel."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vikingbot.channels.telegram import TelegramChannel
from vikingbot.bus.message import Message, MessageType


class TestTelegramChannel:
    """Tests for TelegramChannel."""

    @pytest.fixture
    def telegram_channel(self):
        """Create a Telegram channel instance."""
        config = {
            "bot_token": "test-token-12345",
            "webhook_url": "https://example.com/webhook",
        }
        return TelegramChannel(config=config)

    @pytest.mark.asyncio
    async def test_initialization(self, telegram_channel):
        """Test channel initialization."""
        assert telegram_channel.config["bot_token"] == "test-token-12345"
        assert telegram_channel.config["webhook_url"] == "https://example.com/webhook"

    @pytest.mark.asyncio
    @patch("vikingbot.channels.telegram.Bot")
    async def test_start(self, mock_bot_class, telegram_channel):
        """Test starting the channel."""
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock()
        mock_bot_class.return_value = mock_bot

        await telegram_channel.start()

        mock_bot_class.assert_called_once_with(token="test-token-12345")
        mock_bot.set_webhook.assert_called_once_with(url="https://example.com/webhook")

    @pytest.mark.asyncio
    @patch("vikingbot.channels.telegram.Bot")
    async def test_stop(self, mock_bot_class, telegram_channel):
        """Test stopping the channel."""
        mock_bot = MagicMock()
        mock_bot.delete_webhook = AsyncMock()
        mock_bot_class.return_value = mock_bot

        telegram_channel._bot = mock_bot
        await telegram_channel.stop()

        mock_bot.delete_webhook.assert_called_once()

    @pytest.mark.asyncio
    @patch("vikingbot.channels.telegram.Bot")
    async def test_send_message(self, mock_bot_class, telegram_channel):
        """Test sending a message."""
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot

        telegram_channel._bot = mock_bot

        message = Message(
            id="msg-001",
            content="Hello from bot",
            user_id="bot",
            channel="telegram",
            chat_id="123456",
        )

        await telegram_channel.send(message)

        mock_bot.send_message.assert_called_once_with(
            chat_id="123456",
            text="Hello from bot",
        )

    @pytest.mark.asyncio
    @patch("vikingbot.channels.telegram.Bot")
    async def test_send_message_with_reply(self, mock_bot_class, telegram_channel):
        """Test sending a reply message."""
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot

        telegram_channel._bot = mock_bot

        message = Message(
            id="msg-002",
            content="Reply message",
            user_id="bot",
            channel="telegram",
            chat_id="123456",
            reply_to="original-msg-id",
        )

        await telegram_channel.send(message)

        mock_bot.send_message.assert_called_once_with(
            chat_id="123456",
            text="Reply message",
            reply_to_message_id="original-msg-id",
        )

    def test_parse_update(self, telegram_channel):
        """Test parsing Telegram update."""
        update_data = {
            "update_id": 123456789,
            "message": {
                "message_id": 1,
                "from": {
                    "id": 123456,
                    "first_name": "Test",
                    "username": "testuser",
                },
                "chat": {
                    "id": 123456,
                    "type": "private",
                },
                "date": 1234567890,
                "text": "Hello bot",
            },
        }

        message = telegram_channel.parse_update(update_data)

        assert message.id == "1"
        assert message.content == "Hello bot"
        assert message.user_id == "123456"
        assert message.channel == "telegram"
        assert message.chat_id == "123456"
        assert message.username == "testuser"

    def test_parse_update_with_photo(self, telegram_channel):
        """Test parsing Telegram update with photo."""
        update_data = {
            "update_id": 123456790,
            "message": {
                "message_id": 2,
                "from": {"id": 123456, "first_name": "Test"},
                "chat": {"id": 123456, "type": "private"},
                "date": 1234567891,
                "photo": [
                    {"file_id": "small", "file_unique_id": "s1", "width": 100, "height": 100},
                    {"file_id": "large", "file_unique_id": "l1", "width": 500, "height": 500},
                ],
                "caption": "Photo caption",
            },
        }

        message = telegram_channel.parse_update(update_data)

        assert message.id == "2"
        assert message.type == MessageType.IMAGE
        assert message.content == "Photo caption"
