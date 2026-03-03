"""Tests for Feishu channel."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vikingbot.channels.feishu import FeishuChannel
from vikingbot.bus.message import Message, MessageType


class TestFeishuChannel:
    """Tests for FeishuChannel."""

    @pytest.fixture
    def feishu_channel(self):
        """Create a Feishu channel instance."""
        config = {
            "app_id": "test-app-id",
            "app_secret": "test-app-secret",
            "encrypt_key": "test-encrypt-key",
            "verification_token": "test-verification-token",
            "webhook_url": "https://example.com/feishu/webhook",
        }
        return FeishuChannel(config=config)

    @pytest.mark.asyncio
    async def test_initialization(self, feishu_channel):
        """Test channel initialization."""
        assert feishu_channel.config["app_id"] == "test-app-id"
        assert feishu_channel.config["app_secret"] == "test-app-secret"
        assert feishu_channel.config["verification_token"] == "test-verification-token"

    @pytest.mark.asyncio
    async def test_get_access_token(self, feishu_channel):
        """Test getting access token."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "msg": "ok",
            "tenant_access_token": "test-token-123",
            "expire": 7200,
        }

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            token = await feishu_channel._get_access_token()

            assert token == "test-token-123"

    @pytest.mark.asyncio
    async def test_send_text_message(self, feishu_channel):
        """Test sending text message."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0, "msg": "ok"}

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            message = Message(
                id="msg-001",
                content="Hello Feishu",
                user_id="bot",
                channel="feishu",
                chat_id="chat-001",
            )

            await feishu_channel.send(message)

    @pytest.mark.asyncio
    async def test_send_card_message(self, feishu_channel):
        """Test sending card (rich) message."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0, "msg": "ok"}

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            message = Message(
                id="msg-002",
                content="Card content",
                user_id="bot",
                channel="feishu",
                chat_id="chat-001",
                type=MessageType.CARD,
                metadata={
                    "card": {
                        "header": {"title": {"tag": "plain_text", "content": "Title"}},
                        "elements": [],
                    }
                },
            )

            await feishu_channel.send(message)

    def test_parse_text_message(self, feishu_channel):
        """Test parsing text message from webhook."""
        webhook_data = {
            "header": {
                "event_id": "event-001",
                "event_type": "im.message.receive_v1",
                "create_time": "1234567890000",
                "token": "test-verification-token",
                "app_id": "test-app-id",
            },
            "event": {
                "sender": {
                    "sender_id": {"union_id": "user-001", "user_id": "user-001"},
                    "sender_type": "user",
                },
                "message": {
                    "message_id": "om-001",
                    "root_id": "om-000",
                    "parent_id": "om-000",
                    "create_time": "1234567890000",
                    "chat_id": "chat-001",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": '{"text": "Hello bot"}',
                },
            },
        }

        message = feishu_channel.parse_webhook(webhook_data)

        assert message.id == "om-001"
        assert message.content == "Hello bot"
        assert message.user_id == "user-001"
        assert message.channel == "feishu"
        assert message.chat_id == "chat-001"
        assert message.type == MessageType.TEXT

    def test_parse_card_action(self, feishu_channel):
        """Test parsing card action (button click) from webhook."""
        webhook_data = {
            "header": {
                "event_id": "event-002",
                "event_type": "card.action.trigger",
                "create_time": "1234567890000",
                "token": "test-verification-token",
            },
            "event": {
                "operator": {
                    "tenant_key": "tenant-001",
                    "user_id": "user-002",
                },
                "token": "action-token",
                "action": {
                    "value": {"key": "submit_button"},
                    "tag": "button",
                },
            },
        }

        message = feishu_channel.parse_webhook(webhook_data)

        assert message.user_id == "user-002"
        assert message.channel == "feishu"
        assert message.metadata.get("action") == {"value": {"key": "submit_button"}, "tag": "button"}

    def test_url_verification(self, feishu_channel):
        """Test URL verification challenge."""
        challenge_data = {
            "challenge": "test-challenge-code",
            "token": "test-verification-token",
            "type": "url_verification",
        }

        result = feishu_channel.handle_verification(challenge_data)

        assert result == {"challenge": "test-challenge-code"}
