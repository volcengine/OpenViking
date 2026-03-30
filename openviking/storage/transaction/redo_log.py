# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lightweight redo log for crash recovery of session_memory operations."""

import json
from typing import Any, Dict, List

from openviking.pyagfs import AGFSClient
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_REDO_ROOT = "/local/_system/redo"


class RedoLog:
    """Lightweight pending-task marker.

    Write a marker before the operation starts; delete it after success.
    On startup, scan for leftover markers and redo.
    """

    def __init__(self, agfs: AGFSClient):
        self._agfs = agfs

    def _task_path(self, task_id: str) -> str:
        return f"{_REDO_ROOT}/{task_id}/redo.json"

    def _ensure_dirs(self, dir_path: str) -> None:
        parts = dir_path.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                self._agfs.mkdir(current)
            except Exception:
                pass

    def write_pending(self, task_id: str, info: Dict[str, Any]) -> None:
        """Write a redo marker before the operation starts."""
        dir_path = f"{_REDO_ROOT}/{task_id}"
        self._ensure_dirs(dir_path)
        data = json.dumps(info, default=str).encode("utf-8")
        self._agfs.write(self._task_path(task_id), data)

    def mark_done(self, task_id: str) -> None:
        """Delete the redo marker after a successful operation."""
        try:
            self._agfs.rm(f"{_REDO_ROOT}/{task_id}", recursive=True)
        except Exception as e:
            logger.warning(f"Failed to clean redo marker {task_id}: {e}")

    def list_pending(self) -> List[str]:
        """Return all pending task IDs (directories under _REDO_ROOT)."""
        try:
            entries = self._agfs.ls(_REDO_ROOT)
            if not isinstance(entries, list):
                return []
            return [
                e["name"]
                for e in entries
                if isinstance(e, dict) and e.get("isDir") and e.get("name") not in (".", "..")
            ]
        except Exception:
            return []

    def read(self, task_id: str) -> Dict[str, Any]:
        """Read the info dict of a pending task."""
        try:
            content = self._agfs.cat(self._task_path(task_id))
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            return json.loads(content)
        except Exception as e:
            logger.warning(f"Failed to read redo info for {task_id}: {e}")
            return {}
