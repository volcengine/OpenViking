"""Task and memory context for event facts and token reporting."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from openviking.observability.context import get_observability_context

_embedding_purpose = ContextVar("metric_embedding_purpose", default="unknown")
_memory_type = ContextVar("metric_memory_type", default=None)
_memory_task_id = ContextVar("metric_memory_task_id", default=None)
_memory_source_task_ids = ContextVar("metric_memory_source_task_ids", default=None)
_memory_batch_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "metric_memory_batch_context", default=None
)


def get_memory_type():
    return _memory_type.get()


@contextmanager
def bind_memory_type(memory_type, *, task_id=None, source_task_ids=None):
    token = _memory_type.set(memory_type)
    task_token = _memory_task_id.set(task_id)
    sources_token = _memory_source_task_ids.set(source_task_ids)
    try:
        yield
    finally:
        _memory_source_task_ids.reset(sources_token)
        _memory_task_id.reset(task_token)
        _memory_type.reset(token)


@contextmanager
def bind_memory_batch(ctx, task_ids):
    """Report a shared batch from its inputs, never its timer's inherited root.

    A model call shared by several commits is one observation. Preserve those
    sources without duplicating usage or choosing one submitter as its owner.
    Empty request/task IDs deliberately suppress stale queue-root fallbacks.
    """
    task_ids = tuple(task_ids)
    sources = sorted({task_id for task_id in task_ids if task_id})
    task_id = sources[0] if len(sources) == 1 and all(task_ids) else ""
    token = _memory_batch_context.set(
        {
            "account_id": ctx.account_id,
            "user_id": ctx.user.user_id,
            "task_id": task_id,
            "request_id": task_id,
            "task_type": "session_commit",
            "source_task_ids": sources,
        }
    )
    try:
        yield
    finally:
        _memory_batch_context.reset(token)


@contextmanager
def bind_embedding_purpose(purpose):
    token = _embedding_purpose.set(
        purpose if purpose in {"memory", "resource", "skill", "search_query"} else "unknown"
    )
    try:
        yield
    finally:
        _embedding_purpose.reset(token)


def event_context(name):
    """Use the existing Task context, including after queue delivery/restart."""
    if name not in {
        "http.request",
        "vlm.call",
        "embedding.call",
        "understanding_call",
        "file_mutation",
        "skill_mutation",
        "task_terminal",
    }:
        return {}
    memory_task_id = _memory_task_id.get()
    memory_batch = _memory_batch_context.get()
    if memory_batch is not None and name in {
        "file_mutation",
        "skill_mutation",
        "vlm.call",
        "embedding.call",
    }:
        batch_fields = dict(memory_batch)
        sources = _memory_source_task_ids.get()
        if memory_task_id:
            batch_fields.update(
                task_id=memory_task_id,
                request_id=memory_task_id,
                source_task_ids=[memory_task_id],
            )
        elif sources is not None:
            # An explicitly ambiguous operation cannot inherit a single batch
            # submitter, even when some source Task metadata is unavailable.
            batch_fields.update(task_id="", request_id="", source_task_ids=sorted(set(sources)))
        if name == "vlm.call":
            batch_fields["usage_purpose"] = "memory_extract"
        elif name == "embedding.call":
            batch_fields["usage_purpose"] = _embedding_purpose.get()
        return batch_fields
    if name == "file_mutation" and memory_task_id:
        # Shared memory batch workers outlive their first queue request. Use
        # the committed operation's provenance without reattaching Task Work.
        return {
            "task_id": memory_task_id,
            "request_id": memory_task_id,
            "task_type": "session_commit",
        }
    from openviking.service.task_work_index import TaskExecutionContext, get_task_context

    context = get_task_context()
    if context is None:
        root = get_observability_context().root
        # The session queue owns this root and sets request_id to its persisted
        # Task ID. Extraction callbacks can retain it after losing Task Work
        # context across event loops. HTTP request IDs are never Task evidence.
        if (
            root is not None
            and root.http_method == "QUEUE"
            and root.http_route == "/queuefs/session_commit"
            and root.request_id
            and root.account_id
            and root.user_id
        ):
            context = TaskExecutionContext(
                root.request_id, root.account_id, root.user_id, task_type="session_commit"
            )
    fields: dict[str, str | None] = {}
    if context is not None:
        fields.update(
            task_id=context.task_id, account_id=context.account_id, user_id=context.user_id
        )
        if context.task_type:
            fields.update(task_type=context.task_type, mode=context.mode)
        if name == "vlm.call":
            fields["usage_purpose"] = {
                "session_commit": "memory_extract",
                "add_resource": "resource_parse",
                "content_write": "resource_parse",
                "compile": "compile",
                "add_skill": "other",
            }.get(context.task_type, "unknown")
    if name == "embedding.call":
        fields["usage_purpose"] = _embedding_purpose.get()
    return fields
