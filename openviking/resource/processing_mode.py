# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource post-ingest processing modes."""

from typing import Literal

SEMANTIC_AND_VECTORS = "semantic_and_vectors"
VECTORS_ONLY = "vectors_only"
# Temporary rollout guard. Keep the queue worker alive to drain persisted
# messages, but never enqueue or execute semantic/VLM work.
SEMANTIC_QUEUE_DISABLED = True

ProcessingMode = Literal["semantic_and_vectors", "vectors_only"]
# Temporary rollout default: callers that omit processing_mode do not enqueue
# semantic/VLM work. Explicit values remain accepted for compatibility, while
# the temporary semantic-queue guard below keeps this deployment vectors-only.
DEFAULT_PROCESSING_MODE: ProcessingMode = VECTORS_ONLY
SUPPORTED_PROCESSING_MODES = frozenset({SEMANTIC_AND_VECTORS, VECTORS_ONLY})


def normalize_processing_mode(value: str | None) -> ProcessingMode:
    mode = value or DEFAULT_PROCESSING_MODE
    if mode not in SUPPORTED_PROCESSING_MODES:
        supported = ", ".join(sorted(SUPPORTED_PROCESSING_MODES))
        raise ValueError(f"Invalid processing_mode '{mode}'. Supported values: {supported}")
    return mode  # type: ignore[return-value]
