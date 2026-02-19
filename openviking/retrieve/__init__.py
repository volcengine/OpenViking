# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Retrieval module for OpenViking.

Provides intent-driven hierarchical context retrieval.
"""

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking_cli.retrieve.types import (
    ContextType,
    FindResult,
    MatchedContext,
    QueryPlan,
    QueryResult,
    RelatedContext,
    TypedQuery,
)

__all__ = [
    # Types
    "ContextType",
    "TypedQuery",
    "QueryPlan",
    "RelatedContext",
    "MatchedContext",
    "QueryResult",
    "FindResult",
    # Retriever
    "HierarchicalRetriever",
]
