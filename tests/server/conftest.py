# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for OpenViking server tests."""

import shutil
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn

from openviking import AsyncOpenViking
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TEST_ROOT = Path(__file__).parent
TEST_TMP_DIR = TEST_ROOT / ".tmp_server"

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_MD_CONTENT = """\
# Sample Document

## Introduction
This is a sample markdown document for server testing.

## Features
- Feature 1: Resource management
- Feature 2: Semantic search
"""


# ---------------------------------------------------------------------------
# Core fixtures: service + app + async client (HTTP API tests, in-process)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def temp_dir():
    """Create temp directory, auto-cleanup."""
    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)
    TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
    yield TEST_TMP_DIR
    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)


@pytest.fixture(scope="function")
def sample_markdown_file(temp_dir: Path) -> Path:
    """Create a sample markdown file for resource tests."""
    f = temp_dir / "sample.md"
    f.write_text(SAMPLE_MD_CONTENT)
    return f


@pytest_asyncio.fixture(scope="function")
async def service(temp_dir: Path):
    """Create and initialize an OpenVikingService in embedded mode."""
    svc = OpenVikingService(
        path=str(temp_dir / "data"), user=UserIdentifier.the_default_user("test_user")
    )
    await svc.initialize()
    yield svc
    await svc.close()


@pytest_asyncio.fixture(scope="function")
async def app(service: OpenVikingService):
    """Create FastAPI app with pre-initialized service (no auth)."""
    from openviking.server.dependencies import set_service

    config = ServerConfig(api_key=None)
    fastapi_app = create_app(config=config, service=service)
    # ASGITransport doesn't trigger lifespan, so wire up the service manually
    set_service(service)
    return fastapi_app


@pytest_asyncio.fixture(scope="function")
async def client(app):
    """httpx AsyncClient bound to the ASGI app (no real network)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(scope="function")
async def client_with_resource(client, service, sample_markdown_file):
    """Client + a resource already added and processed."""
    result = await service.resources.add_resource(
        path=str(sample_markdown_file),
        reason="test resource",
        wait=True,
    )
    yield client, result.get("root_uri", "")


# ---------------------------------------------------------------------------
# SDK fixtures: real uvicorn server + AsyncHTTPClient (end-to-end tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def running_server(temp_dir: Path):
    """Start a real uvicorn server in a background thread."""
    await AsyncOpenViking.reset()

    svc = OpenVikingService(
        path=str(temp_dir / "sdk_data"), user=UserIdentifier.the_default_user("sdk_test_user")
    )
    await svc.initialize()

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
    for _ in range(50):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)

    yield port, svc

    server.should_exit = True
    thread.join(timeout=5)
    await svc.close()
    await AsyncOpenViking.reset()
