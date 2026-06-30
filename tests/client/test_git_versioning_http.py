# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end parity tests for client.snapshot.* over HTTP.

These exercise the AsyncHTTPClient.snapshot namespace surface that mirrors
the LocalClient.snapshot surface covered by tests/client/test_git_versioning.py,
routed through AsyncHTTPClient -> real FastAPI server (via httpx
ASGITransport) -> real OpenVikingService -> real VikingFS.

The full stack is genuine: real httpx response parsing, real envelope
handling, real X-Snapshot-* header round-tripping. No mocks at the
client.snapshot or AsyncHTTPClient layer.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

ragfs_python = pytest.importorskip("ragfs_python")

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.storage.transaction import reset_lock_manager
from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.vlm_config import VLMConfig


pytestmark = pytest.mark.asyncio

OID_RE = re.compile(r"^[0-9a-f]{40}$")

PROJECT_ROOT = Path(__file__).parent.parent.parent
HTTP_TEST_TMP_DIR = PROJECT_ROOT / "test_data" / "tmp_client_git_http"


def _install_fake_embedder(monkeypatch):
    dimension = 1024

    class FakeEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="test-fake-embedder")

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            return EmbedResult(dense_vector=[0.1] * dimension)

        def embed_batch(self, texts, is_query: bool = False):
            return [self.embed(t, is_query=is_query) for t in texts]

        def get_dimension(self) -> int:
            return dimension

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder())
    return FakeEmbedder


def _install_fake_vlm(monkeypatch):
    async def _fake_get_completion(self, prompt, thinking=False):
        return "fake summary"

    async def _fake_get_vision_completion(self, prompt, images, thinking=False):
        return "fake vision"

    monkeypatch.setattr(VLMConfig, "is_available", lambda self: True)
    monkeypatch.setattr(VLMConfig, "get_completion_async", _fake_get_completion)
    monkeypatch.setattr(VLMConfig, "get_vision_completion_async", _fake_get_vision_completion)


@pytest.fixture(scope="function")
def http_temp_dir():
    unique = HTTP_TEST_TMP_DIR / uuid.uuid4().hex[:8]
    unique.mkdir(parents=True, exist_ok=True)
    yield unique
    shutil.rmtree(unique, ignore_errors=True)


@pytest_asyncio.fixture(scope="function")
async def http_service(http_temp_dir: Path, monkeypatch):
    """Stand up a real OpenVikingService backed by a temp data dir."""
    reset_lock_manager()
    fake_embedder_cls = _install_fake_embedder(monkeypatch)
    _install_fake_vlm(monkeypatch)

    svc = OpenVikingService(
        path=str(http_temp_dir / "data"),
        user=UserIdentifier.the_default_user("git_http_test_user"),
    )
    await svc.initialize()
    svc.viking_fs.query_embedder = fake_embedder_cls()

    test_ctx = RequestContext(
        user=UserIdentifier("git_http_test_account", "git_http_test_user"),
        role=Role.ADMIN,
    )
    await svc.initialize_account_directories(test_ctx)
    await svc.initialize_user_directories(test_ctx)
    try:
        yield svc
    finally:
        await svc.close()
        reset_lock_manager()


@pytest_asyncio.fixture(scope="function")
async def http_app(http_service: OpenVikingService):
    """FastAPI app with the test service wired in (no auth)."""
    from openviking.server.auth.plugins import DevAuthPlugin
    from openviking.server.auth.registry import get_registry
    from openviking.server.dependencies import set_service

    config = ServerConfig()
    app = create_app(config=config, service=http_service)
    set_service(http_service)
    # ASGITransport doesn't trigger lifespan, so wire up the auth plugin manually.
    registry = get_registry()
    if registry.get("dev") is None:
        registry.register(DevAuthPlugin)
    app.state.auth_plugin = registry.get("dev")()
    return app


@pytest_asyncio.fixture(scope="function")
async def http_git_client(http_app) -> AsyncGenerator[AsyncHTTPClient, None]:
    """Real AsyncHTTPClient whose underlying httpx talks to the ASGI app.

    The returned client exposes the production `.snapshot` namespace; the only
    swap is the transport — every other layer is the real stack.
    """
    client = AsyncHTTPClient(
        url="http://testserver",
        api_key="test-key",
        account="git_http_test_account",
        user="git_http_test_user",
    )
    transport = httpx.ASGITransport(app=http_app)
    headers = {
        "X-API-Key": "test-key",
        "X-OpenViking-Account": "git_http_test_account",
        "X-OpenViking-User": "git_http_test_user",
    }
    client._http = httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=headers,
        timeout=30.0,
    )
    try:
        yield client
    finally:
        await client._http.aclose()


async def _write_blob(service: OpenVikingService, uri: str, body: bytes) -> None:
    ctx = RequestContext(
        user=UserIdentifier("git_http_test_account", "git_http_test_user"),
        role=Role.ROOT,
    )
    await service.viking_fs.write_file(uri, body, ctx=ctx)


async def test_http_commit_and_log_roundtrip(http_git_client, http_service):
    client = http_git_client

    await _write_blob(http_service, "viking://resources/http_a.md", b"hello-http")

    commit = await client.snapshot.commit(message="http parity")
    assert commit["result"] in ("created", "noop")
    assert isinstance(commit["commit_oid"], str)
    assert OID_RE.match(commit["commit_oid"])

    log = await client.snapshot.log(limit=5)
    assert isinstance(log, list) and len(log) >= 1
    assert "oid" in log[0] and "message" in log[0]


async def test_http_show_blob_byte_exact_roundtrip(http_git_client, http_service):
    client = http_git_client
    blob_uri = "viking://resources/http_show_blob.txt"
    expected = b"byte exact \x00\x01\x02 payload\n"

    await _write_blob(http_service, blob_uri, expected)
    commit = await client.snapshot.commit(message="with blob")
    assert OID_RE.match(commit["commit_oid"])

    result = await client.snapshot.show(commit["commit_oid"], path=blob_uri)
    assert isinstance(result, dict)
    assert result["bytes"] == expected
    assert result["size"] == len(expected)
    assert OID_RE.match(result["oid"])


async def test_http_show_metadata_without_path(http_git_client, http_service):
    client = http_git_client

    await _write_blob(http_service, "viking://resources/http_meta.md", b"metadata")
    commit = await client.snapshot.commit(message="meta commit")

    meta = await client.snapshot.show(commit["commit_oid"])
    assert meta["oid"] == commit["commit_oid"]
    assert meta["message"].startswith("meta commit")
    assert meta["parents"] == []


async def test_http_restore_dry_run_does_not_mutate(http_git_client, http_service):
    client = http_git_client

    await _write_blob(http_service, "viking://resources/proj/a.md", b"v1")
    v1 = await client.snapshot.commit(message="v1")
    assert OID_RE.match(v1["commit_oid"])

    await _write_blob(http_service, "viking://resources/proj/a.md", b"v2")
    v2 = await client.snapshot.commit(message="v2")
    assert v2["commit_oid"] != v1["commit_oid"]

    log_before = await client.snapshot.log(limit=10)

    dry = await client.snapshot.restore(
        project_dir="viking://resources/proj",
        source_commit=v1["commit_oid"],
        dry_run=True,
    )

    assert "diff" in dry or dry.get("result") == "noop"

    blob_after = await client.snapshot.show(v2["commit_oid"], path="viking://resources/proj/a.md")
    assert blob_after["bytes"] == b"v2"

    log_after = await client.snapshot.log(limit=10)
    assert len(log_after) == len(log_before)
