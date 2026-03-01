# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenViking console example package."""

from .app import create_console_app
from .config import ConsoleConfig, load_console_config

__all__ = ["create_console_app", "ConsoleConfig", "load_console_config"]
