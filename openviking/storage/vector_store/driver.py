# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Base driver contracts for vector store backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from openviking.storage.vector_store.expr import (
    And,
    Contains,
    Eq,
    FilterExpr,
    In,
    Or,
    Prefix,
    Range,
    RawDSL,
    Regex,
    TimeRange,
)


class VectorStoreDriver(ABC):
    """Backend-specific adapter for collection operations + filter AST compilation."""

    mode: str

    @classmethod
    @abstractmethod
    def from_config(cls, config: Any) -> "VectorStoreDriver":
        """Create a driver instance from VectorDB backend config."""

    @abstractmethod
    def has_collection(self, name: str) -> bool:
        """Return whether collection exists."""

    @abstractmethod
    def get_collection(self, name: str) -> Any:
        """Return backend collection handle."""

    @abstractmethod
    def create_collection(self, name: str, meta: Dict[str, Any]) -> Any:
        """Create a collection and return backend collection handle."""

    @abstractmethod
    def drop_collection(self, name: str) -> None:
        """Drop collection."""

    @abstractmethod
    def list_collections(self) -> list[str]:
        """List all collections."""

    def close(self) -> None:
        """Release backend resources."""

    def compile_expr(self, expr: FilterExpr | None) -> Dict[str, Any]:
        """Compile a filter AST node to vectordb DSL."""
        if expr is None:
            return {}

        if isinstance(expr, RawDSL):
            return expr.payload

        if isinstance(expr, And):
            conds = [self.compile_expr(c) for c in expr.conds if c is not None]
            conds = [c for c in conds if c]
            if not conds:
                return {}
            if len(conds) == 1:
                return conds[0]
            return {"op": "and", "conds": conds}

        if isinstance(expr, Or):
            conds = [self.compile_expr(c) for c in expr.conds if c is not None]
            conds = [c for c in conds if c]
            if not conds:
                return {}
            if len(conds) == 1:
                return conds[0]
            return {"op": "or", "conds": conds}

        if isinstance(expr, Eq):
            return {"op": "must", "field": expr.field, "conds": [expr.value]}

        if isinstance(expr, In):
            return {"op": "must", "field": expr.field, "conds": list(expr.values)}

        if isinstance(expr, Prefix):
            # For path fields the current vectordb implementation uses `must` semantics.
            return {"op": "must", "field": expr.field, "conds": [expr.prefix]}

        if isinstance(expr, Range):
            payload: Dict[str, Any] = {"op": "range", "field": expr.field}
            if expr.gte is not None:
                payload["gte"] = expr.gte
            if expr.gt is not None:
                payload["gt"] = expr.gt
            if expr.lte is not None:
                payload["lte"] = expr.lte
            if expr.lt is not None:
                payload["lt"] = expr.lt
            return payload

        if isinstance(expr, Contains):
            return {
                "op": "contains",
                "field": expr.field,
                "substring": expr.substring,
            }

        if isinstance(expr, Regex):
            return {"op": "regex", "field": expr.field, "pattern": expr.pattern}

        if isinstance(expr, TimeRange):
            payload: Dict[str, Any] = {"op": "range", "field": expr.field}
            if expr.start is not None:
                payload["gte"] = expr.start
            if expr.end is not None:
                payload["lt"] = expr.end
            return payload

        raise TypeError(f"Unsupported filter expr type: {type(expr)!r}")
