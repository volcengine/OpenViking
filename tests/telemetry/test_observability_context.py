# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Integration tests for unified observability context management.

This module tests the unified context management system that integrates:
- Root context (HTTP request-level)
- Operation context (business operation-level)
- Execution context (non-request scenarios)

Key features tested:
- Context binding and retrieval
- Context isolation between async tasks
- Integration with logging context
- Trace/span ID priority resolution
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from openviking.observability.context import (
    ObservabilityContext,
    bind_execution_context,
    bind_operation_context,
    bind_root_context,
    get_observability_context,
    get_operation_observability_context,
    get_root_observability_context,
    reset_observability_context,
    set_observability_context,
)
from openviking.telemetry.span_models import (
    OperationSpanAttributes,
    RootSpanAttributes,
)


class TestObservabilityContextBasics:
    """
    Basic tests for ObservabilityContext data class.
    """

    def test_default_context_creation(self) -> None:
        """
        Test that a default ObservabilityContext is created with None values.
        """
        ctx = ObservabilityContext()

        assert ctx.root is None
        assert ctx.operation is None
        assert ctx.execution_trace_id is None
        assert ctx.execution_span_id is None
        assert ctx.extra == {}

    def test_get_trace_id_empty(self) -> None:
        """
        Test that get_trace_id returns empty string when no context is available.
        """
        ctx = ObservabilityContext()

        assert ctx.get_trace_id() == ""

    def test_get_span_id_empty(self) -> None:
        """
        Test that get_span_id returns empty string when no context is available.
        """
        ctx = ObservabilityContext()

        assert ctx.get_span_id() == ""

    def test_to_log_fields_empty(self) -> None:
        """
        Test that to_log_fields returns basic fields even when empty.
        """
        ctx = ObservabilityContext()

        fields = ctx.to_log_fields()

        assert "trace_id" in fields
        assert "span_id" in fields
        assert fields["trace_id"] == ""
        assert fields["span_id"] == ""

    def test_bind_execution_generates_ids(self) -> None:
        """
        Test that bind_execution generates trace_id and span_id when not provided.
        """
        ctx = ObservabilityContext()
        ctx.bind_execution()

        assert ctx.execution_trace_id is not None
        assert ctx.execution_span_id is not None
        assert len(ctx.execution_trace_id) == 32  # UUID hex is 32 chars
        assert len(ctx.execution_span_id) == 16  # First 16 chars of trace_id

    def test_bind_execution_uses_provided_ids(self) -> None:
        """
        Test that bind_execution uses provided trace_id and span_id.
        """
        ctx = ObservabilityContext()
        ctx.bind_execution(trace_id="custom_trace_id", span_id="custom_span_id")

        assert ctx.execution_trace_id == "custom_trace_id"
        assert ctx.execution_span_id == "custom_span_id"

    def test_set_extra(self) -> None:
        """
        Test that set_extra adds custom attributes.
        """
        ctx = ObservabilityContext()
        ctx.set_extra("custom_key", "custom_value")
        ctx.set_extra("number_key", 42)

        assert ctx.extra["custom_key"] == "custom_value"
        assert ctx.extra["number_key"] == 42

    def test_to_log_fields_includes_extra(self) -> None:
        """
        Test that to_log_fields includes extra attributes.
        """
        ctx = ObservabilityContext()
        ctx.set_extra("custom_field", "custom_value")

        fields = ctx.to_log_fields()

        assert fields["custom_field"] == "custom_value"


class TestRootContextBinding:
    """
    Tests for root context binding and retrieval.
    """

    def test_bind_root_context(self) -> None:
        """
        Test that root context can be bound and retrieved.
        """
        root_attrs = RootSpanAttributes(
            http_method="GET",
            http_route="/test",
            request_id="req-123",
        )
        root_attrs.account_id = "acct-456"

        token = bind_root_context(root_attrs)

        try:
            retrieved = get_root_observability_context()

            assert retrieved is not None
            assert retrieved.http_method == "GET"
            assert retrieved.http_route == "/test"
            assert retrieved.request_id == "req-123"
            assert retrieved.account_id == "acct-456"
        finally:
            reset_observability_context(token)

    def test_root_context_to_log_fields(self) -> None:
        """
        Test that root context fields are included in to_log_fields.
        """
        root_attrs = RootSpanAttributes(
            http_method="POST",
            http_route="/api/v1/sessions",
            request_id="req-test",
        )
        root_attrs.account_id = "acct-test"

        token = bind_root_context(root_attrs)

        try:
            ctx = get_observability_context()
            fields = ctx.to_log_fields()

            assert fields["request_id"] == "req-test"
            assert fields["account_id"] == "acct-test"
            assert fields["http_method"] == "POST"
            assert fields["http_route"] == "/api/v1/sessions"
        finally:
            reset_observability_context(token)

    def test_reset_root_context(self) -> None:
        """
        Test that root context can be reset.
        """
        root_attrs = RootSpanAttributes(
            http_method="GET",
            http_route="/test",
            request_id="req-123",
        )

        token = bind_root_context(root_attrs)

        try:
            assert get_root_observability_context() is not None
        finally:
            reset_observability_context(token)

        # After reset, get_root_observability_context should return None
        # because the context is reset to its previous state
        retrieved = get_root_observability_context()
        assert retrieved is None


class TestOperationContextBinding:
    """
    Tests for operation context binding and retrieval.
    """

    def test_bind_operation_context(self) -> None:
        """
        Test that operation context can be bound and retrieved.
        """
        operation_attrs = OperationSpanAttributes(
            operation="search.find",
            telemetry_id="tm-123",
        )

        token = bind_operation_context(operation_attrs)

        try:
            retrieved = get_operation_observability_context()

            assert retrieved is not None
            assert retrieved.operation == "search.find"
            assert retrieved.telemetry_id == "tm-123"
        finally:
            reset_observability_context(token)

    def test_operation_context_to_log_fields(self) -> None:
        """
        Test that operation context fields are included in to_log_fields.
        """
        operation_attrs = OperationSpanAttributes(
            operation="session.commit",
            telemetry_id="tm-456",
        )
        operation_attrs.status = "ok"

        token = bind_operation_context(operation_attrs)

        try:
            ctx = get_observability_context()
            fields = ctx.to_log_fields()

            assert fields["operation"] == "session.commit"
            assert fields["telemetry_id"] == "tm-456"
            assert fields["status"] == "ok"
        finally:
            reset_observability_context(token)


class TestExecutionContextBinding:
    """
    Tests for execution context binding (context manager.
    """

    def test_bind_execution_context_manager(self) -> None:
        """
        Test that execution context can be bound using context manager.
        """
        with bind_execution_context() as (trace_id, span_id):
            assert trace_id is not None
            assert span_id is not None
            assert len(trace_id) == 32
            assert len(span_id) == 16

            ctx = get_observability_context()
            assert ctx.execution_trace_id == trace_id
            assert ctx.execution_span_id == span_id

        # After exiting context manager, execution context should be reset
        ctx_after = get_observability_context()
        assert ctx_after.execution_trace_id is None
        assert ctx_after.execution_span_id is None

    def test_bind_execution_with_custom_ids(self) -> None:
        """
        Test that execution context can be bound with custom IDs.
        """
        custom_trace_id = "custom_trace_123456789012345678901234"
        custom_span_id = "custom_span_123456"

        with bind_execution_context(
            trace_id=custom_trace_id,
            span_id=custom_span_id,
        ) as (trace_id, span_id):
            assert trace_id == custom_trace_id
            assert span_id == custom_span_id

            ctx = get_observability_context()
            assert ctx.execution_trace_id == custom_trace_id
            assert ctx.execution_span_id == custom_span_id

    def test_execution_context_trace_id_priority(self) -> None:
        """
        Test that execution context trace_id is used when no OTel span is available.
        """
        with bind_execution_context() as (trace_id, span_id):
            ctx = get_observability_context()

            # get_trace_id should return the execution trace_id
            # since no OTel span is available
            effective_trace_id = ctx.get_trace_id()
            effective_span_id = ctx.get_span_id()

            assert effective_trace_id == trace_id
            assert effective_span_id == span_id


class TestContextIsolation:
    """
    Tests for context isolation between async tasks.
    """

    @pytest.mark.asyncio
    async def test_async_task_context_isolation(self) -> None:
        """
        Test that context is isolated between concurrent async tasks.
        """
        seen: list[Optional[str]] = []

        async def _worker(account_id: str):
            root_attrs = RootSpanAttributes(
                http_method="GET",
                http_route="/test",
                request_id=f"req-{account_id}",
            )
            root_attrs.account_id = account_id

            token = bind_root_context(root_attrs)
            try:
                await asyncio.sleep(0)  # Yield to other tasks
                root_context = get_root_observability_context()
                seen.append(root_context.account_id if root_context else None)
            finally:
                reset_observability_context(token)

        await asyncio.gather(
            _worker("acct-a"),
            _worker("acct-b"),
            _worker("acct-c"),
        )

        # All three tasks should see their own context, not leaked from others
        assert sorted(seen) == ["acct-a", "acct-b", "acct-c"]

    @pytest.mark.asyncio
    async def test_nested_context_binding(self) -> None:
        """
        Test that nested context binding works correctly.
        """
        root_attrs_outer = RootSpanAttributes(
            http_method="GET",
            http_route="/outer",
            request_id="req-outer",
        )
        root_attrs_outer.account_id = "acct-outer"

        token_outer = bind_root_context(root_attrs_outer)

        try:
            # Verify outer context
            outer_ctx = get_root_observability_context()
            assert outer_ctx is not None
            assert outer_ctx.account_id == "acct-outer"

            # Bind inner context
            root_attrs_inner = RootSpanAttributes(
                http_method="POST",
                http_route="/inner",
                request_id="req-inner",
            )
            root_attrs_inner.account_id = "acct-inner"

            token_inner = bind_root_context(root_attrs_inner)

            try:
                # Verify inner context
                inner_ctx = get_root_observability_context()
                assert inner_ctx is not None
                assert inner_ctx.account_id == "acct-inner"
            finally:
                reset_observability_context(token_inner)

            # After resetting inner context, should be back to outer
            after_inner_reset = get_root_observability_context()
            assert after_inner_reset is not None
            assert after_inner_reset.account_id == "acct-outer"

        finally:
            reset_observability_context(token_outer)

        # After resetting outer context
        after_outer_reset = get_root_observability_context()
        assert after_outer_reset is None


class TestCombinedContexts:
    """
    Tests for combining multiple context types.
    """

    def test_combined_root_and_operation_context(self) -> None:
        """
        Test that root and operation contexts can be combined.
        """
        root_attrs = RootSpanAttributes(
            http_method="POST",
            http_route="/api/v1/sessions",
            request_id="req-123",
        )
        root_attrs.account_id = "acct-456"

        operation_attrs = OperationSpanAttributes(
            operation="session.commit",
            telemetry_id="tm-789",
        )
        operation_attrs.status = "ok"

        # Bind root context first
        token_root = bind_root_context(root_attrs)

        try:
            # Then bind operation context
            token_operation = bind_operation_context(operation_attrs)

            try:
                ctx = get_observability_context()
                fields = ctx.to_log_fields()

                # Both root and operation fields should be present
                assert fields["request_id"] == "req-123"
                assert fields["account_id"] == "acct-456"
                assert fields["operation"] == "session.commit"
                assert fields["telemetry_id"] == "tm-789"
                assert fields["status"] == "ok"

            finally:
                reset_observability_context(token_operation)

            # After resetting operation context, root context should still be there
            after_op_reset = get_root_observability_context()
            assert after_op_reset is not None
            assert after_op_reset.account_id == "acct-456"

        finally:
            reset_observability_context(token_root)


class TestContextManagerIntegration:
    """
    Tests for integration with context manager patterns.
    """

    def test_set_and_reset_observability_context(self) -> None:
        """
        Test that set_observability_context and reset_observability_context work correctly.
        """
        # Create a custom context
        custom_ctx = ObservabilityContext()
        custom_ctx.set_extra("custom_key", "custom_value")
        custom_ctx.bind_execution(trace_id="custom_trace", span_id="custom_span")

        # Set the context
        token = set_observability_context(custom_ctx)

        try:
            # Verify the context is set
            retrieved = get_observability_context()
            assert retrieved.execution_trace_id == "custom_trace"
            assert retrieved.execution_span_id == "custom_span"
            assert retrieved.extra["custom_key"] == "custom_value"

        finally:
            # Reset the context
            reset_observability_context(token)

        # After reset, should be back to default
        after_reset = get_observability_context()
        assert after_reset.execution_trace_id is None
        assert after_reset.execution_span_id is None


class TestLogFieldsIntegration:
    """
    Tests for log fields integration.
    """

    def test_to_log_fields_combines_all_contexts(self) -> None:
        """
        Test that to_log_fields combines all context types correctly.
        """
        ctx = ObservabilityContext()

        # Add root context
        root_attrs = RootSpanAttributes(
            http_method="GET",
            http_route="/test",
            request_id="req-123",
        )
        root_attrs.account_id = "acct-456"
        ctx.bind_root(root_attrs)

        # Add operation context
        operation_attrs = OperationSpanAttributes(
            operation="search.find",
            telemetry_id="tm-789",
        )
        operation_attrs.status = "ok"
        ctx.bind_operation(operation_attrs)

        # Add execution context
        ctx.bind_execution(trace_id="exec_trace", span_id="exec_span")

        # Add extra fields
        ctx.set_extra("extra_field", "extra_value")

        # Get log fields
        fields = ctx.to_log_fields()

        # Verify all fields are present
        assert fields["trace_id"] == "exec_trace"
        assert fields["span_id"] == "exec_span"
        assert fields["request_id"] == "req-123"
        assert fields["account_id"] == "acct-456"
        assert fields["http_method"] == "GET"
        assert fields["http_route"] == "/test"
        assert fields["operation"] == "search.find"
        assert fields["telemetry_id"] == "tm-789"
        assert fields["status"] == "ok"
        assert fields["extra_field"] == "extra_value"
