# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Embedding Message Converter.

This module provides a unified interface for converting Context objects
to EmbeddingMsg objects for asynchronous vector processing.
"""

from openviking.core.context import Context, ContextLevel
from openviking.core.namespace import owner_fields_for_uri
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
        if context_data.get("owner_user_id") is None and context_data.get("owner_agent_id") is None:
            owner_fields = owner_fields_for_uri(
                context_data.get("uri", ""),
                user=context.user,
                account_id=context_data.get("account_id"),
            )
            context_data["owner_user_id"] = owner_fields["owner_user_id"]
            context_data["owner_agent_id"] = owner_fields["owner_agent_id"]

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
