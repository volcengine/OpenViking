# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Summarizer for OpenViking.

Handles summarization and key information extraction.
"""

from typing import TYPE_CHECKING, Any, Dict, List

from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.telemetry import get_current_telemetry
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.parse.vlm import VLMProcessor
    from openviking.server.identity import RequestContext

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

        temp_uris = kwargs.get("temp_uris", [])
        if not temp_uris:
            temp_uris = resource_uris
        if len(temp_uris) != len(resource_uris):
            logger.error(
                f"temp_uris length ({len(temp_uris)}) must match resource_uris length ({len(resource_uris)})"
            )
            return {
                "status": "error",
                "message": "temp_uris length must match resource_uris length",
            }
        enqueued_count = 0

        telemetry = get_current_telemetry()
        for uri, temp_uri in zip(resource_uris, temp_uris):
            # Determine context_type based on URI
            context_type = "resource"
            if uri.startswith("viking://memory/"):
                context_type = "memory"
            elif uri.startswith("viking://agent/skills/"):
                context_type = "skill"

            msg = SemanticMsg(
                uri=temp_uri,
                context_type=context_type,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                agent_id=ctx.user.agent_id,
                role=ctx.role.value,
                skip_vectorization=skip_vectorization,
                telemetry_id=telemetry.telemetry_id if telemetry.enabled else "",
                target_uri=uri,
            )
            await semantic_queue.enqueue(msg)
            enqueued_count += 1
            logger.info(
                f"Enqueued semantic generation for: {uri} (skip_vectorization={skip_vectorization})"
            )

        return {"status": "success", "enqueued_count": enqueued_count}
