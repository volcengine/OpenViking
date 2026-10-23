# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for GeminiDenseEmbedder.
Provider behavior uses mocks; SDK lifecycle tests use loopback HTTP only.
"""

import asyncio
import concurrent.futures
import json
import socket
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.models.model_error import ModelError, ModelErrorKind


def _make_mock_embedding(values):
    emb = MagicMock()
    emb.values = values
    return emb


def _make_mock_result(values_list):
    result = MagicMock()
    result.embeddings = [_make_mock_embedding(v) for v in values_list]
    return result


@pytest.fixture(params=["aiohttp", "httpx"])
def sdk_lifecycle(request, monkeypatch):
    from google import genai
    from google.genai import types

    from openviking.models.embedder import gemini_embedders

    state = SimpleNamespace(
        clients=[],
        requests=[],
        status=200,
        failures=0,
        block=False,
        entered=Event(),
        release=Event(),
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.connection.settimeout(10)
            state.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            state.entered.set()
            if state.block and not state.release.wait(timeout=10):
                return
            status = 500 if len(state.requests) <= state.failures else state.status
            payload = (
                {"embeddings": [{"values": [0.6, 0.8, 0.0]}]}
                if status == 200
                else {"error": {"code": status, "message": "Permission denied or service failure"}}
            )
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected when the caller cancels a blocked request.

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    server_thread = Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    server_thread.start()
    loops = [asyncio.new_event_loop(), asyncio.new_event_loop()]
    loop_threads = [Thread(target=loop.run_forever, daemon=True) for loop in loops]
    for thread in loop_threads:
        thread.start()
    state.loops = loops

    connect = socket.socket.connect

    def loopback_connect(sock, address):
        assert address[0] in {"127.0.0.1", "::1"}, f"Non-loopback connection: {address}"
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_connect)
    real_client = genai.Client

    def client_factory(**kwargs):
        options = types.HttpOptions.model_validate(kwargs.get("http_options") or {})
        kwargs["http_options"] = options.model_copy(
            update={
                "base_url": f"http://127.0.0.1:{server.server_port}",
                "client_args": {"trust_env": False},
                "async_client_args": {"trust_env": False},
            }
        )
        client = real_client(**kwargs)
        # SDK internals are intentional here: exercise both real transports and
        # inspect their release, which MagicMock-based provider tests cannot prove.
        monkeypatch.setattr(client._api_client, "_use_aiohttp", lambda: request.param == "aiohttp")
        record = SimpleNamespace(
            client=client, transport=request.param, owner=None, closed_on=[], calls=[]
        )
        state.clients.append(record)
        embed_content = client.aio.models.embed_content
        aclose = client.aio.aclose

        async def tracked_embed(**call_kwargs):
            if record.owner is None:
                record.owner = asyncio.get_running_loop()
            record.calls.append(call_kwargs)
            return await embed_content(**call_kwargs)

        async def tracked_close():
            record.closed_on.append(asyncio.get_running_loop())
            await aclose()

        monkeypatch.setattr(client.aio.models, "embed_content", tracked_embed)
        monkeypatch.setattr(client.aio, "aclose", tracked_close)
        return client

    monkeypatch.setattr(gemini_embedders.genai, "Client", client_factory)

    def close_client(record):
        async def cleanup():
            try:
                await record.client.aio.aclose()
            finally:
                record.client.close()

        asyncio.run_coroutine_threadsafe(cleanup(), record.owner or loops[0]).result(timeout=10)

    try:
        state.embedder = gemini_embedders.GeminiDenseEmbedder(
            api_key="loopback-test-key",
            dimension=3,
            query_param="RETRIEVAL_QUERY",
            config={"max_retries": 1},
        )
        yield state
    finally:
        state.release.set()
        try:
            # Also clean up broken implementations, on their original owner loops.
            with ExitStack() as cleanup:
                for record in state.clients:
                    cleanup.callback(close_client, record)
        finally:
            for loop, thread in zip(loops, loop_threads, strict=True):
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=10)
                loop.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=10)


def _assert_sdk_closed(record):
    api_client = record.client._api_client
    assert api_client._httpx_client.is_closed
    if record.owner is not None:
        assert api_client._async_httpx_client.is_closed
        session = api_client._aiohttp_session
        if record.transport == "aiohttp":
            assert session is not None and session.closed
        else:
            assert session is None
        assert record.closed_on
        assert all(loop is record.owner for loop in record.closed_on)
    # The embedder's long-lived sync client never opens an async transport.


def _model_usage(embedder):
    usage = embedder.get_token_usage()["usage_by_model"].get(embedder.model_name)
    return usage["total_usage"] if usage else {"call_count": 0, "prompt_tokens": 0}


def _run_on(loop, coroutine):
    return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=10)


def test_real_sdk_embedding_from_two_live_loops(sdk_lifecycle):
    state = sdk_lifecycle
    embedder = state.embedder
    before = _model_usage(embedder)
    results = [
        _run_on(state.loops[0], embedder.embed_async("document text")),
        _run_on(state.loops[0], embedder.embed_query_async("query text")),
        _run_on(state.loops[1], embedder.embed_async("another document")),
    ]

    assert all(result.dense_vector == pytest.approx([0.6, 0.8, 0.0]) for result in results)
    calls = [call for record in state.clients for call in record.calls]
    assert calls[1]["contents"] == "task: search result | query: query text"
    assert calls[1]["config"].task_type is None
    usage = _model_usage(embedder)
    assert usage["call_count"] == before["call_count"] + 3
    assert usage["prompt_tokens"] > before["prompt_tokens"]
    embedder.close()
    for record in state.clients:
        _assert_sdk_closed(record)


def test_real_sdk_close_releases_sync_and_async_transports(sdk_lifecycle):
    state = sdk_lifecycle
    assert state.embedder.embed("sync text").dense_vector == pytest.approx([0.6, 0.8, 0.0])
    _run_on(state.loops[0], state.embedder.embed_async("async text"))

    async def close_from_other_loop():
        state.embedder.close()

    _run_on(state.loops[1], close_from_other_loop())
    for record in state.clients:
        _assert_sdk_closed(record)


def test_real_sdk_async_retry_keeps_usage_and_closes_client(sdk_lifecycle):
    from openviking.models.embedder.retry_embedder import RetryEmbedder

    state = sdk_lifecycle
    state.failures = 1
    before = _model_usage(state.embedder)
    result = _run_on(state.loops[0], RetryEmbedder(state.embedder).embed_async("retry text"))

    assert result.dense_vector == pytest.approx([0.6, 0.8, 0.0])
    assert len(state.requests) == 2
    assert _model_usage(state.embedder)["call_count"] == before["call_count"] + 1
    state.embedder.close()
    for record in state.clients:
        _assert_sdk_closed(record)


def test_real_sdk_cancellation_releases_request_client(sdk_lifecycle):
    state = sdk_lifecycle
    state.block = True
    finished = Event()

    async def embed_until_cancelled():
        try:
            await state.embedder.embed_async("cancelled text")
        finally:
            finished.set()

    future = asyncio.run_coroutine_threadsafe(embed_until_cancelled(), state.loops[0])
    try:
        assert state.entered.wait(timeout=10)
        assert future.cancel()
        with pytest.raises(concurrent.futures.CancelledError):
            future.result(timeout=10)
        assert finished.wait(timeout=10)
        state.embedder.close()
        for record in state.clients:
            _assert_sdk_closed(record)
    finally:
        future.cancel()
        state.release.set()


def test_real_sdk_async_error_preserves_reason_and_closes_client(sdk_lifecycle):
    state = sdk_lifecycle
    state.status = 403
    with pytest.raises(ModelError, match="Permission denied") as raised:
        _run_on(state.loops[0], state.embedder.embed_async("forbidden text"))

    assert raised.value.kind is ModelErrorKind.PERMISSION_DENIED
    assert raised.value.status_code == 403
    state.embedder.close()
    for record in state.clients:
        _assert_sdk_closed(record)


def test_input_token_limit_constant():
    from openviking.models.embedder.gemini_embedders import _GEMINI_INPUT_TOKEN_LIMIT

    assert _GEMINI_INPUT_TOKEN_LIMIT == 8192


class TestGeminiDenseEmbedderInit:
    def test_requires_api_key(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="api_key"):
            GeminiDenseEmbedder("gemini-embedding-2-preview")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_init_stores_fields(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key="test-key",
            dimension=1536,
            task_type="RETRIEVAL_DOCUMENT",
        )
        assert embedder.model_name == "gemini-embedding-2-preview"
        assert embedder.task_type == "RETRIEVAL_DOCUMENT"
        assert embedder.get_dimension() == 1536
        mock_client_class.assert_called_once()

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_default_dimension_3072(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder(api_key="key")
        assert embedder.model_name == "gemini-embedding-2"
        assert embedder.get_dimension() == 3072

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_dimension_1_valid(self, mock_client_class):
        """API accepts dimension=1 (128 is a quality recommendation, not a hard limit)."""
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=1)
        assert embedder.get_dimension() == 1

    def test_default_dimension_classmethod_prefix_rule(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        assert GeminiDenseEmbedder._default_dimension("gemini-embedding-2") == 3072
        assert GeminiDenseEmbedder._default_dimension("gemini-embedding-2.1") == 3072
        assert GeminiDenseEmbedder._default_dimension("gemini-embedding-3-preview") == 3072
        assert GeminiDenseEmbedder._default_dimension("text-embedding-005") == 768
        assert (
            GeminiDenseEmbedder._default_dimension("text-embedding-004") == 768
        )  # exact match wins
        assert (
            GeminiDenseEmbedder._default_dimension("gemini-embedding-2-preview") == 3072
        )  # exact match

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_token_limit_per_model(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import (
            _MODEL_TOKEN_LIMITS,
            GeminiDenseEmbedder,
        )

        for model, expected in _MODEL_TOKEN_LIMITS.items():
            e = GeminiDenseEmbedder(model, api_key="key")
            assert e._token_limit == expected

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_supports_multimodal_false(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        assert embedder.supports_multimodal is False


class TestGeminiDenseEmbedderEmbed:
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_text(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1, 0.2, 0.3]])
        embedder = GeminiDenseEmbedder(api_key="key", dimension=3)
        result = embedder.embed("hello world")
        assert result.dense_vector is not None
        assert len(result.dense_vector) == 3
        mock_client.models.embed_content.assert_called_once()
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["model"] == "gemini-embedding-2"
        assert kwargs["contents"] == "hello world"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_preview_embed_passes_task_type_in_config(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key="key",
            dimension=1,
            task_type="RETRIEVAL_QUERY",
        )
        embedder.embed("query text", title="Guide")
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["contents"] == "query text"
        assert kwargs["config"].task_type == "RETRIEVAL_QUERY"
        assert kwargs["config"].title == "Guide"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_raises_model_error_on_api_error(self, mock_client_class):
        from google.genai.errors import APIError

        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client.models.embed_content.side_effect = APIError(401, {}, response=mock_response)
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key")
        with pytest.raises(ModelError, match="Gemini embedding failed"):
            embedder.embed("hello")

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_retry_embedder_retries_transient_429(self, mock_client_class, monkeypatch):
        from google.genai.errors import APIError

        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
        from openviking.models.embedder.retry_embedder import RetryEmbedder

        monkeypatch.setattr("openviking.utils.model_retry.time.sleep", lambda _delay: None)
        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client.models.embed_content.side_effect = [
            APIError(
                429,
                {"error": {"message": "Rate limit exceeded. Please retry."}},
                response=mock_response,
            ),
            _make_mock_result([[0.1, 0.2, 0.3]]),
        ]
        inner = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key="key",
            dimension=3,
            config={"max_retries": 1},
        )

        result = RetryEmbedder(inner).embed("hello")

        assert result.dense_vector == [0.1, 0.2, 0.3]
        assert mock_client.models.embed_content.call_count == 2

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_empty_string_returns_zero_vector(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="key", dimension=3)
        for text in ["", "   ", "\t\n"]:
            result = embedder.embed(text)
            assert result.dense_vector == [0.0, 0.0, 0.0]
        mock_client.models.embed_content.assert_not_called()


class TestGeminiValidation:
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_all_valid_task_types_accepted(self, mock_client):
        from openviking.models.embedder.gemini_embedders import (
            _VALID_TASK_TYPES,
            GeminiDenseEmbedder,
        )

        for tt in _VALID_TASK_TYPES:
            e = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k", task_type=tt)
            assert e.task_type == tt

    def test_invalid_task_type_raises_on_init(self):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="Invalid task_type"):
            GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k", task_type="NOT_VALID")

    def test_valid_task_types_count(self):
        from openviking.models.embedder.gemini_embedders import _VALID_TASK_TYPES

        assert len(_VALID_TASK_TYPES) == 8

    def test_code_retrieval_query_in_task_types(self):
        from openviking.models.embedder.gemini_embedders import _VALID_TASK_TYPES

        assert "CODE_RETRIEVAL_QUERY" in _VALID_TASK_TYPES

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_dimension_too_high_raises(self, mock_client):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        with pytest.raises(ValueError, match="3072"):
            GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k", dimension=4096)


class TestGeminiErrorMessages:
    @pytest.mark.parametrize(
        "reason,expected_kind",
        [
            ("QUOTA_EXCEEDED", ModelErrorKind.QUOTA_EXHAUSTED),
            ("RATE_LIMIT_EXCEEDED", ModelErrorKind.RATE_LIMIT_ERROR),
            (None, ModelErrorKind.UNKNOWN),
        ],
    )
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_resource_exhausted_uses_structured_reason(self, mock_client, reason, expected_kind):
        from google.genai.errors import ClientError

        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock = mock_client.return_value
        error = ClientError(
            429,
            {"error": {"details": [] if reason is None else [{"reason": reason}]}},
        )
        mock.models.embed_content.side_effect = error
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k")
        with pytest.raises(ModelError, match="Resource exhausted") as raised:
            embedder.embed("hello")

        assert raised.value.kind is expected_kind
        assert raised.value.status_code == 429
        cause = raised.value.__cause__
        assert cause is not None
        assert cause.__cause__ is error

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_error_message_includes_http_code(self, mock_client):
        from google.genai.errors import APIError

        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock = mock_client.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock.models.embed_content.side_effect = APIError(404, {}, response=mock_resp)
        embedder = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key="k")
        with pytest.raises(ModelError, match="HTTP 404"):
            embedder.embed("hello")


class TestGeminiRequest:
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_embed_per_call_task_type(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value
        mock_client.models.embed_content.return_value = _make_mock_result([[0.1]])
        embedder = GeminiDenseEmbedder(api_key="key", dimension=1, task_type="RETRIEVAL_QUERY")
        embedder.embed("text", task_type="clustering")
        _, kwargs = mock_client.models.embed_content.call_args
        assert kwargs["contents"] == "task: clustering | query: text"
        assert kwargs["config"].model_dump(exclude_none=True) == {"output_dimensionality": 1}

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    async def test_embed_async_per_call_title(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        mock_client = mock_client_class.return_value.__enter__.return_value
        mock_async = mock_client.aio.__aenter__.return_value
        mock_async.models.embed_content = AsyncMock(return_value=_make_mock_result([[0.1]]))
        embedder = GeminiDenseEmbedder(api_key="key", dimension=1)
        result = await embedder.embed_async("text", title="Doc Title")
        kwargs = mock_async.models.embed_content.call_args.kwargs
        assert result.dense_vector == [0.1]
        assert kwargs["contents"] == "title: Doc Title | text: text"
        assert kwargs["config"].model_dump(exclude_none=True) == {"output_dimensionality": 1}

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_repr(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key="key",
            dimension=768,
            task_type="RETRIEVAL_DOCUMENT",
        )
        r = repr(embedder)
        assert "GeminiDenseEmbedder(" in r
        assert "gemini-embedding-2-preview" in r
        assert "768" in r
        assert "RETRIEVAL_DOCUMENT" in r

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_client_constructed_with_retry_options(self, mock_client_class):
        from openviking.models.embedder.gemini_embedders import (
            _HTTP_RETRY_AVAILABLE,
            GeminiDenseEmbedder,
        )

        GeminiDenseEmbedder(api_key="key")
        mock_client_class.assert_called_once()
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs.get("api_key") == "key"
        if _HTTP_RETRY_AVAILABLE:
            assert "http_options" in call_kwargs
