# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Execute a compiled :class:`ContextUpdatePlan` outside the parser pipeline.

``add_resources`` builds a :class:`ContextUpdatePlan` and drives its stages
(synchronous content commit, direct index actions, and the asynchronous
semantic plan / file refresh) inline. The single-file and batch content-write
paths need the same execution, so the shared logic lives here and both callers
reuse it. Keeping one implementation prevents the write and ingest paths from
drifting on how a plan is committed and enqueued.

The helper deliberately does not own the resource lock or the request wait
tracker: those lifecycles differ per caller (write releases the lock between
enqueue and wait; ingest hands the lock off to the queue). Callers keep that
control and only ask this module to perform the plan's own work.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

from openviking.core.context import ContextLevel
from openviking.core.namespace import context_type_for_uri
from openviking.server.identity import RequestContext
from openviking.storage.context_update_plan import ContextUpdatePlan, execute_content_tree_actions
from openviking.storage.index_action import FieldPatch, IndexAction
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.log_correlation import log_correlation
from openviking_cli.utils import VikingURI, get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PlanWork:
    """Which downstream work a plan actually requested.

    The caller finalizes ``semantic_status`` / ``vector_status`` after it waits,
    because only the caller knows the queue status once its lock is released.
    ``semantic_action`` carries the parent-refresh freshness decision so a
    single-file caller can surface a ``deferred``/``skipped`` status.
    """

    semantic_requested: bool = False
    vector_requested: bool = False
    semantic_action: Optional[str] = None


async def enqueue_direct_index_actions(actions: Any, *, ctx: RequestContext) -> bool:
    """Enqueue plan-level direct index actions (delete / upsert / field update).

    Canonical implementation shared by ``add_resources`` and the content-write
    paths. Deletes are coalesced into one message, file L2 upserts reuse
    :func:`vectorize_file`, and scalar-only mutations enqueue an ``update_fields``
    message. Returns whether any embedding work was enqueued.
    """
    if not actions:
        return False

    from openviking.storage.queuefs import get_queue_manager
    from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
    from openviking.telemetry import get_current_telemetry
    from openviking.utils.embedding_utils import _enqueue_embedding_message

    queue_manager = get_queue_manager()
    embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING, allow_create=True)
    telemetry_id = get_current_telemetry().telemetry_id
    action_counts = Counter(action.action.value for action in actions)

    delete_ids = [action.record_id for action in actions if action.action == IndexAction.DELETE]
    if delete_ids:
        message = EmbeddingMsg.for_delete(
            record_ids=delete_ids,
            context_data={
                "uri": actions[0].uri,
                "account_id": ctx.account_id,
                "owner_user_id": ctx.user.user_id,
            },
            telemetry_id=telemetry_id,
        )
        await _enqueue_embedding_message(
            embedding_queue,
            message,
            failure_message="Failed to enqueue planned vector deletes",
        )
    for action in actions:
        if action.action in {IndexAction.UPSERT, IndexAction.MERGE}:
            if action.level != int(ContextLevel.DETAIL):
                raise ValueError("Direct index upsert only supports file detail records")
            await vectorize_resource_file(
                action.uri,
                ctx=ctx,
                file_md5=action.md5,
                scalar_override={
                    **dict(action.upsert_fields),
                    "_record_id": action.record_id,
                },
                action=action.action.value,
                field_patch=action.field_patch,
            )
            continue
        if action.action != IndexAction.UPDATE_FIELDS:
            continue
        assert action.field_patch is not None
        message = EmbeddingMsg.for_update_fields(
            record_id=action.record_id,
            field_patch=action.field_patch,
            context_data={
                "uri": action.uri,
                "level": action.level,
                "account_id": ctx.account_id,
                "owner_user_id": ctx.user.user_id,
            },
            telemetry_id=telemetry_id,
        )
        await _enqueue_embedding_message(
            embedding_queue,
            message,
            failure_message=f"Failed to enqueue scalar update for {action.uri}",
        )
    logger.debug(
        "[DirectIndexActions] %s root=%s action_counts=%s action_count=%d",
        log_correlation(),
        actions[0].uri if actions else "",
        dict(action_counts),
        len(actions),
    )
    return True


async def vectorize_resource_file(
    file_uri: str,
    *,
    ctx: RequestContext,
    ingest_options: IngestOptions | None = None,
    creator_acl_grant: Any = None,
    file_md5: str | None = None,
    scalar_override: Optional[dict[str, Any]] = None,
    field_patch: FieldPatch | None = None,
    action: str = "merge",
) -> bool:
    """Enqueue a single file's L2 vector using the standard file text policy."""
    from openviking.utils.embedding_utils import vectorize_file

    parent = VikingURI(file_uri).parent
    if parent is None:
        return False
    name = file_uri.rsplit("/", 1)[-1]
    return await vectorize_file(
        file_path=file_uri,
        summary_dict={"name": name, "summary": ""},
        parent_uri=parent.uri,
        context_type=context_type_for_uri(file_uri),
        ctx=ctx,
        ingest_options=IngestOptions.from_value(ingest_options),
        creator_acl_grant=creator_acl_grant,
        file_md5=file_md5,
        scalar_override=scalar_override,
        field_patch=field_patch,
        action=action,
    )


async def commit_and_enqueue_plan(
    plan: ContextUpdatePlan,
    *,
    ctx: RequestContext,
    inline_store: Any = None,
    inline_ref: Any = None,
    target: Any = None,
    ingest_options: IngestOptions | None = None,
    file_created: bool = False,
    force_refresh: bool | None = None,
    generation_trigger: str = "content_write",
    summarizer: Any = None,
) -> PlanWork:
    """Commit content synchronously, then enqueue index + file-refresh work.

    Content-tree actions run under the caller's lease before any queue handoff.
    Direct index actions and the flat-file ``file_refresh`` are enqueued next.
    The caller owns the lock lifecycle and the request wait tracker (register
    before calling, wait/finalize after); this helper only performs the plan's
    own work and reports which downstream work it requested.

    ``force_refresh`` selects the parent-aggregation policy for ``file_refresh``:
    ``None`` always aggregates (resource ingest), while a boolean opts into the
    freshness gate so an asynchronous single-file write can defer parent L0/L1
    aggregation without dropping the changed file's own vector maintenance.
    """
    if plan.content_tree_actions:
        if target is None:
            raise ValueError("content-tree actions require a resource target")
        await execute_content_tree_actions(
            plan.content_tree_actions,
            store=inline_store,
            artifact_ref=inline_ref,
            target=target,
        )

    vector_requested = False
    if plan.direct_index_actions:
        vector_requested = await enqueue_direct_index_actions(plan.direct_index_actions, ctx=ctx)

    semantic_requested = False
    semantic_action: Optional[str] = None
    if plan.file_refresh is not None:
        if summarizer is None:
            from openviking.utils.summarizer import Summarizer

            summarizer = Summarizer(vlm_processor=None)
        refresh_result = await summarizer.refresh_file_parent(
            file_uri=plan.file_refresh.file_uri,
            ctx=ctx,
            ingest_options=IngestOptions.from_value(ingest_options),
            created=file_created,
            file_md5=plan.file_refresh.md5,
            file_abstract="",
            generation_trigger=generation_trigger,
            force_refresh=force_refresh,
        )
        semantic_requested = refresh_result.get("enqueued_count", 0) > 0
        semantic_action = refresh_result.get("semantic_action")
        vector_requested = True

    return PlanWork(
        semantic_requested=semantic_requested,
        vector_requested=vector_requested,
        semantic_action=semantic_action,
    )


__all__ = [
    "PlanWork",
    "commit_and_enqueue_plan",
    "enqueue_direct_index_actions",
    "vectorize_resource_file",
]
