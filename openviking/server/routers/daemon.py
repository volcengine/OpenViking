# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Daemon status API endpoints."""
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/daemon", tags=["daemon"])


@router.get("/status")
async def get_daemon_status() -> Dict[str, Any]:
    """
    Get the current status of the Active Daemon.

    Returns:
        {
            "enabled": bool,
            "running": bool,
            "watch_dir": str | null,
            "db_path": str | null,
            "batch_trigger_lines": int,
            "batch_trigger_seconds": int,
            "cursor_count": int,
            "last_flush_time": str | null
        }
    """
    # This is a placeholder — actual implementation needs access to DaemonService instance
    # For now, return static config info
    from openviking.server.config import DaemonConfig
    
    daemon_config = DaemonConfig.from_env()
    
    return {
        "enabled": daemon_config.enabled,
        "running": False,  # Would need to track actual state
        "watch_dir": daemon_config.watch_dir,
        "db_path": daemon_config.db_path,
        "batch_trigger_lines": daemon_config.batch_trigger_lines,
        "batch_trigger_seconds": daemon_config.batch_trigger_seconds,
        "cursor_count": 0,
        "last_flush_time": None,
    }
