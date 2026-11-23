# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for OpenAI embedder HTTP connection ownership."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import openai
import pytest
from pydantic import ValidationError

from openviking.concurrency import AsyncSemaphore
from openviking.models.embedder import OpenAIDenseEmbedder
from openviking_cli.utils.config.embedding_config import EmbeddingConfig


class _EmbeddingServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _EmbeddingHandler)
        self.connection_count = 0
        self.document_started = threading.Event()
        self.release_document = threading.Event()

    def get_request(self):
        request, address = super().get_request()
        self.connection_count += 1
        return request, address


class _EmbeddingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(content_length))
        if request["input"] == "blocked-document":
            self.server.document_started.set()
            self.server.release_document.wait(timeout=10)
        payload = json.dumps(
            {
                "object": "list",
                "data": [
                    {
                        "object": "embedding",
                        "index": 0,
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
                "model": "test-embedding",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _embedding_server() -> Iterator[_EmbeddingServer]:
    server = _EmbeddingServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release_document.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _pool(client):
    return client._client._transport._pool


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "azure"])
async def test_openai_embedder_bounds_idle_connections(provider: str) -> None:
    provider_kwargs = {}
    if provider == "azure":
        provider_kwargs = {
            "api_base": "https://test-resource.openai.azure.com",
            "api_version": "2025-01-01-preview",
        }

    embedder = OpenAIDenseEmbedder(
        model_name="test-embedding",
        api_key="test-key",
        dimension=3,
        config={"max_concurrent": 3},
        provider=provider,
        **provider_kwargs,
    )
    async_client = embedder._get_async_client()

    try:
        for client in (embedder.client, async_client):
            pool = _pool(client)
            assert pool._max_keepalive_connections == 3
            assert pool._keepalive_expiry == 5.0
            assert client._client.timeout == openai.DEFAULT_TIMEOUT
    finally:
        embedder.client.close()
        await async_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("account_bound", [False, True])
async def test_openai_embedder_reuses_connections_without_blocking_queries(account_bound) -> None:
    with _embedding_server() as server:
        embedder = OpenAIDenseEmbedder(
            model_name="test-embedding",
            api_key="test-key",
            api_base=f"http://127.0.0.1:{server.server_port}/v1",
            dimension=3,
            encoding_format="float",
            config={"max_concurrent": 1},
        )
        if account_bound:
            embedder._account_semaphore = AsyncSemaphore(1)
        async_client = embedder._get_async_client()
        tasks = []

        try:
            first = await embedder.embed_async("first")
            second = await embedder.embed_async("second")

            assert first.dense_vector == pytest.approx([0.1, 0.2, 0.3])
            assert second.dense_vector == pytest.approx([0.1, 0.2, 0.3])
            assert server.connection_count == 1

            document = asyncio.create_task(embedder.embed_async("blocked-document"))
            tasks.append(document)
            assert await asyncio.to_thread(server.document_started.wait, 5)
            waiting_document = asyncio.create_task(embedder.embed_async("next-document"))
            tasks.append(waiting_document)

            # Queries must reach the provider while the only document slot and
            # its HTTP connection remain occupied, without freeing that slot.
            query_results = await asyncio.wait_for(
                asyncio.gather(
                    embedder.embed_async("first-query", is_query=True),
                    embedder.embed_async("second-query", is_query=True),
                ),
                timeout=5,
            )
            assert all(result.dense_vector == [0.1, 0.2, 0.3] for result in query_results)
            assert not document.done()
            assert not waiting_document.done()

            server.release_document.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

            embedder.close()
            await asyncio.sleep(0)

            assert embedder.client.is_closed()
            assert async_client.is_closed()
        finally:
            server.release_document.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not embedder.client.is_closed():
                embedder.client.close()
            if not async_client.is_closed():
                await async_client.close()


@pytest.mark.parametrize("max_concurrent", [0, -1])
def test_embedding_config_rejects_non_positive_concurrency(max_concurrent: int) -> None:
    with pytest.raises(ValidationError, match="max_concurrent"):
        EmbeddingConfig(max_concurrent=max_concurrent)


@pytest.mark.parametrize("max_concurrent", [0, -1])
def test_direct_embedder_normalizes_non_positive_pool_limit(max_concurrent: int) -> None:
    embedder = OpenAIDenseEmbedder(
        model_name="test-embedding",
        api_key="test-key",
        dimension=3,
        config={"max_concurrent": max_concurrent},
    )

    try:
        pool = _pool(embedder.client)
        assert pool._max_keepalive_connections == 1
    finally:
        embedder.close()
