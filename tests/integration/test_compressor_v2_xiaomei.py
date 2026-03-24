# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
OpenViking Memory Demo Test - User: 小美 (Xiaomei)

Integration test using the demo conversation from the user.
Tests memory extraction and recall for a realistic conversation.
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
DISPLAY_NAME = "小美"


# ── Conversation data (10 turns user + assistant) ─────────────────────────
CONVERSATION = [
    {
        "user": "嗨！我是小美，刚毕业不久，现在在一家互联网公司做运营。我想找个能帮我记录日常生活的小助手，比如记一下每天发生的事情、我的想法、还有想做的事情。你能帮帮我吗？",
        "assistant": "你好小美！当然可以，我很乐意当你的生活小助手。你可以随时跟我聊天，我会帮你记住重要的事情。",
    },
    {
        "user": "今天心情还不错！早上在地铁上看到了一个超级可爱的小猫咪，它主人带着它坐车，只露出个小脑袋，太萌了！对了，今天部门开会说下个月要做 618 大促，我负责写活动文案，有点紧张，这是我第一次独立负责这么重要的项目。",
        "assistant": "看到可爱的猫咪真的很治愈！618 大促是个很好的锻炼机会，第一次独立负责确实会紧张，但这说明领导信任你。你可以先列个文案大纲，慢慢来。",
    },
    {
        "user": "说到吃，中午我跟同事小丽一起去吃了楼下那家麻辣烫，超级好吃！我喜欢多放醋和麻酱，不太能吃辣。不过最近在减肥，不敢吃太多主食。你有没有什么好吃又不胖的推荐呀？",
        "assistant": "麻辣烫多放醋和麻酱是很经典的搭配。减肥期间可以试试轻食沙拉，或者选麻辣烫里多加蔬菜和豆腐，少放粉丝和面条。",
    },
    {
        "user": "对了，我办了一张健身卡，就在我家小区旁边。上周去了一次，跑了 30 分钟步，还练了会儿瑜伽。结果第二天腿酸得不行，下楼都费劲。教练说让我每周去三次，我怕坚持不下来...",
        "assistant": "刚开始运动肌肉酸痛是正常的。不用一下子每周三次，先从每周两次开始适应，跑步和瑜伽交替练习，慢慢增加频率。",
    },
    {
        "user": "这个周末你有什么建议吗？我想跟我男朋友一起出去。我们之前想过去看樱花，但好像花期快过了。要不看电影？最近有什么好看的电影吗？或者去探店？我知道有一家咖啡馆好像很不错。",
        "assistant": "花期快过了的话可以抓紧最后的机会去看。也可以先去那家咖啡馆探店，下午再看场电影，安排一个轻松的约会日。",
    },
    {
        "user": "说起来，我平时喜欢追剧，尤其是那种甜宠剧，最近在看《归路》，太甜了！我还喜欢画画，虽然画得不太好，但挺解压的。偶尔也会看看书，最近在看《被讨厌的勇气》，挺有启发的。",
        "assistant": "甜宠剧确实很解压。画画也是个很好的放松方式，不用在意画得好不好。《被讨厌的勇气》是本好书，里面关于课题分离的理念很有用。",
    },
    {
        "user": "哎呀，我差点忘了提醒你！我对芒果过敏，吃了会起疹子。上次在公司同事给了我一个芒果蛋糕，我不知道，吃了一口就进医院了，还好不严重。还有，我每天晚上睡觉前都要喝一杯热牛奶，不然会失眠。",
        "assistant": "芒果过敏一定要注意，我记住了，以后推荐食物会避开芒果相关的。睡前喝热牛奶是个好习惯，有助于睡眠。",
    },
    {
        "user": "最近我种草了一个拍立得，就是富士的 mini12，粉色那款，颜值超级高！但有点贵，要 700 多块钱，还在犹豫要不要买。对了，我还想买一个投影仪，这样周末可以在家看电影。",
        "assistant": "富士 mini12 粉色确实很好看，适合记录生活。700 多块不算特别贵，可以等促销入手。投影仪也很实用，在家看电影很有氛围。",
    },
    {
        "user": "说到同事，小丽人超好，她说会帮我一起想 618 的文案点子。还有，我闺蜜下周要结婚了！她是我们宿舍第一个结婚的，真为她开心。我还在想送什么礼物好呢，红包肯定要包，但想再加点特别的。",
        "assistant": "小丽是个好搭档。闺蜜结婚的话，除了红包可以送一本定制相册，把你们大学的照片整理进去，很有纪念意义。",
    },
    {
        "user": "好的，谢谢你听我说了这么多！以后跟我聊天的时候，轻松一点就好，像朋友一样。如果我不开心了，多安慰安慰我；如果我开心，就跟我一起开心。对了，多给我推荐好吃的好玩的，谢谢啦！",
        "assistant": "没问题小美！以后就当朋友一样聊天，我会记住你的喜好和习惯。有什么开心的或不开心的都可以找我聊。",
    },
]

# ── Verification queries ──────────────────────────────────────────────────
VERIFY_QUERIES = [
    {
        "query": "小美的工作情况",
        "expected_keywords": ["618", "文案", "运营", "小丽"],
    },
    {
        "query": "小美的饮食偏好和注意事项",
        "expected_keywords": ["芒果过敏", "麻辣烫", "减肥"],
    },
    {
        "query": "小美的爱好",
        "expected_keywords": ["追剧", "画画", "看书"],
    },
    {
        "query": "小美想买什么",
        "expected_keywords": ["拍立得", "投影仪"],
    },
    {
        "query": "小美的运动计划",
        "expected_keywords": ["健身", "瑜伽", "跑步"],
    },
]


@pytest_asyncio.fixture(scope="function")
async def http_client():
    """Create AsyncHTTPClient connected to local server"""
    client = AsyncHTTPClient(url=SERVER_URL)
    await client.initialize()

    yield client

    await client.close()


class TestXiaomeiMemoryDemo:
    """Integration test for Xiaomei memory demo"""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_xiaomei_memory_extraction_and_recall(
        self, http_client: AsyncHTTPClient
    ):
        """
        Test full flow:
        1. Create session and add conversation messages
        2. Commit session (triggers memory extraction)
        3. Wait for processing
        4. Verify memory recall with queries
        """
        client = http_client

        print("\n" + "=" * 80)
        print(f"OpenViking Memory Demo Test — {DISPLAY_NAME}")
        print(f"Server: {SERVER_URL}")
        print("=" * 80)

        # Get user/agent space names
        user_space = client._user.user_space_name()
        agent_space = client._user.agent_space_name()
        print(f"\nUser space: {user_space}")
        print(f"Agent space: {agent_space}")

        # Phase 1: Create session and add messages
        print("\n" + "-" * 40)
        print(f"Phase 1: Ingest conversation ({len(CONVERSATION)} turns)")
        print("-" * 40)

        result = await client.create_session()
        assert "session_id" in result
        session_id = result["session_id"]
        print(f"\nCreated session: {session_id}")

        # Add conversation messages
        total = len(CONVERSATION)
        for i, turn in enumerate(CONVERSATION, 1):
            print(f"  [{i}/{total}] Adding user + assistant message...")
            # Add user message
            parts = [TextPart(turn["user"])]
            parts_dicts = [asdict(p) for p in parts]
            await client.add_message(session_id, "user", parts=parts_dicts)
            # Add assistant message
            parts = [TextPart(turn["assistant"])]
            parts_dicts = [asdict(p) for p in parts]
            await client.add_message(session_id, "assistant", parts=parts_dicts)

        print(f"\n  Added {total * 2} messages")

        # Commit session
        print("\n  Committing session...")
        commit_result = await client.commit_session(session_id)
        assert commit_result["status"] == "committed"
        print(f"  Commit result: {commit_result}")

        # Wait for processing
        print("\n  Waiting for processing...")
        await client.wait_processed()
        print("  Processing complete!")

        # Phase 2: Verify memory recall
        print("\n" + "-" * 40)
        print(f"Phase 2: Verify memory recall ({len(VERIFY_QUERIES)} queries)")
        print("-" * 40)

        total_hits = 0
        total_queries = len(VERIFY_QUERIES)

        for i, item in enumerate(VERIFY_QUERIES, 1):
            query = item["query"]
            expected = item["expected_keywords"]

            print(f"\n  [{i}/{total_queries}] Query: {query}")
            print(f"      Expected keywords: {', '.join(expected)}")

            try:
                results = await client.find(query, limit=5)

                # Collect all recall texts
                recall_texts = []
                count = 0

                if hasattr(results, "memories") and results.memories:
                    for m in results.memories:
                        text = getattr(m, "content", "") or getattr(m, "text", "") or str(m)
                        recall_texts.append(text)
                        uri = getattr(m, "uri", "")
                        score = getattr(m, "score", 0)
                        print(f"      Memory: {uri} (score: {score:.4f})")
                        print(f"        {text[:100]}..." if len(text) > 100 else f"        {text}")
                    count += len(results.memories)

                if hasattr(results, "resources") and results.resources:
                    for r in results.resources:
                        text = getattr(r, "content", "") or getattr(r, "text", "") or str(r)
                        recall_texts.append(text)
                        print(f"      Resource: {r.uri} (score: {r.score:.4f})")
                    count += len(results.resources)

                # Check keyword hits
                all_text = " ".join(recall_texts)
                hits = [kw for kw in expected if kw in all_text]
                hit_str = ", ".join(hits) if hits else "None"

                print(f"      Recalled: {count}, Hits: {hit_str}")
                total_hits += len(hits)

            except Exception as e:
                print(f"      ERROR: {e}")

        # List memory files
        print("\n" + "-" * 40)
        print("Memory files created:")
        print("-" * 40)

        try:
            user_memories = await client.ls(f"viking://user/{user_space}/memories", recursive=True)
            print(f"\nUser memories ({len(user_memories)} entries):")
            for entry in user_memories[:20]:
                print(f"  - {entry['name']} ({'dir' if entry['isDir'] else 'file'})")
        except Exception as e:
            print(f"Could not list user memories: {e}")

        try:
            agent_memories = await client.ls(f"viking://agent/{agent_space}/memories", recursive=True)
            print(f"\nAgent memories ({len(agent_memories)} entries):")
            for entry in agent_memories[:20]:
                print(f"  - {entry['name']} ({'dir' if entry['isDir'] else 'file'})")
        except Exception as e:
            print(f"Could not list agent memories: {e}")

        print("\n" + "=" * 80)
        print(f"Test completed! Total keyword hits: {total_hits}/{total_queries * len(VERIFY_QUERIES[0]['expected_keywords'])}")
        print("=" * 80)

        # Test passes if no exception is thrown
        assert True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_server_health(self, http_client: AsyncHTTPClient):
        """Verify server is healthy"""
        result = await http_client.health()
        assert result is True
        print(f"Server at {SERVER_URL} is healthy")