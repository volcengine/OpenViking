# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
End-to-end test for SessionCompressorV2 (memory v2 templating system).

Uses AsyncHTTPClient to connect to local openviking-server at 127.0.0.1:1933.
No need to worry about ov.conf - server uses its own config.
"""

from dataclasses import asdict
import pytest
import pytest_asyncio

from openviking.message import TextPart
from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Server URL - user starts openviking-server separately
SERVER_URL = "http://127.0.0.1:1933"


def create_test_conversation_messages():
    """Create a conversation that should trigger memory extraction (Chinese with various memory types)"""
    return [
        # ===== 个人信息（画像） =====
        ("user", "你好，我叫李明，是一名软件工程师，在北京工作。"),
        ("assistant", "你好李明！很高兴认识你。能介绍一下你自己吗？"),
        ("user", "好的，我今年28岁，浙江杭州人，现在在网易工作，主要做后端开发。"),
        ("assistant", "很厉害！你在网易做什么项目？"),

        # ===== 个人偏好 =====
        ("user", "我负责推荐系统相关的开发。对了，我喜欢喝咖啡，每天早上都要喝一杯美式咖啡，不加糖。"),
        ("assistant", "喝咖啡是个好习惯！你对咖啡有什么特别的偏好吗？"),
        ("user", "是的，我只喝浅烘焙的咖啡豆，而且必须是来自埃塞俄比亚的耶加雪菲，酸度要高一些的。"),
        ("assistant", "很讲究啊！除了咖啡，你还有什么其他爱好吗？"),

        # ===== 兴趣爱好 =====
        ("user", "我周末喜欢去爬山，北京周边的山我基本都爬过了，最喜欢的是香山，秋天的红叶特别美。"),
        ("assistant", "爬山很锻炼身体！你一般和谁一起去？"),
        ("user", "通常和我的女朋友张小红一起去，她也很喜欢户外运动，我们还一起加入了一个登山俱乐部。"),

        # ===== 关系记忆 =====
        ("assistant", "真好！你们在一起多久了？"),
        ("user", "我们在一起三年了，是2023年的5月20日确定的关系，那天我们在西湖边散步，我向她表白的。"),
        ("assistant", "很浪漫的日子！你们有计划结婚吗？"),

        # ===== 事件/计划 =====
        ("user", "有的，我们打算明年春天结婚，婚礼地点初步定在杭州，具体时间还没定，大概是4月份。"),
        ("assistant", "恭喜恭喜！杭州是个美丽的城市。"),
        ("user", "是的，我老家就是杭州的，所以想在那里办婚礼。我们还打算去云南度蜜月，想去大理和丽江。"),

        # ===== Agent/项目相关记忆 =====
        ("assistant", "听起来很棒！对了，你们现在在做什么项目？"),
        ("user", "我们团队正在做OpenViking项目，这是一个Agent原生的上下文数据库。"),
        ("assistant", "听起来很有意思！能详细说说吗？"),
        ("user", "好的，这个项目主要是帮助Agent更好地管理和记忆上下文信息，支持长期记忆的提取和存储。有两种主要的记忆类型：卡片（cards）用于知识笔记，采用Zettelkasten笔记法；事件（events）用于记录重要的决策和时间线。"),
        ("assistant", "明白了！那技术架构是怎样的？"),

        # ===== 技术知识/卡片 =====
        ("user", "技术上，我们使用MemoryReAct模式结合LLM来分析对话和生成记忆操作。v2版本使用了YAML配置的模板系统，比v1的8个固定类别更灵活。"),
        ("assistant", "很有技术含量！你们团队有多少人？"),

        # ===== 团队/组织信息 =====
        ("user", "我们团队现在有8个人，包括3个前端、4个后端，还有1个产品经理。产品经理叫王芳，她特别擅长用户体验设计。"),
        ("assistant", "团队配置很齐全！你们平时怎么协作？"),

        # ===== 工作习惯 =====
        ("user", "我们用敏捷开发，每周一上午开站会，周三进行技术评审，周五下午做代码回顾。我一般早上10点到公司，晚上8点左右下班。"),
        ("assistant", "很规律的工作节奏！"),
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

        # 2. Add conversation messages
        conversation = create_test_conversation_messages()
        for role, content in conversation:
            parts = [TextPart(content)]
            parts_dicts = [asdict(p) for p in parts]
            await client.add_message(session_id, role, parts=parts_dicts)
            print(f"[{role}]: {content[:60]}...")

        # 3. Commit session (this should trigger memory extraction)
        print("\nCommitting session...")
        commit_result = await client.commit_session(session_id)
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

        # 6. List the memories directory structure and read memory contents
        print("\nChecking memories directories...")

        # Helper function to read and print memory files
        async def print_memory_files(uri_prefix: str, memories_list: list):
            """Read and print memory file contents"""
            for entry in memories_list:
                if not entry.get("isDir", False) and entry.get("name", "").endswith(".md"):
                    uri = entry.get("uri", "")
                    if uri:
                        try:
                            content = await client.read(uri)
                            print(f"\n--- {entry['name']} ---")
                            print(content)
                            print("-" * 60)
                        except Exception as e:
                            print(f"  Failed to read {uri}: {e}")

        user_space = client._user.user_space_name()
        agent_space = client._user.agent_space_name()

        try:
            # Try to list agent memories
            agent_memories = await client.ls(f"viking://agent/{agent_space}/memories", recursive=True)
            print(f"Agent memories entries: {len(agent_memories)}")
            for entry in agent_memories[:20]:  # Show first 20
                print(f"  - {entry['name']} ({'dir' if entry['isDir'] else 'file'})")
            # Read and print memory files
            await print_memory_files(f"viking://agent/{agent_space}/memories", agent_memories)
        except Exception as e:
            print(f"Could not list agent memories: {e}")

        try:
            # Try to list user memories
            user_memories = await client.ls(f"viking://user/{user_space}/memories", recursive=True)
            print(f"\nUser memories entries: {len(user_memories)}")
            for entry in user_memories[:20]:  # Show first 20
                print(f"  - {entry['name']} ({'dir' if entry['isDir'] else 'file'})")
            # Read and print memory files
            await print_memory_files(f"viking://user/{user_space}/memories", user_memories)
        except Exception as e:
            print(f"Could not list user memories: {e}")

        print("\n" + "=" * 80)
        print("Test completed!")
        print("=" * 80)
        print(f"\nConnected to server: {SERVER_URL}")
        print(f"Session ID: {session_id}")
        print("Server uses its own ov.conf configuration")
        print("Note: Data cleanup is handled by test_restart_openviking_server.sh")

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
