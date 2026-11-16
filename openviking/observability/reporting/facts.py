"""Small, vendor-neutral adapters around committed business operations.

No document contents, prompts, API keys or Task auth fields enter these events.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from openviking.observability.context import get_observability_context
from openviking.observability.events import build_event, get_event_bus, try_publish_event

_embedding_purpose = ContextVar("metric_embedding_purpose", default="unknown")
_memory_type = ContextVar("metric_memory_type", default=None)
_memory_task_id = ContextVar("metric_memory_task_id", default=None)
_memory_source_task_ids = ContextVar("metric_memory_source_task_ids", default=None)
_memory_batch_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "metric_memory_batch_context", default=None
)
_deferred_file_changes: ContextVar[DeferredFileChanges | None] = ContextVar(
    "metric_deferred_file_changes", default=None
)


@dataclass
class DeferredFileChanges:
    events: list = field(default_factory=list)
    active: bool = True

    def publish(self, task):
        self.active = False
        for event in self.events:
            try:
                payload = dict(
                    event.payload,
                    task_id=task.task_id,
                    task_type=task.task_type,
                    mode=(getattr(task, "meta", None) or {}).get("mode"),
                )
                get_event_bus().publish(replace(event, payload=payload))
            except Exception:
                # Reporting must not change an already committed write.
                continue
        self.events.clear()


@contextmanager
def defer_file_changes():
    """Keep committed write facts until their existing root Task is created."""
    pending = DeferredFileChanges()
    token = _deferred_file_changes.set(pending)
    try:
        yield pending
    finally:
        pending.active = False
        pending.events.clear()
        _deferred_file_changes.reset(token)


def begin_request_reporting():
    # A new HTTP root must not inherit a parent's mutable Task attribution map.
    get_observability_context().extra["metric_tasks"] = {}


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
        tasks[task.task_id] = {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "uri": getattr(task, "resource_id", None),
            "mode": task.meta.get("mode"),
            "created_at": int(task.created_at * 1000),
        }


def emit_terminal(task):
    if task is None or not task.is_terminal():
        return
    try_publish_event(
        "task_terminal",
        {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "status": task.status.value,
            "account_id": task.account_id,
            "user_id": task.user_id,
            "mode": task.meta.get("mode"),
            "created_at": int(task.created_at * 1000),
            "updated_at": int(task.updated_at * 1000),
        },
    )


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
    data = {
        "uri": resolved.uri,
        "is_dir": bool(is_dir),
        "scope": classification.scope,
        "owner_user_id": resolved.owner_user_id,
        "context_type": classification.context_type,
        "level": 0 if leaf == ".abstract.md" else 1 if leaf == ".overview.md" else 2,
        "output_role": "internal_metadata"
        if internal
        else "semantic_sidecar"
        if sidecar
        else "business_file",
    }
    if ctx is not None:
        data.update(account_id=ctx.account_id, user_id=ctx.user.user_id)
    if classification.context_type == "memory":
        data["memory_type"] = _memory_type.get()
        if data["memory_type"] is None and not sidecar and not internal:
            # Manual ContentWrite reuses the registered memory schema, including
            # static single-file templates such as profile.md and identity.md.
            from openviking.session.memory.memory_type_registry import get_default_registry

            registry = get_default_registry()
            if "memories" in parts:
                index = parts.index("memories") + 1
                candidate = parts[index] if len(parts) > index else None
                schemas = registry.list_all()
                data["memory_type"] = next(
                    (
                        schema.memory_type
                        for schema in schemas
                        if candidate == schema.memory_type or candidate == schema.filename_template
                    ),
                    None,
                )
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
        data.update(
            old_uri=old_uri,
            change_type=change_type,
            operation=operation,
            is_new_file=change_type == "added" if change_type else None,
        )
        from openviking.service.task_work_index import get_task_context

        pending = _deferred_file_changes.get()

        def publish(name):
            if pending is not None and pending.active and get_task_context() is None:
                pending.events.append(build_event(name, data))
            else:
                try_publish_event(name, data)

        publish("file_mutation")
        if data.get("skill_uri") and (
            uri.endswith("/SKILL.md") or (is_dir and uri.rstrip("/") == data["skill_uri"])
        ):
            publish("skill_mutation")
    except Exception:
        # Observability must not change a committed storage operation's outcome.
        return


def emit_compile_outputs(task, changes, *, snapshot_id, committed_at):
    """Attribute committed snapshot differences, including writes by external Agents.

    Ordinary HTTP writes cannot inherit the remote Compile Task context. The
    existing Compile snapshot diff is authoritative; no file content is read.
    Stable IDs and commit time make finalization retries the same observation.
    """
    from .runtime import is_reporting_enabled

    if not is_reporting_enabled():
        return
    try:
        import hashlib
        import json

        if (
            task.task_type != "compile"
            or not snapshot_id
            or not committed_at
            or not task.task_id
            or not task.account_id
            or not task.user_id
        ):
            return
        target = task.meta["request"]["to"].rstrip("/") + "/"
        for change in changes:
            uri, kind = change["uri"], change["change_type"]
            if not uri.startswith(target) or kind not in {"added", "modified", "deleted"}:
                continue
            data = file_metadata(uri)
            if data is None or data["output_role"] != "business_file":
                continue
            identity = json.dumps(
                [task.account_id, task.user_id, task.task_id, snapshot_id, uri, kind],
                separators=(",", ":"),
            )
            data.update(
                event_id="evt_" + hashlib.sha256(identity.encode()).hexdigest(),
                account_id=task.account_id,
                user_id=task.user_id,
                task_id=task.task_id,
                task_type="compile",
                change_type=kind,
                operation="compile_commit",
                is_new_file=kind == "added",
                updated_at=int(committed_at),
            )
            try_publish_event("file_mutation", data)
    except Exception:
        # Reporting is optional and cannot interrupt output publication.
        return
