# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue consumer for target-scoped artifact relation reconciliation."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult


class ArtifactRelationsProcessor(DequeueHandlerBase):
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        try:
            payload = data.get("data", data)
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise ValueError("Artifact relation payload must be an object")
            account_id = str(payload.get("account_id") or "")
            target = str(payload.get("to") or "")
            user_id = str(payload.get("user_id") or "artifact-graph")
            if not account_id or not target:
                raise ValueError("Artifact relation payload is missing account_id or to")
            await self._graph.reconcile_target(account_id, target, user_id)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))
        return ProcessResult.success()


__all__ = ["ArtifactRelationsProcessor"]
