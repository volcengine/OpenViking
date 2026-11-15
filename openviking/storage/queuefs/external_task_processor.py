# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""QueueFS consumer for externally executed tasks."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_tracker import get_task_tracker
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult


class ExternalTaskProcessor(DequeueHandlerBase):
    def __init__(
        self,
        service: ExternalTaskService,
    ) -> None:
        self._service = service

    @staticmethod
    def _parse(data: Dict[str, Any]) -> tuple[str, str, str]:
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("External task queue payload must be an object")
        task_id = str(payload.get("task_id") or "")
        account_id = str(payload.get("account_id") or "")
        user_id = str(payload.get("user_id") or "")
        if not task_id or not account_id or not user_id:
            raise ValueError("External task queue payload is missing task ownership")
        return task_id, account_id, user_id

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        try:
            task_id, account_id, user_id = self._parse(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))
        # Escaped execution/persistence errors leave this delivery open for recovery.
        if not await self._service.execute(task_id, account_id, user_id):
            return ProcessResult.retry()
        task = await get_task_tracker().get(task_id, account_id=account_id, user_id=user_id)
        if task is not None and task.error:
            return ProcessResult.failed(task.error)
        if await get_task_tracker().is_cancellation_requested(
            task_id, account_id=account_id, user_id=user_id
        ):
            return ProcessResult.cancelled()
        return ProcessResult.success()

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.cancelled()
        try:
            task_id, account_id, user_id = self._parse(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))
        # Cancellation is settled only after provider cleanup has completed.
        await self._service.cancel_recovered(task_id, account_id, user_id)
        return ProcessResult.cancelled()


__all__ = ["ExternalTaskProcessor"]
