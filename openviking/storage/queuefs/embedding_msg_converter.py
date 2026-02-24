# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Embedding Message Converter.

This module provides a unified interface for converting Context objects
to EmbeddingMsg objects for asynchronous vector processing.
"""

from openviking.core.context import Context, ContextLevel
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class EmbeddingMsgConverter:
    """Converter for Context objects to EmbeddingMsg."""

    @staticmethod
    def from_context(context: Context, **kwargs) -> EmbeddingMsg:
        """
        Convert a Context object to EmbeddingMsg.
        """
        vectorization_text = context.get_vectorization_text()
        if not vectorization_text:
            return None

        context_dict = context.to_dict()

        # 根据 URI 判断 level 字段（用于向量索引）
        uri = context_dict.get("uri", "")
        if uri.endswith("/.abstract.md"):
            context_dict["level"] = ContextLevel.ABSTRACT
        elif uri.endswith("/.overview.md"):
            context_dict["level"] = ContextLevel.OVERVIEW
        else:
            context_dict["level"] = ContextLevel.DETAIL

        embedding_msg = EmbeddingMsg(
            message=vectorization_text,
            context_data=context_dict,
        )

        # Set any additional fields from kwargs
        for key, value in kwargs.items():
            if hasattr(embedding_msg.context_data, key) and value is not None:
                setattr(embedding_msg.context_data, key, value)
        return embedding_msg
