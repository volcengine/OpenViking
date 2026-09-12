# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-local cache for sharing query embeddings across retrieval scopes."""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Dict, Tuple

from openviking.models.embedder.base import EmbeddingInput, EmbedResult


class QueryEmbeddingCache:
    """Share one in-flight embedding task for each query within a request."""

    def __init__(self) -> None:
        self._tasks: Dict[Tuple[int, str], asyncio.Task[EmbedResult]] = {}

    @staticmethod
    def _input_key(embedding_input: EmbeddingInput) -> str:
        if isinstance(embedding_input, str):
            return embedding_input
        return json.dumps(
            embedding_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def get_or_create(
        self,
        *,
        embedder: object,
        embedding_input: EmbeddingInput,
        factory: Callable[[], Awaitable[EmbedResult]],
    ) -> EmbedResult:
        """Return the cached result, creating exactly one shared task on a miss."""
        key = (id(embedder), self._input_key(embedding_input))
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.create_task(factory())
            self._tasks[key] = task
        return await task

    @property
    def size(self) -> int:
        """Number of distinct query embeddings requested through this cache."""
        return len(self._tasks)
