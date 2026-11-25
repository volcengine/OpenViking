# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_openviking_top_level_exports_http_clients():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import sys
from pathlib import Path

repo_root = Path.cwd()
sys.path.insert(0, str(repo_root))
from openviking import AsyncHTTPClient, SyncHTTPClient
from openviking.client import AsyncHTTPClient as module_async, SyncHTTPClient as module_sync
from openviking_cli.client.http import AsyncHTTPClient as legacy_async
from openviking_cli.client.sync_http import SyncHTTPClient as legacy_sync

assert AsyncHTTPClient is module_async is legacy_async
assert SyncHTTPClient is module_sync is legacy_sync
assert 'openviking.service' not in sys.modules
sdk_path = Path(sys.modules['openviking_sdk'].__file__).resolve()
assert repo_root / 'sdk' / 'python' in sdk_path.parents
""",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
