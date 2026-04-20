# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Maintenance utilities for OpenViking.

Houses background and periodic-pass orchestrators that operate on the
persisted state (memories, resources, vector index) rather than serving
a request. First inhabitant: MemoryConsolidator (the dream-style janitor
pass).
"""

from openviking.maintenance.memory_consolidator import (
    ConsolidationResult,
    MemoryConsolidator,
)

__all__ = ["ConsolidationResult", "MemoryConsolidator"]
