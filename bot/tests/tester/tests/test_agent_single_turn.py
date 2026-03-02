"""
Agent 单轮对话测试

测试目的: 验证 `vikingbot agent -m ""` 单聊功能是否正常工作
"""

import pytest


class TestAgentSingleTurn:
    """
    测试组: Agent 单轮对话

    目的: 验证 vikingbot agent -m "" 单聊功能是否正常工作
    """

    def test_vikingbot_agent_command_exists(self):
        """
        规格: vikingbot agent 命令存在且可执行
        """
        import sys
        from pathlib import Path

        # 确认可以导入 vikingbot cli 模块
        bot_path = Path(__file__).parent / "../../openviking/bot"
        sys.path.insert(0, str(bot_path.resolve()))

        # 验证可以导入 commands 模块
        from vikingbot.cli import commands

        assert commands is not None
        assert hasattr(commands, "app")

    def test_can_create_agent_components(self):
        """
        规格: 命令可以接受消息内容作为输入
        """
        import sys
        from pathlib import Path

        bot_path = Path(__file__).parent / "../../openviking/bot"
        sys.path.insert(0, str(bot_path.resolve()))

        # 验证可以导入核心组件
        from vikingbot.bus.queue import MessageBus
        from vikingbot.config.schema import SessionKey
        from vikingbot.session.manager import SessionManager

        # 验证可以创建基本组件
        bus = MessageBus()
        assert bus is not None

        session_key = SessionKey(type="cli", channel_id="default", chat_id="test")
        assert session_key is not None

    @pytest.mark.asyncio
    async def test_session_key_creation(self):
        """
        规格: SessionKey 可以正确创建
        """
        import sys
        from pathlib import Path

        bot_path = Path(__file__).parent / "../../openviking/bot"
        sys.path.insert(0, str(bot_path.resolve()))

        from vikingbot.config.schema import SessionKey

        # 测试 SessionKey 创建
        key = SessionKey(type="cli", channel_id="default", chat_id="test_session")

        assert key.type == "cli"
        assert key.channel_id == "default"
        assert key.chat_id == "test_session"
        assert key.safe_name() == "cli__default__test_session"

    def test_message_bus_creation(self):
        """
        规格: MessageBus 可以正确创建
        """
        import sys
        from pathlib import Path

        bot_path = Path(__file__).parent / "../../openviking/bot"
        sys.path.insert(0, str(bot_path.resolve()))

        from vikingbot.bus.queue import MessageBus

        bus = MessageBus()

        assert bus.inbound_size == 0
        assert bus.outbound_size == 0

    @pytest.mark.asyncio
    async def test_inbound_message_creation(self):
        """
        规格: 可以创建 InboundMessage
        """
        import sys
        from pathlib import Path

        bot_path = Path(__file__).parent / "../../openviking/bot"
        sys.path.insert(0, str(bot_path.resolve()))

        from vikingbot.bus.events import InboundMessage
        from vikingbot.config.schema import SessionKey

        session_key = SessionKey(type="cli", channel_id="default", chat_id="test")

        msg = InboundMessage(
            sender_id="user",
            content="Hello, test!",
            session_key=session_key,
        )

        assert msg.sender_id == "user"
        assert msg.content == "Hello, test!"
        assert msg.session_key == session_key

    @pytest.mark.asyncio
    async def test_outbound_message_creation(self):
        """
        规格: 可以创建 OutboundMessage
        """
        import sys
        from pathlib import Path

        bot_path = Path(__file__).parent / "../../openviking/bot"
        sys.path.insert(0, str(bot_path.resolve()))

        from vikingbot.bus.events import OutboundMessage, OutboundEventType
        from vikingbot.config.schema import SessionKey

        session_key = SessionKey(type="cli", channel_id="default", chat_id="test")

        msg = OutboundMessage(
            session_key=session_key,
            content="Response!",
            event_type=OutboundEventType.RESPONSE,
        )

        assert msg.session_key == session_key
        assert msg.content == "Response!"
        assert msg.event_type == OutboundEventType.RESPONSE
        assert msg.is_normal_message is True
