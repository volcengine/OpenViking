# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Unified observability context management.

This module provides a unified context management system for observability,
integrating:
- Root context (HTTP request-level)
- Operation context (business operation-level)
- Execution context (non-request scenarios)

Key features:
- Single contextvar for all observability context
- Unified binding, retrieval, and reset interfaces
- Type-safe data structures
- Automatic OTel trace/span ID integration
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from openviking.telemetry.span_models import OperationSpanAttributes, RootSpanAttributes

# Try to import opentelemetry
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import format_span_id, format_trace_id
except ImportError:
    otel_trace = None
    format_span_id = None
    format_trace_id = None

logger = logging.getLogger(__name__)


@dataclass
class ObservabilityContext:
    """
    Unified observability context data structure.

    This class integrates all observability-related context information:
    - Root context (HTTP request-level)
    - Operation context (business operation-level)
    - Execution context (non-request scenarios)

    Attributes:
        root: Root span attributes for HTTP requests.
        operation: Operation span attributes for business operations.
        execution_trace_id: Trace ID for non-request execution contexts.
        execution_span_id: Span ID for non-request execution contexts.
        extra: Additional custom attributes.
    """

    root: Optional[RootSpanAttributes] = None
    operation: Optional[OperationSpanAttributes] = None
    execution_trace_id: Optional[str] = None
    execution_span_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def get_trace_id(self) -> str:
        """
        Get the effective trace ID.

        Priority:
        1. OTel current span trace_id
        2. Execution context trace_id
        3. Empty string

        Returns:
            The effective trace ID, or empty string if not available.
        """
        # 1. Check OTel current span
        if otel_trace is not None and format_trace_id is not None:
            try:
                current_span = otel_trace.get_current_span()
                if current_span is not None and hasattr(current_span, "context"):
                    span_context = current_span.get_span_context()
                    if span_context.is_valid:
                        return format_trace_id(span_context.trace_id)
            except Exception:
                pass

        # 2. Check execution context
        if self.execution_trace_id:
            return self.execution_trace_id

        # 3. Return empty string
        return ""

    def get_span_id(self) -> str:
        """
        Get the effective span ID.

        Priority:
        1. OTel current span span_id
        2. Execution context span_id
        3. Empty string

        Returns:
            The effective span ID, or empty string if not available.
        """
        # 1. Check OTel current span
        if otel_trace is not None and format_span_id is not None:
            try:
                current_span = otel_trace.get_current_span()
                if current_span is not None and hasattr(current_span, "context"):
                    span_context = current_span.get_span_context()
                    if span_context.is_valid:
                        return format_span_id(span_context.span_id)
            except Exception:
                pass

        # 2. Check execution context
        if self.execution_span_id:
            return self.execution_span_id

        # 3. Return empty string
        return ""

    def to_log_fields(self) -> Dict[str, Any]:
        """
        Convert the context to log fields.

        Returns:
            A dictionary of log fields including trace_id, span_id, and other context fields.
        """
        fields: Dict[str, Any] = {
            "trace_id": self.get_trace_id(),
            "span_id": self.get_span_id(),
        }

        # Add root context fields
        if self.root is not None:
            root_fields = self.root.to_log_fields()
            fields.update(root_fields)

        # Add operation context fields
        if self.operation is not None:
            operation_fields = self.operation.to_log_fields()
            fields.update(operation_fields)

        # Add extra fields
        fields.update(self.extra)

        return fields

    def bind_root(self, root_attrs: RootSpanAttributes) -> "ObservabilityContext":
        """
        Bind root context.

        Args:
            root_attrs: The root span attributes to bind.

        Returns:
            Self for method chaining.
        """
        self.root = root_attrs
        return self

    def bind_operation(self, operation_attrs: OperationSpanAttributes) -> "ObservabilityContext":
        """
        Bind operation context.

        Args:
            operation_attrs: The operation span attributes to bind.

        Returns:
            Self for method chaining.
        """
        self.operation = operation_attrs
        return self

    def bind_execution(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> "ObservabilityContext":
        """
        Bind execution context for non-request scenarios.

        Args:
            trace_id: Optional trace ID. Generated automatically if not provided.
            span_id: Optional span ID. Derived from trace_id if not provided.

        Returns:
            Self for method chaining.
        """
        self.execution_trace_id = trace_id or uuid.uuid4().hex
        self.execution_span_id = span_id or self.execution_trace_id[:16]
        return self

    def set_extra(self, key: str, value: Any) -> "ObservabilityContext":
        """
        Set an extra attribute.

        Args:
            key: The attribute key.
            value: The attribute value.

        Returns:
            Self for method chaining.
        """
        self.extra[key] = value
        return self


# Global context variable
_OBSERVABILITY_CONTEXT: contextvars.ContextVar[Optional[ObservabilityContext]] = (
    contextvars.ContextVar(
        "openviking_observability_context",
        default=None,
    )
)


def get_observability_context() -> ObservabilityContext:
    """
    Get the current observability context.

    If no context is bound, a new empty context is created.

    Returns:
        The current observability context.
    """
    ctx = _OBSERVABILITY_CONTEXT.get()
    if ctx is None:
        ctx = ObservabilityContext()
        _OBSERVABILITY_CONTEXT.set(ctx)
    return ctx


def set_observability_context(ctx: ObservabilityContext) -> contextvars.Token:
    """
    Set the observability context.

    Args:
        ctx: The observability context to set.

    Returns:
        A token that can be used to reset the context.
    """
    return _OBSERVABILITY_CONTEXT.set(ctx)


def reset_observability_context(token: contextvars.Token) -> None:
    """
    Reset the observability context to a previous state.

    Args:
        token: The token returned by set_observability_context.
    """
    _OBSERVABILITY_CONTEXT.reset(token)


def _clone_observability_context(ctx: Optional[ObservabilityContext]) -> ObservabilityContext:
    """
    Clone the current observability context.

    Note:
        ContextVars restore by object reference. If we mutate the existing context
        in-place and then call `set()`, a later `reset(token)` would restore the
        same mutated object, breaking the expected reset semantics.
    """
    if ctx is None:
        return ObservabilityContext()

    # Shallow copy: span attribute objects are treated as immutable snapshots.
    return ObservabilityContext(
        root=ctx.root,
        operation=ctx.operation,
        execution_trace_id=ctx.execution_trace_id,
        execution_span_id=ctx.execution_span_id,
        extra=dict(ctx.extra),
    )


def bind_root_context(root_attrs: RootSpanAttributes) -> contextvars.Token:
    """
    Bind root context for HTTP requests.

    Args:
        root_attrs: The root span attributes to bind.

    Returns:
        A token that can be used to reset the context.
    """
    current = _OBSERVABILITY_CONTEXT.get()
    ctx = _clone_observability_context(current)
    ctx.bind_root(root_attrs)
    return set_observability_context(ctx)


def bind_operation_context(operation_attrs: OperationSpanAttributes) -> contextvars.Token:
    """
    Bind operation context for business operations.

    Args:
        operation_attrs: The operation span attributes to bind.

    Returns:
        A token that can be used to reset the context.
    """
    current = _OBSERVABILITY_CONTEXT.get()
    ctx = _clone_observability_context(current)
    ctx.bind_operation(operation_attrs)
    return set_observability_context(ctx)


@contextmanager
def bind_execution_context(
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
):
    """
    Context manager for binding execution context in non-request scenarios.

    This is useful for:
    - Process startup
    - CLI execution
    - Background tasks
    - Any code that doesn't run under an HTTP request

    Args:
        trace_id: Optional trace ID. Generated automatically if not provided.
        span_id: Optional span ID. Derived from trace_id if not provided.

    Yields:
        A tuple of (trace_id, span_id).
    """
    # Create new context with execution trace/span IDs
    ctx = ObservabilityContext()
    ctx.bind_execution(trace_id, span_id)

    token = set_observability_context(ctx)

    try:
        yield ctx.execution_trace_id, ctx.execution_span_id
    finally:
        reset_observability_context(token)


# Convenience aliases for unified context management
bind_root_observability_context = bind_root_context
reset_root_observability_context = reset_observability_context
bind_operation_observability_context = bind_operation_context


def get_root_observability_context() -> Optional[RootSpanAttributes]:
    """Get the root context from the unified observability context."""
    ctx = get_observability_context()
    return ctx.root


def get_operation_observability_context() -> Optional[OperationSpanAttributes]:
    """Get the operation context from the unified observability context."""
    ctx = get_observability_context()
    return ctx.operation


def reset_operation_observability_context(token: contextvars.Token) -> None:
    """Reset the observability context."""
    reset_observability_context(token)
