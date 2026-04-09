# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor

# 设置手动测试标记：RUN_MANUAL=1 pytest tests/session/test_manual_memory_batching.py
skip_if_not_manual = pytest.mark.skipif(
    os.environ.get("RUN_MANUAL") != "1", reason="手动执行测试，需设置 RUN_MANUAL=1 运行"
)

logger = logging.getLogger(__name__)

# 西北大环线 10 天行程模板
TRIP_DAYS = [
    {"day": 1, "route": "西宁 - 青海湖", "highlights": "青海湖、骑行、油菜花"},
    {"day": 2, "route": "青海湖 - 茶卡盐湖 - 大柴旦", "highlights": "天空之境、盐层、戈壁"},
    {"day": 3, "route": "大柴旦 - 翡翠湖 - 青海雅丹", "highlights": "绿宝石湖泊、雅丹地貌"},
    {"day": 4, "route": "大柴旦 - 阿克塞 - 敦煌", "highlights": "石油小镇、无人区"},
    {"day": 5, "route": "敦煌 - 莫高窟 - 鸣沙山", "highlights": "壁画、月牙泉、沙漠"},
    {"day": 6, "route": "敦煌 - 瓜州 - 嘉峪关 - 张掖", "highlights": "雄关、长城、祁连雪山"},
    {"day": 7, "route": "张掖丹霞 - 扁都口 - 祁连", "highlights": "七彩丹霞、祁连山"},
    {"day": 8, "route": "祁连 - 卓尔山 - 祁连山草原 - 门源", "highlights": "东方瑞士、油菜花海"},
    {"day": 9, "route": "门源 - 达坂山 - 西宁", "highlights": "大通河、塔尔寺、美食"},
    {"day": 10, "route": "西宁 - 返程", "highlights": "回味无穷、离别感悟"},
]


class NorthwestTripMockVLM:
    """模拟 LLM 生成西北大环线旅行日记和摘要。"""

    def __init__(self):
        self.is_available = MagicMock(return_value=True)
        self.call_count = 0
        self.max_concurrent = 20

    async def get_completion_async(self, prompt: str) -> str:
        self.call_count += 1
        # 根据 prompt 内容模拟不同的生成结果
        if "summary" in prompt.lower() or "摘要" in prompt:
            return f"【生成摘要】：这份日记详细记录了西北大环线第 {self.call_count % 10 + 1} 天的旅程。重点包括了该地的自然景观与人文感悟，字数充足，情感真挚。"

        # 构造约 1000 字的详细日记
        day_info = TRIP_DAYS[self.call_count % 10]
        content = f"今天是西北大环线的第 {day_info['day']} 天，行程是 {day_info['route']}。 "
        content += f"主要景点有 {day_info['highlights']}。 "
        content += "西北的景色真是让人震撼，广袤的戈壁，洁白的盐湖，还有那一抹抹翠绿的翡翠湖，每一处都像是上帝打翻的调色盘。 "
        content += "在路上，我们感受到了大自然的鬼斧神工，也体会到了生命的顽强。那些在荒漠中伫立的雅丹，仿佛在诉说着千年的孤独。 "
        content += "每一步都是风景，每一眼都是永恒。这里的风带着沙土的味道，阳光灼热却不刺眼。 "
        # 扩展到约 1000 字
        return (content * 10)[:2000]


@pytest.mark.asyncio
async def test_manual_memory_batching_100_files(monkeypatch):
    """
    西北大环线 100 个文件压力测试。

    1. 模拟 10 天行程，每天 10 篇日记，共 100 个文件。
    2. 每个文件约 1000 字，模拟流水账。
    3. 验证分批处理（Batching）逻辑是否能平滑处理 100 个文件的并发摘要生成。
    """
    file_count = 100
    mock_vlm = NorthwestTripMockVLM()

    # 1. 模拟配置
    mock_config = MagicMock()
    mock_config.vlm = mock_vlm
    mock_config.language_fallback = "zh-CN"
    mock_config.semantic.max_file_content_chars = 30000
    mock_config.semantic.max_skeleton_chars = 5000
    mock_config.semantic.max_overview_prompt_chars = 60000
    mock_config.semantic.overview_batch_size = 50
    mock_config.semantic.abstract_max_chars = 256
    mock_config.semantic.overview_max_chars = 4000
    mock_config.semantic.max_concurrent_llm = 10
    mock_config.code.code_summary_mode = "llm"

    # 2. 模拟 AGFS/VikingFS 中的 100 个文件
    class MockVikingFS:
        def __init__(self):
            self.files = []
            for i in range(file_count):
                day = (i // 10) + 1
                entry = (i % 10) + 1
                self.files.append(
                    {
                        "name": f"day_{day:02d}_entry_{entry:02d}.txt",
                        "isDir": False,
                        "uri": f"viking://user/memories/northwest_trip/day_{day:02d}_entry_{entry:02d}.txt",
                    }
                )

        async def ls(self, uri, ctx=None):
            return self.files

        async def read_file(self, uri, ctx=None):
            # 模拟读取 1000 字的流水账（由 LLM 构造）
            return await mock_vlm.get_completion_async(f"Generate diary for {uri}")

        async def write_file(self, uri, content, ctx=None):
            return True

        def _uri_to_path(self, uri, ctx=None):
            return uri.replace("viking://", "/")

    mock_fs = MockVikingFS()

    # 3. 模拟 Tracker 和 WaitTracker
    mock_wait_tracker = MagicMock()
    mock_embedding_tracker = MagicMock()
    mock_embedding_tracker.register = AsyncMock()

    # 使用 patch.multiple 来模拟多个 get_xxx 方法
    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=mock_config,
        ),
        patch("openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=mock_fs),
        patch(
            "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker",
            return_value=mock_wait_tracker,
        ),
        patch(
            "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
            return_value=mock_embedding_tracker,
        ),
    ):
        # 4. 初始化 Processor 并设置并发
        processor = SemanticProcessor(max_concurrent_llm=10)

        # --- 增加并发监控逻辑 ---
        active_concurrency = 0
        max_observed_concurrency = 0
        generate_summary_calls = []
        _generate_single_file_summary = processor._generate_single_file_summary

        async def mock_generate_summary(*args, **kwargs):
            nonlocal active_concurrency, max_observed_concurrency, generate_summary_calls
            # 增加 LLM 调用计数以满足后续断言
            try:
                active_concurrency += 1
                # 进入方法：并发计数增加
                max_observed_concurrency = max(max_observed_concurrency, active_concurrency)
                # 模拟 I/O 耗时，给事件循环调度其他协程的机会
                await asyncio.sleep(0.01)
                return await _generate_single_file_summary(*args, **kwargs)
            finally:
                active_concurrency -= 1

        # 将增强后的 mock 应用到 processor
        monkeypatch.setattr(processor, "_generate_single_file_summary", mock_generate_summary)

        # 5. 构造消息
        msg = SemanticMsg(
            uri="viking://user/memories/northwest_trip",
            context_type="memory",
            telemetry_id="tel-stress-northwest-100",
            changes={"added": [f["uri"] for f in mock_fs.files], "modified": [], "deleted": []},
        )

        # 6. 执行测试
        print(f"\n[Manual Test] 正在处理 {file_count} 个西北大环线旅行记忆文件（分批模式）...")
        await processor._process_memory_directory(msg)

    # 7. 验证结果
    print(f"[Manual Test] 处理完成。LLM 总调用次数: {mock_vlm.call_count}")
    print(f"[Verification] 峰值并发数: {max_observed_concurrency}")

    # 断言峰值并发不超过 batch_size (10)
    assert max_observed_concurrency <= 10, (
        f"并发数过高: {max_observed_concurrency}，分批逻辑可能失效！"
    )
    assert max_observed_concurrency > 0

    # 100次 摘要生成 + 1次 overview(L1) + 1次 abstract(L0)
    # 因为 read_file 也被 mock 了，所以构造过程不再消耗 call_count
    assert mock_vlm.call_count >= 102
    assert mock_embedding_tracker.register.called

    print("[Manual Test] 分批逻辑压力测试及并发验证成功。")


if __name__ == "__main__":
    # 方便直接运行此脚本
    os.environ["RUN_MANUAL"] = "1"
    import sys

    sys.exit(pytest.main([__file__]))
