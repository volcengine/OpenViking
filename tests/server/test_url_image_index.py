# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the URL-image vector index endpoint (`POST /api/v1/resources/url_image_index`).

This endpoint is the "bring your own image hosting" entrypoint: the caller
gives an https URL and an OpenViking target URI, and OpenViking embeds the
image (via the configured multimodal embedder) and inserts the resulting
vector directly into vectordb — **without** writing the image bytes to agfs.

The tests here pin:
  - happy path (multimodal embedder, image-only and image+summary)
  - rejection when the configured embedder is text-only
  - rejection on dim mismatch
  - rejection on a non-http(s) image URL
  - rejection on a malformed target URI
  - tenant isolation: ``X-OpenViking-Account`` confines reads/writes
  - no bytes written to agfs as a side effect
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from openviking.config import EmbeddingConfig, ServerConfig, VLMConfig
from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.app import create_app
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier

# Re-using server conftest's reset_lock_manager helper via a local import: not
# strictly required, but mirrors how other server tests stitch the fixtures.


# ---------------------------------------------------------------------------
# Local fake embedders. The default FakeEmbedder in tests/server/conftest.py
# inherits supports_multimodal=False from DenseEmbedderBase — perfect for the
# negative case. For positive cases we install a multimodal-capable variant.
# ---------------------------------------------------------------------------


class _FakeMultimodalEmbedder(DenseEmbedderBase):
    """Records every call so tests can assert which content was embedded."""

    def __init__(self, dimension: int = 2048):
        super().__init__(model_name="test-fake-multimodal")
        self._dimension = dimension
        self.calls: list[Any] = []

    @property
    def supports_multimodal(self) -> bool:  # noqa: D401 (test stub)
        return True

    def embed(self, content, is_query: bool = False) -> EmbedResult:
        self.calls.append(content)
        # Use a deterministic but distinguishable vector so dim-mismatch tests
        # can be added later without changing the happy-path assertions.
        return EmbedResult(dense_vector=[0.5] * self._dimension)

    def embed_batch(self, texts, is_query: bool = False):
        return [self.embed(t, is_query=is_query) for t in texts]

    def get_dimension(self) -> int:
        return self._dimension


class _DimMismatchEmbedder(_FakeMultimodalEmbedder):
    """Returns vectors of the WRONG dimension so the endpoint's dim check fires."""

    def embed(self, content, is_query: bool = False) -> EmbedResult:
        self.calls.append(content)
        # Configured EmbeddingConfig.dimension is 2048 (set in test_data_dir
        # fixture); deliberately return 1536-dim vectors here.
        return EmbedResult(dense_vector=[0.5] * 1536)


# ---------------------------------------------------------------------------
# Fixtures local to this file
# ---------------------------------------------------------------------------


def _install_fake_vlm_local(monkeypatch):
    async def _fake_completion(self, prompt, thinking=False):
        return "Fake summary."

    async def _fake_vision_completion(self, prompt, images, thinking=False):
        return "Fake vision description."

    monkeypatch.setattr(VLMConfig, "is_available", lambda self: True)
    monkeypatch.setattr(VLMConfig, "get_completion_async", _fake_completion)
    monkeypatch.setattr(VLMConfig, "get_vision_completion_async", _fake_vision_completion)


def _install_multimodal_embedder(monkeypatch, embedder_cls=_FakeMultimodalEmbedder):
    holder: dict[str, Any] = {}

    def _factory(self):
        emb = embedder_cls(self.dimension)
        holder["embedder"] = emb
        return emb

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", _factory)
    return holder


@pytest_asyncio.fixture
async def multimodal_service(
    tmp_path: Path, monkeypatch
) -> AsyncGenerator[tuple[OpenVikingService, dict[str, Any]], None]:
    """A bare service with a multimodal-capable fake embedder installed."""
    from openviking.storage.transaction.lock_manager import reset_lock_manager

    reset_lock_manager()
    _install_fake_vlm_local(monkeypatch)
    embedder_holder = _install_multimodal_embedder(monkeypatch)

    svc = OpenVikingService(
        path=str(tmp_path / "data"),
        user=UserIdentifier.the_default_user("test_user"),
    )
    await svc.initialize()
    yield svc, embedder_holder
    await svc.close()
    reset_lock_manager()


@pytest_asyncio.fixture
async def text_only_service(
    tmp_path: Path, monkeypatch
) -> AsyncGenerator[OpenVikingService, None]:
    """A service whose embedder reports supports_multimodal=False."""
    from openviking.storage.transaction.lock_manager import reset_lock_manager

    reset_lock_manager()
    _install_fake_vlm_local(monkeypatch)

    class _FakeTextOnly(DenseEmbedderBase):
        def __init__(self, dim=2048):
            super().__init__(model_name="text-only")
            self._dim = dim

        def embed(self, text, is_query: bool = False):
            return EmbedResult(dense_vector=[0.0] * self._dim)

        def embed_batch(self, texts, is_query: bool = False):
            return [self.embed(t) for t in texts]

        def get_dimension(self):
            return self._dim

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: _FakeTextOnly(self.dimension))

    svc = OpenVikingService(
        path=str(tmp_path / "data"),
        user=UserIdentifier.the_default_user("test_user"),
    )
    await svc.initialize()
    yield svc
    await svc.close()
    reset_lock_manager()


@pytest_asyncio.fixture
async def dim_mismatch_service(
    tmp_path: Path, monkeypatch
) -> AsyncGenerator[OpenVikingService, None]:
    from openviking.storage.transaction.lock_manager import reset_lock_manager

    reset_lock_manager()
    _install_fake_vlm_local(monkeypatch)
    _install_multimodal_embedder(monkeypatch, embedder_cls=_DimMismatchEmbedder)

    svc = OpenVikingService(
        path=str(tmp_path / "data"),
        user=UserIdentifier.the_default_user("test_user"),
    )
    await svc.initialize()
    yield svc
    await svc.close()
    reset_lock_manager()


def _client_for(svc: OpenVikingService) -> httpx.AsyncClient:
    """Build an in-process httpx client bound to the service via an app."""
    from openviking.server.auth.plugins import DevAuthPlugin
    from openviking.server.auth.registry import get_registry
    from openviking.server.dependencies import set_service

    config = ServerConfig()
    app = create_app(config=config, service=svc)
    set_service(svc)
    registry = get_registry()
    if registry.get("dev") is None:
        registry.register(DevAuthPlugin)
    plugin_cls = registry.get("dev")
    if plugin_cls is not None:
        app.state.auth_plugin = plugin_cls()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_image_index_inserts_vector_without_agfs_write(multimodal_service):
    """Happy path: multimodal embedder configured, image-only request succeeds
    and the vector is in vectordb. No file was created in agfs under the
    target URI."""
    svc, holder = multimodal_service
    target_uri = "viking://resources/products/SKU-A/images/0.embed"

    async with _client_for(svc) as client:
        resp = await client.post(
            "/api/v1/resources/url_image_index",
            json={
                "target_uri": target_uri,
                "image_url": "https://example.com/photo.jpg",
                "metadata": {"sku": "SKU-A", "image_idx": 0},
            },
            headers={"X-OpenViking-Account": "tenant-a"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "ok"
    assert body["result"]["uri"].endswith("/SKU-A/images/0.embed")
    assert body["result"]["id"]  # md5 hex string

    # The embedder was called with the URL in image_url passthrough format.
    embedder = holder["embedder"]
    assert len(embedder.calls) == 1
    payload = embedder.calls[0]
    assert isinstance(payload, list)
    assert any(
        p.get("type") == "image_url"
        and p.get("image_url", {}).get("url") == "https://example.com/photo.jpg"
        for p in payload
    )
    # No text part should be present when summary was not supplied.
    assert not any(p.get("type") == "text" for p in payload)

    # The vectordb should now hold the vector under the canonical URI scoped
    # to tenant-a. Round-trip via fetch_by_uri to confirm.
    ctx = RequestContext(user=UserIdentifier("tenant-a", "default"), role=Role.ROOT)
    record = await svc.vikingdb_manager.fetch_by_uri(body["result"]["uri"], ctx=ctx)
    assert record is not None
    assert record.get("account_id") == "tenant-a"
    assert record.get("image_url") == "https://example.com/photo.jpg"
    assert record.get("sku") == "SKU-A"
    assert record.get("image_idx") == 0
    # 2048-dim vector from _FakeMultimodalEmbedder.
    assert len(record.get("vector", [])) == 2048

    # And no file was written under target_uri in agfs.
    viking_fs = svc.viking_fs
    assert not await viking_fs.exists(body["result"]["uri"], ctx=ctx)


@pytest.mark.asyncio
async def test_url_image_index_includes_text_part_when_summary_set(multimodal_service):
    """summary='...' adds a text part to the multimodal embedding input."""
    svc, holder = multimodal_service
    async with _client_for(svc) as client:
        resp = await client.post(
            "/api/v1/resources/url_image_index",
            json={
                "target_uri": "viking://resources/products/SKU-B/images/0.embed",
                "image_url": "https://example.com/b.jpg",
                "summary": "Louis Vuitton Trocadero Wearable Wallet",
            },
            headers={"X-OpenViking-Account": "tenant-a"},
        )

    assert resp.status_code == 200, resp.text
    payload = holder["embedder"].calls[0]
    text_parts = [p for p in payload if p.get("type") == "text"]
    assert len(text_parts) == 1
    assert text_parts[0]["text"] == "Louis Vuitton Trocadero Wearable Wallet"


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_only_embedder_is_rejected(text_only_service):
    """A text-only embedder must produce a 400, not silently embed the URL string."""
    async with _client_for(text_only_service) as client:
        resp = await client.post(
            "/api/v1/resources/url_image_index",
            json={
                "target_uri": "viking://resources/x/y.embed",
                "image_url": "https://example.com/x.jpg",
            },
            headers={"X-OpenViking-Account": "tenant-a"},
        )

    assert resp.status_code == 400
    assert "multimodal" in resp.json().get("error", {}).get("message", "").lower()


@pytest.mark.asyncio
async def test_dim_mismatch_is_rejected(dim_mismatch_service):
    """Embedder returning the wrong dim → 400 (catch dim drift before write)."""
    async with _client_for(dim_mismatch_service) as client:
        resp = await client.post(
            "/api/v1/resources/url_image_index",
            json={
                "target_uri": "viking://resources/x/y.embed",
                "image_url": "https://example.com/x.jpg",
            },
            headers={"X-OpenViking-Account": "tenant-a"},
        )

    assert resp.status_code == 400
    msg = resp.json().get("error", {}).get("message", "")
    assert "dim" in msg.lower()


@pytest.mark.asyncio
async def test_non_http_image_url_is_rejected(multimodal_service):
    svc, _ = multimodal_service
    async with _client_for(svc) as client:
        resp = await client.post(
            "/api/v1/resources/url_image_index",
            json={
                "target_uri": "viking://resources/x/y.embed",
                "image_url": "data:image/jpeg;base64,AAAA",
            },
            headers={"X-OpenViking-Account": "tenant-a"},
        )
    assert resp.status_code == 400
    assert "http" in resp.json().get("error", {}).get("message", "").lower()


@pytest.mark.asyncio
async def test_malformed_target_uri_is_rejected(multimodal_service):
    svc, _ = multimodal_service
    async with _client_for(svc) as client:
        resp = await client.post(
            "/api/v1/resources/url_image_index",
            json={
                "target_uri": "s3://not-a-viking-uri/x.embed",
                "image_url": "https://example.com/x.jpg",
            },
            headers={"X-OpenViking-Account": "tenant-a"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reserved_metadata_keys_are_stripped(multimodal_service):
    """Caller metadata trying to override server-owned fields (id/vector/...)
    must be ignored — server fields win."""
    svc, _ = multimodal_service
    async with _client_for(svc) as client:
        resp = await client.post(
            "/api/v1/resources/url_image_index",
            json={
                "target_uri": "viking://resources/x/y.embed",
                "image_url": "https://example.com/x.jpg",
                "metadata": {
                    "id": "attacker-controlled-id",
                    "account_id": "other-tenant",
                    "vector": [9.9] * 2048,
                    "sku": "real-sku",
                },
            },
            headers={"X-OpenViking-Account": "tenant-a"},
        )
    assert resp.status_code == 200, resp.text
    server_id = resp.json()["result"]["id"]
    assert server_id != "attacker-controlled-id"

    ctx = RequestContext(user=UserIdentifier("tenant-a", "default"), role=Role.ROOT)
    record = await svc.vikingdb_manager.fetch_by_uri(resp.json()["result"]["uri"], ctx=ctx)
    assert record["account_id"] == "tenant-a"  # not other-tenant
    assert record["sku"] == "real-sku"  # non-reserved keys pass through
    # vector must be the embedder's output (all 0.5), not the attacker's (9.9).
    assert record["vector"][0] == 0.5
