# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for integration tests.

Automatically starts an OpenViking server in a background thread so that
AsyncHTTPClient integration tests can run without a manually started server process.
"""

import math
import os
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

PROJECT_ROOT = Path(__file__).parent.parent.parent
TEST_TMP_DIR = PROJECT_ROOT / "test_data" / "tmp_integration"

# ── Gemini integration test helpers ──────────────────────────────────────────
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
requires_api_key = pytest.mark.skipif(not GOOGLE_API_KEY, reason="GOOGLE_API_KEY not set")

# ── Vault integration test helpers ──────────────────────────────────────────
VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "")
requires_vault = pytest.mark.skipif(not VAULT_TOKEN, reason="VAULT_TOKEN not set")

# ── Volcengine KMS integration test helpers ──────────────────────────────────────────
VOLCENGINE_ACCESS_KEY = os.environ.get("VOLCENGINE_ACCESS_KEY", "")
VOLCENGINE_SECRET_KEY = os.environ.get("VOLCENGINE_SECRET_KEY", "")
VOLCENGINE_KMS_KEY_ID = os.environ.get("VOLCENGINE_KMS_KEY_ID", "")
VOLCENGINE_KMS_REGION = os.environ.get("VOLCENGINE_KMS_REGION", "cn-beijing")
requires_volcengine_kms = pytest.mark.skipif(
    not (VOLCENGINE_ACCESS_KEY and VOLCENGINE_SECRET_KEY and VOLCENGINE_KMS_KEY_ID),
    reason="VOLCENGINE_ACCESS_KEY, VOLCENGINE_SECRET_KEY, or VOLCENGINE_KMS_KEY_ID not set",
)

# (model_name, default_dimension, token_limit)
GEMINI_MODELS = [
    ("gemini-embedding-2-preview", 3072, 8192),
]


def l2_norm(vec: list[float]) -> float:
    """Compute L2 norm of a vector."""
    return math.sqrt(sum(v * v for v in vec))


@pytest.fixture(scope="session")
def gemini_embedder():
    """Session-scoped GeminiDenseEmbedder for integration tests."""
    if not GOOGLE_API_KEY:
        pytest.skip("GOOGLE_API_KEY not set")
    try:
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
    except (ImportError, ModuleNotFoundError, AttributeError):
        pytest.skip("google-genai not installed")
    return GeminiDenseEmbedder("gemini-embedding-2-preview", api_key=GOOGLE_API_KEY, dimension=768)


@pytest.fixture(scope="session")
def temp_dir():
    """Create temp directory for the whole test session."""
    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)
    TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
    yield TEST_TMP_DIR


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

    config = ServerConfig()
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
