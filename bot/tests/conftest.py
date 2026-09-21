# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Shared fixtures for the VikingBot suite."""

from pathlib import Path

import pytest


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Give each test its own pytest-managed directory."""
    return tmp_path
