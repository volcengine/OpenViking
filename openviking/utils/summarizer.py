# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Summarizer for OpenViking.

Handles summarization and key information extraction.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional
from openviking_cli.utils import get_logger
from openviking.storage.queuefs import SemanticMsg, get_queue_manager

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext
    from openviking.parse.vlm import VLMProcessor

logger = get_logger(__name__)


class Summarizer:
    """
    Handles summarization of resources.
    """

    def __init__(self, vlm_processor: "VLMProcessor"):
        self.vlm_processor = vlm_processor

    async def summarize(
        self,
        resource_uris: List[str],
        ctx: "RequestContext",
        skip_vectorization: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Summarize the given resources.
        Triggers SemanticQueue to generate .abstract.md and .overview.md.
        """
        queue_manager = get_queue_manager()
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)

        enqueued_count = 0
        for uri in resource_uris:
            # Determine context_type based on URI
            context_type = "resource"
            if uri.startswith("viking://memory/"):
                context_type = "memory"
            elif uri.startswith("viking://agent/skills/"):
                context_type = "skill"

            msg = SemanticMsg(
                uri=uri,
                context_type=context_type,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                agent_id=ctx.user.agent_id,
                role=ctx.role.value,
                skip_vectorization=skip_vectorization,
            )
            await semantic_queue.enqueue(msg)
            enqueued_count += 1
            logger.info(
                f"Enqueued semantic generation for: {uri} (skip_vectorization={skip_vectorization})"
            )

        return {"status": "success", "enqueued_count": enqueued_count}
