# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for OpenAI-compatible rerank client HTTP connection ownership."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest

from openviking.models.rerank import OpenAIRerankClient


class _RerankServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _RerankHandler)
        self.connection_count = 0

    def get_request(self):
        request, address = super().get_request()
        self.connection_count += 1
        return request, address


class _RerankHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(content_length) or b"{}")
        documents = body.get("documents", [])
        payload = json.dumps(
            {
                "results": [
                    {"index": index, "relevance_score": 1.0 - index * 0.1}
                    for index in range(len(documents))
                ]
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
def _rerank_server() -> Iterator[_RerankServer]:
    server = _RerankServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_openai_rerank_reuses_one_connection_across_batches() -> None:
    """One client reranks many times, so it must not dial the endpoint every time."""
    with _rerank_server() as server:
        client = OpenAIRerankClient(
            api_key="test-key",
            api_base=f"http://127.0.0.1:{server.server_port}/rerank",
            model_name="bge-reranker-v2-m3",
        )

        first = client.rerank_batch("query", ["doc-a", "doc-b"])
        second = client.rerank_batch("query", ["doc-b", "doc-a"])

        assert first == pytest.approx([1.0, 0.9])
        assert second == pytest.approx([1.0, 0.9])
        assert server.connection_count == 1
