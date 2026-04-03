# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Embedding Message Converter.

This module provides a unified interface for converting Context objects
to EmbeddingMsg objects for asynchronous vector processing.
"""

from openviking.core.context import Context, ContextLevel
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.telemetry import get_current_telemetry
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class EmbeddingMsgConverter:
    """Converter for Context objects to EmbeddingMsg."""

    @staticmethod
    def from_context(context: Context) -> EmbeddingMsg:
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

        # Derive level field for hierarchical retrieval.
        uri = context_data.get("uri", "")
        context_level = getattr(context, "level", None)
        if context_level is not None:
            resolved_level = context_level
        elif context_data.get("level") is not None:
            resolved_level = context_data.get("level")
        elif isinstance(context.meta, dict) and context.meta.get("level") is not None:
            resolved_level = context.meta.get("level")
        elif uri.endswith("/.abstract.md"):
            resolved_level = ContextLevel.ABSTRACT
        elif uri.endswith("/.overview.md"):
            resolved_level = ContextLevel.OVERVIEW
        else:
            resolved_level = ContextLevel.DETAIL

        if isinstance(resolved_level, ContextLevel):
            resolved_level = int(resolved_level.value)
        context_data["level"] = int(resolved_level)

        embedding_msg = EmbeddingMsg(
            message=vectorization_text,
            context_data=context_data,
            telemetry_id=get_current_telemetry().telemetry_id,
        )
        return embedding_msg
