# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenViking HTTP Server module."""

from openviking.server.app import create_app
from openviking.server.bootstrap import main as run_server

__all__ = ["create_app", "run_server"]
