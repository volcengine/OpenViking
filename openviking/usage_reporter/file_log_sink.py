# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Hourly rotating usage event log sink."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openviking_cli.utils.log_rotation import ProcessSafeTimedRotatingFileHandler

if TYPE_CHECKING:
    from .models import UsageEvent

_EVENT_NAMES = {
    "memory.recalled": "experience.recall.count",
    "memory.injected": "experience.inject.count",
}


def _format_event_time(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _build_tenant_id(
    *,
    resource_id: str,
    account_id: str,
    user_id: str,
    resource_uri: str,
) -> str:
    return (
        f"resource_id:{resource_id};account_id:{account_id};"
        f"user_id:{user_id};resource_uri:{resource_uri}"
    )


def _to_log_record(event: UsageEvent, *, resource_id: str) -> dict[str, Any]:
    record = event.to_dict()
    event_type = str(record.get("event_type") or "")
    try:
        event_name = _EVENT_NAMES[event_type]
    except KeyError as exc:
        raise ValueError(f"unsupported usage event type: {event_type}") from exc

    object_id = str(record.get("event_id") or "").strip()
    if not object_id:
        raise ValueError("event_id is required")

    account_id = str(record.get("account_id") or "")
    user_id = str(record.get("user_id") or "")
    resource_uri = str(record.get("resource_uri") or "")
    return {
        "event_time": _format_event_time(str(record.get("occurred_at") or "")),
        "tenant_id": _build_tenant_id(
            resource_id=resource_id,
            account_id=account_id,
            user_id=user_id,
            resource_uri=resource_uri,
        ),
        "event_name": event_name,
        "object_id": object_id,
        "count": 1,
        "tags": {
            "resource_type": str(record.get("resource_type") or ""),
        },
    }


def _format_record(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


class FileLogUsageSink:
    """Write usage records to a dedicated hourly rotating log file."""

    def __init__(
        self,
        *,
        path: str | os.PathLike[str],
        resource_id_env: str = "OV_RESOURCE_ID",
        rotation_interval_hours: int = 1,
        backup_count: int = 168,
    ) -> None:
        raw_path = str(path).strip()
        if not raw_path:
            raise ValueError("path is required")
        self._resource_id = os.environ.get(resource_id_env, "").strip()
        if not self._resource_id:
            raise ValueError(f"{resource_id_env} is required")
        if rotation_interval_hours <= 0:
            raise ValueError("rotation_interval_hours must be positive")
        if backup_count < 0:
            raise ValueError("backup_count must be non-negative")

        self._path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = ProcessSafeTimedRotatingFileHandler(
            filename=self._path,
            when="H",
            interval=rotation_interval_hours,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
            utc=True,
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger = logging.getLogger(f"openviking.usage_reporter.file_log.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._logger.addHandler(self._handler)
        self._closed = False

    @property
    def path(self) -> Path:
        return self._path

    async def write(self, *, events: list[UsageEvent]) -> None:
        if not events:
            return
        records = [_to_log_record(event, resource_id=self._resource_id) for event in events]
        await asyncio.to_thread(self._write_records, records)

    def _write_records(self, records: list[dict[str, Any]]) -> None:
        if self._closed:
            raise RuntimeError("usage log sink is closed")
        for record in records:
            self._logger.info(_format_record(record))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._logger.removeHandler(self._handler)
        self._handler.close()
