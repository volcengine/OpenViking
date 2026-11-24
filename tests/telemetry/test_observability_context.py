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
import subprocess
import sys

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
)
from openviking.telemetry.span_models import (
    OperationSpanAttributes,
    RootSpanAttributes,
)


class TestObservabilityContextBasics:
    """
    Basic tests for ObservabilityContext data class.
    """

    def test_to_log_fields_empty(self) -> None:
        """
        Test that to_log_fields returns basic fields even when empty.
        """
        ctx = ObservabilityContext()

        fields = ctx.to_log_fields()

        assert fields == {"trace_id": "", "span_id": ""}

    def test_import_from_fresh_interpreter_does_not_trigger_circular_import(self) -> None:
        """
        Test that observability context can be imported in a fresh interpreter.

        This guards against package-level import cycles between
        ``openviking.observability.context`` and ``openviking_cli.utils``.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib; importlib.import_module('openviking.observability.context')",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr


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

        async def _worker(account_id: str) -> str | None:
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
                assert root_context is not None
                return root_context.account_id
            finally:
                reset_observability_context(token)

        accounts = ["acct-a", "acct-b", "acct-c"]
        assert await asyncio.gather(*(_worker(account) for account in accounts)) == accounts

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
                assert fields["http_method"] == "POST"
                assert fields["http_route"] == "/api/v1/sessions"
                assert fields["operation"] == "session.commit"
                assert fields["telemetry_id"] == "tm-789"
                assert fields["status"] == "ok"

            finally:
                reset_observability_context(token_operation)

            # After resetting operation context, root context should still be there
            after_op_reset = get_root_observability_context()
            assert after_op_reset is not None
            assert after_op_reset.account_id == "acct-456"
            assert get_operation_observability_context() is None

        finally:
            reset_observability_context(token_root)

        assert get_root_observability_context() is None
