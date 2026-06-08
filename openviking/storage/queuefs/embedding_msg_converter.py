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
        vectorization_images = context.get_vectorization_images()
        if not vectorization_text and not vectorization_images:
            return None

        context_data = context.to_dict()

        # Backfill tenant fields for legacy writers that only set user/uri.
        if not context_data.get("account_id"):
            user = context_data.get("user") or {}
            context_data["account_id"] = user.get("account_id", "default")
        uri = context_data.get("uri", "")
        owner_fields = None
        if uri:
            owner_fields = owner_fields_for_uri(
                uri,
                user=context.user,
                account_id=context_data.get("account_id"),
            )
            context_data["uri"] = owner_fields["uri"]
        if context_data.get("owner_user_id") is None:
            if owner_fields is not None:
                context_data["owner_user_id"] = owner_fields["owner_user_id"]

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

        if vectorization_images:
            # Multimodal message: combine text (if any) and image references into the
            # multimodal embedding input format. Image-aware embedders consume this list;
            # text-only embedders fall back to the text part.
            parts = []
            if vectorization_text:
                parts.append({"type": "text", "text": vectorization_text})
            for image_ref in vectorization_images:
                parts.append({"type": "image_url", "image_url": {"url": image_ref}})
            message = parts
        else:
            message = vectorization_text

        embedding_msg = EmbeddingMsg(
            message=message,
            context_data=context_data,
            telemetry_id=get_current_telemetry().telemetry_id,
        )
        return embedding_msg
