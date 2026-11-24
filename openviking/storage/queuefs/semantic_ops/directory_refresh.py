# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Schedule automatic maintenance of a directory after its children change."""

from openviking.core.namespace import context_type_for_uri
from openviking.observability.log_safety import safe_uri_for_log
from openviking.server.identity import RequestContext
from openviking.service.task_tracker import run_tracked_operation
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import detach_task_context
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.telemetry import get_current_telemetry
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


async def enqueue_directory_refresh(
    directory_uri: str,
    changed_entries: int,
    *,
    ctx: RequestContext,
    skip_vectorization: bool = False,
    propagate_to_parent: bool = True,
    dashboard_source_type: str = "",
    required: bool = False,
) -> str | None:
    """Schedule independent directory work and report any best-effort enqueue error."""
    from openviking.storage.queuefs import get_queue_manager

    directory_uri = directory_uri.rstrip("/")
    message = SemanticMsg(
        uri=directory_uri,
        context_type=context_type_for_uri(directory_uri),
        recursive=False,
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        group_ids=list(ctx.group_ids),
        peer_id=ctx.user.user_id,
        role=str(ctx.role),
        telemetry_id=get_current_telemetry().telemetry_id,
        # A request that only generates content cannot supersede indexing work.
        coalesce_key="" if skip_vectorization else f"directory|{ctx.account_id}|{directory_uri}",
        generation_trigger="directory_refresh",
        directory_changes=changed_entries,
        skip_vectorization=skip_vectorization,
        propagate_to_parent=propagate_to_parent,
        dashboard_source_type=dashboard_source_type,
    )

    async def enqueue() -> dict[str, str]:
        queue_manager = get_queue_manager()
        queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        await queue.enqueue(message)
        return {"uri": directory_uri}

    async def dispatch() -> None:
        with detach_task_context():
            await run_tracked_operation(
                "semantic_refresh",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                resource_id=directory_uri,
                operation=enqueue,
                wait=False,
                meta={"internal": True},
            )

    # The tracker records inline work in the current coroutine's context.
    # Scheduling must leave the producer's inline work intact on return.
    try:
        await run_to_completion(dispatch)
    except Exception as exc:
        if required:
            raise
        logger.info(
            "Directory refresh enqueue failed uri=%s error_type=%s",
            safe_uri_for_log(directory_uri),
            type(exc).__name__,
            extra={"degraded": True},
        )
        return str(exc) or type(exc).__name__
    return None
