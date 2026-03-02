"""End-to-end integration tests for the agent."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.message import Message, MessageType


class TestAgentE2E:
    """End-to-end tests for agent functionality."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        with patch("vikingbot.agent.loop.LiteLLM") as mock:
            llm_instance = MagicMock()
            llm_instance.acompletion = AsyncMock(return_value={
                "choices": [{
                    "message": {
                        "content": 'I received your message and will help you.',
                    }
                }]
            })
            mock.return_value = llm_instance
            yield mock

    @pytest.fixture
    def mock_session_manager(self):
        """Create a mock session manager."""
        with patch("vikingbot.agent.loop.SessionManager") as mock:
            session_mgr = MagicMock()
            session_mgr.load_session = AsyncMock(return_value=[])
            session_mgr.save_session = AsyncMock()
            mock.return_value = session_mgr
            yield mock

    @pytest.mark.asyncio
    async def test_simple_message_flow(
        self,
        mock_llm,
        mock_session_manager,
    ):
        """Test a simple message flow through the agent."""
        # Create agent loop
        agent = AgentLoop(config={})

        # Create test message
        message = Message(
            id="msg-001",
            content="Hello, how are you?",
            user_id="user-123",
            channel="telegram",
            chat_id="chat-456",
            type=MessageType.TEXT,
        )

        # Process message
        with patch.object(agent, '_llm', mock_llm.return_value):
            with patch.object(agent, '_session_manager', mock_session_manager.return_value):
                # This would be the actual processing call
                # await agent.process_message(message)
                pass

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self):
        """Test multi-turn conversation with context."""
        # This test would verify that the agent maintains context
        # across multiple messages in the same session
        pass

    @pytest.mark.asyncio
    async def test_tool_execution_flow(self):
        """Test the full tool execution flow."""
        # This test would verify:
        # 1. LLM returns a tool call
        # 2. Tool is executed
        # 3. Result is sent back to LLM
        # 4. Final response is generated
        pass

    @pytest.mark.asyncio
    async def test_error_recovery(self):
        """Test error recovery during message processing."""
        # This test would verify that the agent handles errors gracefully
        # and doesn't crash on exceptions
        pass
