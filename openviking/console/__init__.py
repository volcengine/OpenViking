# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""OpenViking Console (standalone web dashboard).

This package contains the FastAPI app and static frontend assets.
"""

from .app import create_console_app  # noqa: F401
from .config import ConsoleConfig, load_console_config  # noqa: F401
