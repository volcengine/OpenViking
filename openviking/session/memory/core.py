# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Extract Context Provider - 抽象接口

定义 ExtractLoop 使用的 Provider 接口，支持两种场景：
1. SessionExtractContextProvider - 从会话消息提取记忆
2. ConsolidationExtractContextProvider - 定时整理已有记忆
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import VikingFS


class ExtractContextProvider(ABC):
    """Extract Context Provider 接口"""

    @abstractmethod
    def instruction(self) -> str:
        """
        指令 - Provider 相关，包含 goal、conversation 等

        Returns:
            完整的指令描述
        """
        pass

    @abstractmethod
    async def prefetch(
        self,
    ) -> List[Dict]:
        """
        执行 prefetch

        Args:
            ctx: RequestContext
            viking_fs: VikingFS
            transaction_handle: 事务句柄
            vlm: VLM 实例

        Returns:
            预取的 tool call messages 列表
        """
        pass

    @abstractmethod
    def get_tools(self) -> List[str]:
        """
        获取可用的工具列表

        Returns:
            工具名称列表
        """
        pass

    @abstractmethod
    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        """
        获取需要参与的 memory schemas

        Args:
            ctx: RequestContext

        Returns:
            需要参与的 MemoryTypeSchema 列表
        """
        pass
