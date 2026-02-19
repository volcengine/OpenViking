# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def agfs_test_root():
    """Root directory for AGFS tests."""
    path = Path("/tmp/openviking_agfs_test")
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
