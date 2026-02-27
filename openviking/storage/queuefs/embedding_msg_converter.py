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

        context_data = context.to_dict()

        # Backfill tenant fields for legacy writers that only set user/uri.
        if not context_data.get("account_id"):
            user = context_data.get("user") or {}
            context_data["account_id"] = user.get("account_id", "default")
        if not context_data.get("owner_space"):
            user = context_data.get("user") or {}
            uri = context_data.get("uri", "")
            account = user.get("account_id", "default")
            user_id = user.get("user_id", "default")
            agent_id = user.get("agent_id", "default")
            from openviking_cli.session.user_id import UserIdentifier

            owner_user = UserIdentifier(account, user_id, agent_id)
            if uri.startswith("viking://agent/"):
                context_data["owner_space"] = owner_user.agent_space_name()
            elif uri.startswith("viking://user/") or uri.startswith("viking://session/"):
                context_data["owner_space"] = owner_user.user_space_name()
            else:
                context_data["owner_space"] = ""

        # Derive level field from URI for hierarchical retrieval.
        uri = context_data.get("uri", "")
        if uri.endswith("/.abstract.md"):
            context_data["level"] = ContextLevel.ABSTRACT
        elif uri.endswith("/.overview.md"):
            context_data["level"] = ContextLevel.OVERVIEW
        else:
            context_data["level"] = ContextLevel.DETAIL

        embedding_msg = EmbeddingMsg(
            message=vectorization_text,
            context_data=context_data,
        )

        # Set any additional fields from kwargs
        for key, value in kwargs.items():
            if value is not None:
                embedding_msg.context_data[key] = value
        return embedding_msg
