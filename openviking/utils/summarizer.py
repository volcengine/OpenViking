# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Summarizer for OpenViking.

Handles summarization and key information extraction.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from openviking.core.directories import get_context_type_for_uri
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking_cli.utils import get_logger
from openviking_cli.utils.uri import VikingURI

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
        lifecycle_lock_handle_id: str = "",
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

        def is_resources_root(uri: str) -> bool:
            return (uri or "").rstrip("/") == "viking://resources"

        async def list_top_children(temp_uri: str) -> List[Tuple[str, str]]:
            viking_fs = get_viking_fs()
            entries = await viking_fs.ls(temp_uri, show_all_hidden=True, ctx=ctx)
            children: List[Tuple[str, str]] = []
            for entry in entries:
                name = entry.get("name", "")
                if not name or name in {".", ".."}:
                    continue
                child_temp_uri = VikingURI(temp_uri).join(name).uri
                children.append((name, child_temp_uri))
            return children

        for uri, temp_uri in zip(resource_uris, temp_uris, strict=True):
            # Determine context_type based on URI
            context_type = get_context_type_for_uri(uri)

            enqueue_units: List[Tuple[str, str]] = []
            if is_resources_root(uri) and uri != temp_uri:
                children = await list_top_children(temp_uri)
                if not children:
                    return {
                        "status": "error",
                        "message": f"no top-level import items found under temp uri: {temp_uri}",
                    }
                for name, child_temp_uri in children:
                    child_target_uri = VikingURI("viking://resources").join(name).uri
                    enqueue_units.append((child_target_uri, child_temp_uri))
            else:
                enqueue_units.append((uri, temp_uri))

            for target_uri, source_uri in enqueue_units:
                msg = SemanticMsg(
                    uri=source_uri,
                    context_type=context_type,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    agent_id=ctx.user.agent_id,
                    role=ctx.role.value,
                    skip_vectorization=skip_vectorization,
                    telemetry_id=telemetry.telemetry_id,
                    target_uri=target_uri if target_uri != source_uri else None,
                    lifecycle_lock_handle_id=lifecycle_lock_handle_id,
                    is_code_repo=kwargs.get("is_code_repo", False),
                )
                await semantic_queue.enqueue(msg)
                if msg.telemetry_id:
                    get_request_wait_tracker().register_semantic_root(msg.telemetry_id, msg.id)
                enqueued_count += 1
                logger.info(
                    f"Enqueued semantic generation for: {target_uri} (skip_vectorization={skip_vectorization})"
                )

        return {"status": "success", "enqueued_count": enqueued_count}
