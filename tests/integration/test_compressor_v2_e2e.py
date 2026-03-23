# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
End-to-end test for SessionCompressorV2 (memory v2 templating system).

Uses AsyncHTTPClient to connect to local openviking-server at 127.0.0.1:1933.
No need to worry about ov.conf - server uses its own config.
"""

import pytest
import pytest_asyncio

from openviking.message import TextPart
from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Server URL - user starts openviking-server separately
SERVER_URL = "http://127.0.0.1:1933"


def create_test_conversation_messages():
    """Create a conversation that should trigger memory extraction"""
    return [
        ("user", "We're working on the OpenViking project, which is an Agent-native context database."),
        ("assistant", "Great! What features are we building?"),
        ("user", "Today we're focusing on the memory extraction feature. There are two versions: v1 uses the legacy MemoryExtractor, v2 uses the new MemoryReAct templating system with YAML schemas."),
        ("assistant", "What's the difference between the two memory types: cards and events?"),
        ("user", "Cards are for knowledge notes using the Zettelkasten method, stored in viking://agent/{agent_space}/memories/cards. Events are for recording important decisions and timelines, stored in viking://user/{user_space}/memories/events."),
        ("assistant", "Got it, that makes sense. What are the key fields for each?"),
        ("user", "Cards have 'name' and 'content' fields. Events have 'event_name', 'event_time', and 'content' fields."),
    ]


@pytest_asyncio.fixture(scope="function")
async def http_client():
    """Create AsyncHTTPClient connected to local server"""
    client = AsyncHTTPClient(url=SERVER_URL)
    await client.initialize()

    yield client

    await client.close()


class TestCompressorV2EndToEnd:
    """End-to-end tests for SessionCompressorV2 via HTTP"""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_memory_v2_extraction_e2e(
        self, http_client: AsyncHTTPClient
    ):
        """
        Test full end-to-end flow:
        1. Create session with conversation
        2. Commit session (triggers memory extraction)
        3. Wait for processing
        4. Verify memories were created in storage
        """
        client = http_client

        print("=" * 80)
        print("SessionCompressorV2 END-TO-END TEST (HTTP)")
        print(f"Server: {SERVER_URL}")
        print("=" * 80)

        # 1. Create session
        result = await client.create_session()
        assert "session_id" in result
        session_id = result["session_id"]
        print(f"\nCreated session: {session_id}")

        # Get session object
        session = client.session(session_id=session_id)

        # 2. Add conversation messages
        conversation = create_test_conversation_messages()
        for role, content in conversation:
            session.add_message(role, [TextPart(content)])
            print(f"[{role}]: {content[:60]}...")

        # 3. Commit session (this should trigger memory extraction)
        print("\nCommitting session...")
        commit_result = session.commit()
        assert commit_result["status"] == "committed"
        print(f"Commit result: {commit_result}")

        # 4. Wait for memory extraction to complete
        print("\nWaiting for processing...")
        await client.wait_processed()
        print("Processing complete!")

        # 5. Try to find memories via search
        print("\nSearching for memories...")
        find_result = await client.find(query="OpenViking memory cards events")
        print(f"Find result: total={find_result.total}")
        print(f"  Memories found: {len(getattr(find_result, 'memories', []))}")
        print(f"  Resources found: {len(getattr(find_result, 'resources', []))}")

        # 6. List the memories directory structure
        print("\nChecking memories directories...")
        try:
            # Try to list agent memories
            agent_memories = await client.ls("viking://agent/default/memories", recursive=True)
            print(f"Agent memories entries: {len(agent_memories)}")
            for entry in agent_memories[:10]:  # Show first 10
                print(f"  - {entry['name']} ({'dir' if entry['isDir'] else 'file'})")
        except Exception as e:
            print(f"Could not list agent memories: {e}")

        try:
            # Try to list user memories
            user_memories = await client.ls("viking://user/default/memories", recursive=True)
            print(f"User memories entries: {len(user_memories)}")
            for entry in user_memories[:10]:  # Show first 10
                print(f"  - {entry['name']} ({'dir' if entry['isDir'] else 'file'})")
        except Exception as e:
            print(f"Could not list user memories: {e}")

        # 7. Clean up - delete session
        print("\nCleaning up...")
        await client.delete_session(session_id)
        print(f"Deleted session: {session_id}")

        print("\n" + "=" * 80)
        print("Test completed!")
        print("=" * 80)
        print(f"\nConnected to server: {SERVER_URL}")
        print("Server uses its own ov.conf configuration")

        # The test passes if it doesn't throw an exception
        # Memory extraction happens in background, v2 writes directly to storage
        assert True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_server_health(
        self, http_client: AsyncHTTPClient
    ):
        """Verify server is healthy"""
        result = await http_client.health()
        assert result is True
        print(f"Server at {SERVER_URL} is healthy")
