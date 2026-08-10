# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Shared fixtures for integration tests.

Automatically starts an OpenViking server in a background thread so that
AsyncHTTPClient integration tests can run without a manually started server process.
"""

import copy
import math
import os
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
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

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


def _local_engine_available() -> bool:
    try:
        from openviking.storage.vectordb.engine import ENGINE_VARIANT
    except Exception:
        return False
    return ENGINE_VARIANT != "unavailable"


requires_engine = pytest.mark.skipif(
    not _local_engine_available(),
    reason="local vectordb engine unavailable",
)


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


def gemini_config_dict(
    model: str,
    dim: int,
    query_param: str | None = None,
    doc_param: str | None = None,
) -> dict:
    """Build a minimal embedded-mode config for Gemini-backed integration tests."""
    return {
        "storage": {
            "workspace": str(TEST_TMP_DIR / "gemini"),
            "agfs": {"backend": "local"},
            "vectordb": {"name": "test", "backend": "local", "project": "default"},
        },
        "embedding": {
            "dense": {
                "provider": "gemini",
                "api_key": GOOGLE_API_KEY,
                "model": model,
                "dimension": dim,
                **({"query_param": query_param} if query_param else {}),
                **({"document_param": doc_param} if doc_param else {}),
            }
        },
    }


async def teardown_ov_client() -> None:
    """Reset singleton client/config state used by embedded integration tests."""
    await AsyncOpenViking.reset()
    OpenVikingConfigSingleton.reset_instance()


async def make_ov_client(config_dict: dict, data_path: str) -> AsyncOpenViking:
    """Create an AsyncOpenViking client from an explicit config dict."""
    if not GOOGLE_API_KEY:
        pytest.skip("GOOGLE_API_KEY not set")
    try:
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder  # noqa: F401
    except (ImportError, ModuleNotFoundError, AttributeError):
        pytest.skip("google-genai not installed")

    await teardown_ov_client()

    workspace = Path(data_path)
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    effective_config = copy.deepcopy(config_dict)
    storage = effective_config.setdefault("storage", {})
    storage["workspace"] = str(workspace)
    storage.setdefault("agfs", {"backend": "local"})
    storage.setdefault("vectordb", {"name": "test", "backend": "local", "project": "default"})

    OpenVikingConfigSingleton.initialize(config_dict=effective_config)

    client = AsyncOpenViking(path=str(workspace))
    await client.initialize()
    return client


def sample_markdown(base_dir: Path, slug: str, content: str) -> Path:
    """Write a markdown file for an integration test case."""
    path = base_dir / f"{slug}.md"
    path.write_text(content, encoding="utf-8")
    return path


@pytest_asyncio.fixture(scope="function")
async def gemini_ov_client(tmp_path):
    """Provide a Gemini-backed OpenViking client and its model metadata."""
    model = "gemini-embedding-2-preview"
    dim = 768
    client = await make_ov_client(gemini_config_dict(model, dim), str(tmp_path / "ov_gemini"))
    try:
        yield client, model, dim
    finally:
        await teardown_ov_client()


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
