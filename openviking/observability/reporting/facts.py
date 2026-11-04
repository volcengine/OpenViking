"""Small, vendor-neutral adapters around committed business operations.

No document contents, prompts, API keys or Task auth fields enter these events.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from openviking.observability.context import get_observability_context
from openviking.observability.events import try_publish_event

_embedding_purpose = ContextVar("metric_embedding_purpose", default="unknown")
_memory_type = ContextVar("metric_memory_type", default=None)


def begin_request_reporting():
    # A new HTTP root must not inherit a parent's mutable Task attribution map.
    get_observability_context().extra["metric_tasks"] = {}


@contextmanager
def bind_memory_type(memory_type):
    token = _memory_type.set(memory_type)
    try:
        yield
    finally:
        _memory_type.reset(token)


@contextmanager
def bind_embedding_purpose(purpose):
    token = _embedding_purpose.set(purpose if purpose in {"memory", "resource", "skill", "search_query"} else "unknown")
    try:
        yield
    finally:
        _embedding_purpose.reset(token)


def event_context(name):
    """Use the existing Task context, including after queue delivery/restart."""
    if name not in {"http.request", "vlm.call", "embedding.call", "understanding_call", "file_mutation", "skill_mutation", "task_terminal"}:
        return {}
    from openviking.service.task_work_index import TaskExecutionContext, get_task_context
    context = get_task_context()
    if context is None:
        root = get_observability_context().root
        # The session queue owns this root and sets request_id to its persisted
        # Task ID. Extraction callbacks can retain it after losing Task Work
        # context across event loops. HTTP request IDs are never Task evidence.
        if (root is not None and root.http_method == "QUEUE"
                and root.http_route == "/queuefs/session_commit"
                and root.request_id and root.account_id and root.user_id):
            context = TaskExecutionContext(
                root.request_id, root.account_id, root.user_id, task_type="session_commit"
            )
    fields = {}
    if context is not None:
        fields.update(task_id=context.task_id, account_id=context.account_id, user_id=context.user_id)
        if context.task_type:
            fields.update(task_type=context.task_type, mode=context.mode)
        if name == "vlm.call":
            fields["usage_purpose"] = {"session_commit": "memory_extract", "add_resource": "resource_parse",
                "content_write": "resource_parse", "compile": "compile", "add_skill": "other"}.get(context.task_type, "unknown")
    if name == "embedding.call":
        fields["usage_purpose"] = _embedding_purpose.get()
    return fields


def remember_request_task(task):
    from .runtime import is_reporting_enabled
    if not is_reporting_enabled():
        return
    from openviking.service.task_work_index import get_task_context

    context = get_observability_context()
    if context.root is None or get_task_context() is not None:
        return
    tasks = context.extra.setdefault("metric_tasks", {})
    if len(tasks) < 100:
        tasks[task.task_id] = {"task_id": task.task_id, "task_type": task.task_type,
                               "uri": getattr(task, "resource_id", None),
                               "mode": task.meta.get("mode"), "created_at": int(task.created_at * 1000)}


def emit_terminal(task):
    if task is None or not task.is_terminal():
        return
    try_publish_event("task_terminal", {
        "task_id": task.task_id, "task_type": task.task_type, "status": task.status.value,
        "account_id": task.account_id, "user_id": task.user_id,
        "mode": task.meta.get("mode"), "created_at": int(task.created_at * 1000),
        "updated_at": int(task.updated_at * 1000),
    })


async def file_exists_before(fs, path):
    """An unknown pre-state is retained as unknown, never assumed to be a create."""
    from .runtime import is_reporting_enabled
    if not is_reporting_enabled():
        return None
    from openviking.server.error_mapping import is_not_found_error
    try:
        await fs._async_agfs.stat(path)
        return True
    except Exception as error:
        return False if is_not_found_error(error) else None


def file_metadata(uri, ctx=None, *, is_dir=False):
    from openviking.core.namespace import classify_uri, resolve_uri
    from openviking.storage.internal_names import is_storage_internal_name

    classification = classify_uri(uri)
    parts = classification.parts
    if classification.scope not in {"user", "agent", "resources"}:
        return None
    leaf = parts[-1] if parts else ""
    sidecar = leaf in {".abstract.md", ".overview.md"}
    internal = (is_storage_internal_name(leaf) or leaf == ".relations.json") and not sidecar
    resolved = resolve_uri(uri)
    data = {"uri": resolved.uri, "is_dir": bool(is_dir), "scope": classification.scope,
            "owner_user_id": resolved.owner_user_id, "context_type": classification.context_type,
            "level": 0 if leaf == ".abstract.md" else 1 if leaf == ".overview.md" else 2,
            "output_role": "internal_metadata" if internal else "semantic_sidecar" if sidecar else "business_file"}
    if ctx is not None:
        data.update(account_id=ctx.account_id, user_id=ctx.user.user_id)
    if classification.context_type == "memory":
        # The real memory template is attached at MemoryUpdater, not guessed from a filename.
        data["memory_type"] = _memory_type.get()
    if "peers" in parts:
        index = parts.index("peers") + 1
        data["peer_id"] = parts[index] if len(parts) > index else None
    if classification.context_type == "skill" and "skills" in parts:
        index = parts.index("skills") + 2
        if len(parts) >= index:
            data["skill_uri"] = "viking://" + "/".join(parts[:index])
    return data


def emit_file_change(uri, ctx, change_type, *, old_uri=None, is_dir=False, operation=None):
    from .runtime import is_reporting_enabled
    if not is_reporting_enabled():
        return
    try:
        data = file_metadata(uri, ctx, is_dir=is_dir)
        if data is None:
            return
        data.update(old_uri=old_uri, change_type=change_type, operation=operation,
                    is_new_file=change_type == "added" if change_type else None)
        try_publish_event("file_mutation", data)
        if data.get("skill_uri") and (uri.endswith("/SKILL.md") or (is_dir and uri.rstrip("/") == data["skill_uri"])):
            try_publish_event("skill_mutation", data)
    except Exception:
        # Observability must not change a committed storage operation's outcome.
        return
