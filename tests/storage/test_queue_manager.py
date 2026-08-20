# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for QueueManager concurrency selection."""

import os
import subprocess
import sys

from openviking.storage.queuefs.queue_manager import QueueManager


def test_queuefs_package_imports_in_a_clean_process(tmp_path) -> None:
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(tmp_path / "missing-ov.conf")

    subprocess.run(
        [sys.executable, "-c", "from openviking.storage.queuefs import QueueManager"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_queue_concurrency_uses_separate_configured_values() -> None:
    manager = QueueManager(
        agfs=object(),
        max_concurrent_external_parse=9,
        max_concurrent_add_resource=7,
        max_concurrent_session_commit=5,
    )

    assert manager._max_concurrent_for_queue(manager.EXTERNAL_PARSE) == 9
    assert manager._max_concurrent_for_queue(manager.ADD_RESOURCE) == 7
    assert manager._max_concurrent_for_queue(manager.SESSION_COMMIT) == 5
