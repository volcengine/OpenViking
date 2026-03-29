# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称"""
        pass

    @abstractmethod
    def instruction(self) -> str:
        """
        指令 - Provider 相关，包含 goal、conversation 等

        Returns:
            完整的指令描述
        """
        pass

    @abstractmethod
    def get_system_prompt(self, json_schema: str) -> str:
        """
        获取完整的 system prompt

        Args:
            json_schema: JSON Schema 字符串

        Returns:
            完整的 system prompt
        """
        pass

    @abstractmethod
    def get_initial_messages(
        self,
        tool_call_messages: List[Dict],
        json_schema: str,
    ) -> List[Dict[str, Any]]:
        """
        获取完整的初始消息列表

        Args:
            tool_call_messages: prefetch 产生的工具调用消息
            json_schema: JSON Schema 字符串

        Returns:
            完整的初始消息列表
        """
        pass

    @abstractmethod
    async def prefetch(
        self,
        ctx: RequestContext,
        viking_fs: VikingFS,
        transaction_handle,
        vlm,
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

    @abstractmethod
    def get_schema_directories(self) -> List[str]:
        """
        获取需要加载的 schema 目录路径（builtin + custom）

        Returns:
            schema 目录路径列表
        """
        pass

    @abstractmethod
    async def load_schemas(self):
        """
        加载所有 schema 到内部 registry（延迟加载）
        """
        pass