# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Daemon status API endpoints."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/daemon", tags=["daemon"])

# Module-level reference to the running DaemonService
_daemon_service = None


def set_daemon_service(service):
    """Called by app.py lifespan to register the daemon service."""
    global _daemon_service
    _daemon_service = service


@router.get("/status")
async def get_daemon_status() -> Dict[str, Any]:
    """
    Get multi-watcher daemon status.

    Returns:
        {
            "enabled": bool,
            "running": bool,
            "watchers": [...],
            "available_tools": [...],
            "db_path": str | null
        }
    """
    from openviking.daemon.watchers.registry import list_available_watchers

    if _daemon_service is None:
        # Daemon not running — return config-based fallback
        from openviking.server.config import DaemonConfig

        config = DaemonConfig.from_env()
        return {
            "enabled": config.enabled,
            "running": False,
            "watchers": [],
            "available_tools": list_available_watchers(),
            "db_path": config.db_path,
        }

    svc = _daemon_service
    watcher_statuses: List[Dict[str, Any]] = []
    for i, watcher in enumerate(svc.watchers):
        wc = svc._watcher_configs[i] if i < len(svc._watcher_configs) else None
        cursor_count = 0
        try:
            if svc.cursor_manager:
                cursor_count = len(svc.cursor_manager.get_all_cursors())
        except Exception:
            pass

        watcher_statuses.append({
            "tool_name": watcher.tool_name,
            "watch_dir": wc.watch_dir if wc else None,
            "file_pattern": wc.file_pattern if wc else None,
            "enabled": True,
            "running": True,
            "cursor_count": cursor_count,
            "batch_trigger_lines": wc.batch_trigger_lines if wc else None,
            "batch_trigger_seconds": wc.batch_trigger_seconds if wc else None,
        })

    return {
        "enabled": True,
        "running": svc.is_running,
        "watchers": watcher_statuses,
        "available_tools": list_available_watchers(),
        "db_path": svc.db_path,
    }
