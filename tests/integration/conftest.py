# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for integration tests.

Automatically starts an OpenViking server in a background thread so that
AsyncHTTPClient integration tests can run without a manually started server process.
"""

import shutil
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier

TEST_ROOT = Path(__file__).parent
TEST_TMP_DIR = TEST_ROOT / ".tmp_integration"


@pytest.fixture(scope="session")
def temp_dir():
    """Create temp directory for the whole test session."""
    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)
    TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
    yield TEST_TMP_DIR
    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def server_url(temp_dir):
    """Start a real uvicorn server in a background thread.

    Returns the base URL (e.g. ``http://127.0.0.1:<port>``).
    The server is automatically shut down after the test session.
    """
    import asyncio

    loop = asyncio.new_event_loop()

    svc = OpenVikingService(
        path=str(temp_dir / "data"), user=UserIdentifier.the_default_user("test_user")
    )
    loop.run_until_complete(svc.initialize())

    config = ServerConfig(api_key=None)
    fastapi_app = create_app(config=config, service=svc)

    # Find a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    uvi_config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(uvi_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server ready
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            r = httpx.get(f"{url}/health", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)

    yield url

    server.should_exit = True
    thread.join(timeout=5)
    loop.run_until_complete(svc.close())
    loop.close()
