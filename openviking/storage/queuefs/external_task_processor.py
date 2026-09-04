# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""QueueFS consumer for externally executed tasks."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Coroutine, Dict, Optional

from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher
from openviking.storage.queuefs.named_queue import DequeueHandlerBase


class ExternalTaskProcessor(DequeueHandlerBase):
    def __init__(
        self,
        service: ExternalTaskService,
        service_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._service = service
        self._dispatcher = OwnerLoopDispatcher()
        if self._dispatcher.bind_current_loop() is not service_loop:
            raise ValueError("ExternalTaskProcessor must be created on the service event loop")

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

    async def _run_on_service_loop(
        self,
        factory: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        await self._dispatcher.run(factory)

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not data:
            return None
        try:
            task_id, account_id, user_id = self._parse(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None
        await self._run_on_service_loop(lambda: self._service.execute(task_id, account_id, user_id))
        self.report_success()
        return None

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not data:
            return None
        try:
            task_id, account_id, user_id = self._parse(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None
        await self._run_on_service_loop(
            lambda: self._service.cancel_recovered(task_id, account_id, user_id)
        )
        self.report_success()
        return None


__all__ = ["ExternalTaskProcessor"]
