# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Schedule automatic maintenance of a directory after its children change."""

from openviking.core.namespace import context_type_for_uri
from openviking.observability.log_safety import safe_uri_for_log
from openviking.server.identity import RequestContext
from openviking.service.task_tracker import run_tracked_operation
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import detach_task_context
from openviking.storage.abstract_overview import plan_abstract_overview_refresh
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs.semantic_msg import SemanticMsg, build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.telemetry import get_current_telemetry
from openviking_cli.utils.config import get_openviking_config
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
) -> tuple[FreshnessAction, str | None]:
    """Record directory changes and schedule a refresh when they reach the threshold."""
    from openviking.storage.queuefs import get_queue_manager
    from openviking.storage.viking_fs import get_viking_fs

    directory_uri = directory_uri.rstrip("/")
    context_type = context_type_for_uri(directory_uri)
    message = SemanticMsg(
        uri=directory_uri,
        context_type=context_type,
        recursive=False,
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        group_ids=list(ctx.group_ids),
        peer_id=ctx.user.user_id,
        role=str(ctx.role),
        telemetry_id=get_current_telemetry().telemetry_id,
        coalesce_key=build_semantic_coalesce_key(
            context_type=context_type,
            uri=directory_uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            peer_id=ctx.user.user_id,
            directory_only=True,
        ),
        generation_trigger="directory_refresh",
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
        config = get_openviking_config().semantic
        try:
            decision = await plan_abstract_overview_refresh(
                viking_fs=get_viking_fs(),
                dir_uri=directory_uri,
                changed_entries=changed_entries,
                ctx=ctx,
                overview_sample_limit=config.overview_sample_limit,
                refresh_ratio=config.freshness_refresh_ratio,
            )
        except LockAcquisitionError:
            logger.info(
                "Skipping directory freshness update because sidecars are busy uri=%s",
                safe_uri_for_log(directory_uri),
                extra={"degraded": True},
            )
            return FreshnessAction.NOOP, None
        if decision.action is not FreshnessAction.REFRESH_NOW:
            return decision.action, None
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
        return FreshnessAction.REFRESH_NOW, str(exc) or type(exc).__name__
    return FreshnessAction.REFRESH_NOW, None
