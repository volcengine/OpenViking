"""Trigger information for the Dashboard export of model calls."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from .fact_context import event_context

_source_type: ContextVar[str | None] = ContextVar("dashboard_token_source_type", default=None)
_task_sources = frozenset({"session_commit", "add_skill", "content_write", "reindex", "compile"})
_semantic_sources = {
    "content_write": "content_write",
    "reindex": "reindex",
    "skill_ingest": "add_skill",
}


@contextmanager
def bind_dashboard_token_source_type(source_type: str | None) -> Iterator[None]:
    token = _source_type.set(source_type)
    try:
        yield
    finally:
        _source_type.reset(token)


def get_dashboard_token_source_type(
    task_type: str | None = None, *, generation_trigger: str | None = None
) -> str | None:
    source_type = _source_type.get()
    if source_type:
        return source_type
    if task_type is None:
        task_type = event_context("embedding.call").get("task_type")
    if task_type == "admin_reindex":
        return "reindex"
    if isinstance(task_type, str) and task_type in _task_sources:
        return task_type
    return _semantic_sources.get(generation_trigger) if generation_trigger else None
