# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for process-lock interaction with host signal handling."""

import signal

from openviking.utils.process_lock import acquire_data_dir_lock


def test_acquire_does_not_override_sigterm_handler(tmp_path):
    """Uvicorn must retain ownership of SIGTERM for graceful lifespan shutdown."""
    original_handler = signal.getsignal(signal.SIGTERM)

    acquire_data_dir_lock(str(tmp_path))

    assert signal.getsignal(signal.SIGTERM) is original_handler
