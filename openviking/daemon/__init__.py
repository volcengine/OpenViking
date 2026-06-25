# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
OpenViking Active Daemon package.
Monitors AI tool logs and automatically extracts knowledge into viking:// storage.
"""
from openviking.daemon.service import DaemonService

__all__ = ["DaemonService"]
