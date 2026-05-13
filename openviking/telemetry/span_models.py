# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Span model definitions.

This module defines the data structures for root spans and operation spans and
provides shared field management, validation-friendly constructors, and
serialization helpers.

Key features:
- Shared field definitions to avoid hard-coded attribute names
- Type-safe dataclass-based models
- Factory helpers for consistent span/context creation
- JSON and dict serialization helpers
- Lightweight integration with OpenTelemetry attribute APIs
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class RootSpanAttributes:
    """Attributes for the root span associated with one HTTP request."""

    # HTTP semantic fields
    http_method: str
    """HTTP method, such as GET or POST."""

    http_route: str
    """Low-cardinality route template, such as `/sessions/{session_id}`."""

    # Request-level fields
    request_id: str
    """Unique request identifier."""

    http_status_code: Optional[int] = None
    """HTTP status code, populated after the response is available."""

    url_path: Optional[str] = None
    """Raw request path for debugging. This may be high-cardinality."""

    url_scheme: Optional[str] = None
    """URL scheme, such as `http` or `https`."""

    http_host: Optional[str] = None
    """HTTP host value."""

    source_type: Optional[str] = None
    """Client source type."""

    source_version: Optional[str] = None
    """Client source version."""

    # Identity fields populated after authentication
    account_id: Optional[str] = None
    """Tenant or account identifier."""

    user_id: Optional[str] = None
    """User identifier."""

    agent_id: Optional[str] = None
    """Agent identifier."""

    def to_otel_attributes(self) -> Dict[str, Any]:
        """Convert the model into an OpenTelemetry span attribute mapping."""
        attrs = {
            "http.method": self.http_method,
            "http.route": self.http_route,
            "request_id": self.request_id,
        }

        if self.http_status_code is not None:
            attrs["http.status_code"] = self.http_status_code
        if self.url_path is not None:
            attrs["url.path"] = self.url_path
        if self.url_scheme is not None:
            attrs["url.scheme"] = self.url_scheme
        if self.http_host is not None:
            attrs["http.host"] = self.http_host
        if self.source_type is not None:
            attrs["source_type"] = self.source_type
        if self.source_version is not None:
            attrs["source_version"] = self.source_version
        if self.account_id is not None:
            attrs["account_id"] = self.account_id
        if self.user_id is not None:
            attrs["user_id"] = self.user_id
        if self.agent_id is not None:
            attrs["agent_id"] = self.agent_id

        return attrs

    def to_log_fields(self) -> Dict[str, Any]:
        """Convert the root context into structured log fields."""
        fields: Dict[str, Any] = {
            "request_id": self.request_id,
            "http_method": self.http_method,
            "http_route": self.http_route,
        }
        if self.http_status_code is not None:
            fields["http_status_code"] = self.http_status_code
        if self.account_id is not None:
            fields["account_id"] = self.account_id
        if self.user_id is not None:
            fields["user_id"] = self.user_id
        if self.agent_id is not None:
            fields["agent_id"] = self.agent_id
        return fields

    def to_dict(self) -> Dict[str, Any]:
        """Return all fields as a plain dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize the model into a JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RootSpanAttributes:
        """Build a `RootSpanAttributes` instance from a dictionary."""
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> RootSpanAttributes:
        """Build a `RootSpanAttributes` instance from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


@dataclass
class OperationSpanAttributes:
    """Attributes for one business-level operation span."""

    # Business-level fields
    operation: str
    """Operation name, such as `search.find`."""

    telemetry_id: str
    """Telemetry identifier for one operation execution."""

    status: Optional[str] = None
    """Business outcome, such as `ok` or `error`."""

    # Token-related fields
    tokens_total: Optional[int] = None
    """Total token count."""

    # Vector-related fields
    vector_searches: Optional[int] = None
    """Number of vector search operations."""

    vector_scanned: Optional[int] = None
    """Number of scanned vectors."""

    vector_returned: Optional[int] = None
    """Number of returned vectors."""

    # Memory-related fields
    memory_extracted: Optional[int] = None
    """Number of extracted memory items."""

    # Error-related fields
    errors_stage: Optional[str] = None
    """Stage where the error occurred."""

    errors_error_code: Optional[str] = None
    """Error code."""

    errors_message: Optional[str] = None
    """Error message."""

    def to_otel_attributes(self) -> Dict[str, Any]:
        """Convert the model into an OpenTelemetry span attribute mapping."""
        attrs = {
            "operation": self.operation,
            "telemetry_id": self.telemetry_id,
        }

        if self.status is not None:
            attrs["status"] = self.status
        if self.tokens_total is not None:
            attrs["tokens.total"] = self.tokens_total
        if self.vector_searches is not None:
            attrs["vector.searches"] = self.vector_searches
        if self.vector_scanned is not None:
            attrs["vector.scanned"] = self.vector_scanned
        if self.vector_returned is not None:
            attrs["vector.returned"] = self.vector_returned
        if self.memory_extracted is not None:
            attrs["memory.extracted"] = self.memory_extracted
        if self.errors_stage is not None:
            attrs["errors.stage"] = self.errors_stage
        if self.errors_error_code is not None:
            attrs["errors.error_code"] = self.errors_error_code
        if self.errors_message is not None:
            attrs["errors.message"] = self.errors_message

        return attrs

    def to_log_fields(self) -> Dict[str, Any]:
        """Convert the operation context into structured log fields."""
        fields: Dict[str, Any] = {
            "operation": self.operation,
            "telemetry_id": self.telemetry_id,
        }
        if self.status is not None:
            fields["status"] = self.status
        if self.tokens_total is not None:
            fields["tokens_total"] = self.tokens_total
        return fields

    def to_dict(self) -> Dict[str, Any]:
        """Return all fields as a plain dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize the model into a JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OperationSpanAttributes:
        """Build an `OperationSpanAttributes` instance from a dictionary."""
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> OperationSpanAttributes:
        """Build an `OperationSpanAttributes` instance from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    @classmethod
    def from_telemetry_snapshot(
        cls,
        operation: str,
        telemetry_id: str,
        status: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None,
    ) -> OperationSpanAttributes:
        """Build an instance from an `OperationTelemetry` summary snapshot."""
        attrs = cls(
            operation=operation,
            telemetry_id=telemetry_id,
            status=status,
        )

        if summary:
            # Extract token-related fields from the summary payload.
            if "tokens" in summary:
                tokens = summary["tokens"]
                if "total" in tokens:
                    attrs.tokens_total = tokens["total"]

            # Extract vector-related fields from the summary payload.
            if "vector" in summary:
                vector = summary["vector"]
                if "searches" in vector:
                    attrs.vector_searches = vector["searches"]
                if "scanned" in vector:
                    attrs.vector_scanned = vector["scanned"]
                if "returned" in vector:
                    attrs.vector_returned = vector["returned"]

            # Extract memory-related fields from the summary payload.
            if "memory" in summary:
                memory = summary["memory"]
                if "extracted" in memory:
                    attrs.memory_extracted = memory["extracted"]

            # Extract error-related fields from the summary payload.
            if "errors" in summary:
                errors = summary["errors"]
                if "stage" in errors:
                    attrs.errors_stage = errors["stage"]
                if "error_code" in errors:
                    attrs.errors_error_code = errors["error_code"]
                if "message" in errors:
                    attrs.errors_message = errors["message"]

        return attrs


def create_root_span_attributes(
    *,
    http_method: str,
    http_route: str,
    request_id: str,
    url_path: Optional[str] = None,
    url_scheme: Optional[str] = None,
    http_host: Optional[str] = None,
    source_type: Optional[str] = None,
    source_version: Optional[str] = None,
) -> RootSpanAttributes:
    """Factory helper for `RootSpanAttributes`."""
    return RootSpanAttributes(
        http_method=http_method,
        http_route=http_route,
        request_id=request_id,
        url_path=url_path,
        url_scheme=url_scheme,
        http_host=http_host,
        source_type=source_type,
        source_version=source_version,
    )


def create_operation_span_attributes(
    *,
    operation: str,
    telemetry_id: str,
    status: Optional[str] = None,
) -> OperationSpanAttributes:
    """Factory helper for `OperationSpanAttributes`."""
    return OperationSpanAttributes(
        operation=operation,
        telemetry_id=telemetry_id,
        status=status,
    )


def update_root_span_identity(
    *,
    request_state: Any,
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> bool:
    """
    Update identity fields on `RootSpanAttributes` and mirror them into the current span.

    This helper is used after authentication completes, when account or user identity becomes
    available only after the root request span has already been created.
    """
    try:
        # Ensure a root observability model is attached to `request.state`.
        if not hasattr(request_state, "root_span_attrs"):
            return False

        root_attrs = request_state.root_span_attrs
        if not isinstance(root_attrs, RootSpanAttributes):
            return False

        # Update identity fields in place.
        if account_id is not None:
            root_attrs.account_id = account_id
        if user_id is not None:
            root_attrs.user_id = user_id
        if agent_id is not None:
            root_attrs.agent_id = agent_id

        # Best-effort sync into the active OTel span, if one exists.
        try:
            from opentelemetry import trace as otel_trace

            current_span = otel_trace.get_current_span()
            if current_span is not None and current_span.is_recording():
                current_span.set_attributes(root_attrs.to_otel_attributes())
        except Exception:
            pass

        return True
    except Exception:
        return False


__all__ = [
    "RootSpanAttributes",
    "OperationSpanAttributes",
    "create_root_span_attributes",
    "create_operation_span_attributes",
    "update_root_span_identity",
]
