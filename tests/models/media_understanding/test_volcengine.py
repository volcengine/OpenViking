# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import asyncio
import gc
import logging
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest
from volcenginesdkarkruntime._exceptions import (
    ArkAPIConnectionError,
    ArkAPITimeoutError,
    ArkAuthenticationError,
    ArkBadRequestError,
)

import openviking.telemetry as telemetry_module
from openviking.models.media_understanding import MediaUnderstandingFactory
from openviking.models.media_understanding.backends import volcengine as volcengine_backend
from openviking.models.media_understanding.backends.volcengine import (
    VolcengineMediaUnderstandingClient,
)
from openviking.models.media_understanding.base import MediaUnderstandingClient


def _response(text: str, *, status: str = "completed"):
    return SimpleNamespace(
        id="response-1",
        status=status,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=40,
            input_tokens_details=SimpleNamespace(cached_tokens=2),
            output_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
    )


def _ark(response_text="# title\n\nbrief\n\n### clip.mp4\n\ndetail"):
    files = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(id="file-1")),
        wait_for_processing=AsyncMock(return_value=SimpleNamespace(status="active")),
        delete=AsyncMock(),
    )
    responses = SimpleNamespace(create=AsyncMock(return_value=_response(response_text)))
    return SimpleNamespace(files=files, responses=responses)


class _StatusError(RuntimeError):
    def __init__(self, status_code, message="", **metadata):
        super().__init__(message)
        self.status_code = status_code
        for name, value in metadata.items():
            setattr(self, name, value)


class _ExplodingStatusAccessorError(RuntimeError):
    @property
    def status_code(self):
        raise RuntimeError("status accessor failed")


class _ExplodingStatusValue:
    def __int__(self):
        raise RuntimeError("status conversion failed")


class _ConcurrencyProbe(MediaUnderstandingClient):
    def __init__(self):
        super().__init__(max_concurrent=2)
        self.active = 0
        self.max_active = 0
        self.two_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def _understand(self, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active == 2:
            self.two_entered.set()
        try:
            await self.release.wait()
            return kwargs["filename"]
        finally:
            self.active -= 1


class _LoopProbe(MediaUnderstandingClient):
    def __init__(self):
        super().__init__(max_concurrent=1)

    async def _understand(self, **kwargs):
        return kwargs["filename"]


class _LoopRetentionProbe(MediaUnderstandingClient):
    def __init__(self):
        super().__init__(max_concurrent=1)
        self.active = 0
        self.release = False

    async def _understand(self, **kwargs):
        self.active += 1
        try:
            while not self.release:
                await asyncio.sleep(0)
            return kwargs["filename"]
        finally:
            self.active -= 1


class _PathProbe(MediaUnderstandingClient):
    def __init__(self, max_concurrent=2, *, fail=False):
        super().__init__(max_concurrent=max_concurrent)
        self.fail = fail
        self.paths = []
        self.path_entered = asyncio.Event()
        self.release_path = asyncio.Event()

    async def _understand(self, **kwargs):
        return kwargs["filename"]

    async def _understand_path(self, *, path, **kwargs):
        self.paths.append(path)
        self.path_entered.set()
        await self.release_path.wait()
        if self.fail:
            raise RuntimeError("provider failed")
        return kwargs["filename"]


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionError(),
        _StatusError(429),
        _StatusError("500"),
        _StatusError(502),
        _StatusError(503),
        _StatusError(504),
    ],
)
def test_media_retry_predicate_accepts_strict_transient_boundaries(error):
    assert volcengine_backend._is_retryable_media_error(error)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422, 501, 505, 599])
def test_media_retry_predicate_rejects_non_transient_http_statuses(status_code):
    assert not volcengine_backend._is_retryable_media_error(_StatusError(status_code))


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("status code 503"),
        RuntimeError("connection reset"),
        httpx.NetworkError("unrelated network error"),
        httpx.TransportError("unrelated transport error"),
    ],
)
def test_media_retry_predicate_rejects_message_heuristics_and_broad_transport(error):
    assert not volcengine_backend._is_retryable_media_error(error)


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("AccountOverdue", "account payment is overdue"),
        ("QuotaExceeded", "usage quota exceeded"),
        ("ContentPolicyViolation", "content is not allowed"),
    ],
)
def test_structured_429_permanent_semantics_are_not_retried(code, message):
    error = _StatusError(429, message, code=code)

    assert not volcengine_backend._is_retryable_media_error(error)


def test_structured_429_checks_permanent_semantics_across_wrapped_error_chain():
    error = _StatusError(429)
    semantic_cause = RuntimeError("")
    semantic_cause.code = "QuotaExceeded"
    error.__cause__ = semantic_cause

    assert not volcengine_backend._is_retryable_media_error(error)


def test_media_retry_predicate_checks_cause_and_context_chains():
    caused = RuntimeError("")
    caused.__cause__ = TimeoutError()

    contextual = RuntimeError("")
    contextual.__context__ = _StatusError(503)

    assert volcengine_backend._is_retryable_media_error(caused)
    assert volcengine_backend._is_retryable_media_error(contextual)


def test_media_retry_predicate_explicit_4xx_overrides_transient_chain():
    error = _StatusError(400)
    error.__cause__ = TimeoutError()

    assert not volcengine_backend._is_retryable_media_error(error)


def test_media_retry_predicate_never_raises_for_hostile_structured_status():
    assert not volcengine_backend._is_retryable_media_error(
        _ExplodingStatusAccessorError("")
    )
    assert not volcengine_backend._is_retryable_media_error(
        _StatusError(_ExplodingStatusValue())
    )


@pytest.mark.parametrize(
    "error",
    [
        ArkAPITimeoutError(httpx.Request("POST", "https://ark.example"), "request-1"),
        ArkAPIConnectionError(
            message="",
            request=httpx.Request("POST", "https://ark.example"),
            request_id="request-1",
        ),
        httpx.TimeoutException(""),
        httpx.ConnectError(""),
    ],
)
def test_media_retry_predicate_accepts_native_transport_errors(error):
    assert volcengine_backend._is_retryable_media_error(error)


@pytest.mark.parametrize(
    "error",
    [
        ArkBadRequestError(
            "bad request",
            response=httpx.Response(
                400, request=httpx.Request("POST", "https://ark.example")
            ),
            body=None,
            request_id="request-1",
        ),
        ArkAuthenticationError(
            "unauthorized",
            response=httpx.Response(
                401, request=httpx.Request("POST", "https://ark.example")
            ),
            body=None,
            request_id="request-1",
        ),
    ],
)
def test_media_retry_predicate_rejects_native_permanent_ark_4xx(error):
    assert not volcengine_backend._is_retryable_media_error(error)


@pytest.mark.asyncio
async def test_base_client_limits_concurrency():
    client = _ConcurrencyProbe()
    tasks = [
        asyncio.create_task(
            client.understand(
                content=b"data",
                filename=f"clip-{index}.mp4",
                media_type="video",
                prompt="prompt",
            )
        )
        for index in range(4)
    ]

    await client.two_entered.wait()
    await asyncio.sleep(0)
    assert client.active == 2
    assert client.max_active == 2
    client.release.set()
    assert len(await asyncio.gather(*tasks)) == 4
    assert client.max_active == 2


@pytest.mark.asyncio
async def test_lazy_content_loaders_share_the_media_concurrency_limit():
    client = _ConcurrencyProbe()
    active_reads = 0
    peak_reads = 0
    read_calls = 0
    two_reads_entered = asyncio.Event()
    release_reads = asyncio.Event()

    async def load_content():
        nonlocal active_reads, peak_reads, read_calls
        read_calls += 1
        active_reads += 1
        peak_reads = max(peak_reads, active_reads)
        if active_reads == 2:
            two_reads_entered.set()
        try:
            await release_reads.wait()
            return b"data"
        finally:
            active_reads -= 1

    tasks = [
        asyncio.create_task(
            client.understand_from_loader(
                content_loader=load_content,
                filename=f"clip-{index}.mp4",
                media_type="video",
                prompt="prompt",
            )
        )
        for index in range(4)
    ]

    await two_reads_entered.wait()
    for _ in range(3):
        await asyncio.sleep(0)
    assert read_calls == 2
    assert peak_reads == 2

    release_reads.set()
    await client.two_entered.wait()
    assert client.max_active == 2
    client.release.set()
    assert len(await asyncio.gather(*tasks)) == 4
    assert peak_reads == 2


@pytest.mark.asyncio
async def test_path_writers_share_the_media_concurrency_limit():
    client = _PathProbe(max_concurrent=2)
    active_writers = 0
    peak_writers = 0
    writer_calls = 0
    two_writers_entered = asyncio.Event()
    release_writers = asyncio.Event()

    async def write_content(path):
        nonlocal active_writers, peak_writers, writer_calls
        writer_calls += 1
        active_writers += 1
        peak_writers = max(peak_writers, active_writers)
        if active_writers == 2:
            two_writers_entered.set()
        try:
            await release_writers.wait()
            path.write_bytes(b"data")
        finally:
            active_writers -= 1

    tasks = [
        asyncio.create_task(
            client.understand_from_writer(
                content_writer=write_content,
                filename=f"clip-{index}.mp4",
                media_type="video",
                prompt="prompt",
            )
        )
        for index in range(4)
    ]

    await two_writers_entered.wait()
    await asyncio.sleep(0)
    assert writer_calls == 2
    assert peak_writers == 2
    release_writers.set()
    client.release_path.set()
    assert len(await asyncio.gather(*tasks)) == 4


@pytest.mark.asyncio
async def test_staged_path_is_removed_after_success_and_failure():
    async def write_content(path):
        path.write_bytes(b"data")

    for fail in (False, True):
        client = _PathProbe(max_concurrent=1, fail=fail)
        client.release_path.set()
        call = client.understand_from_writer(
            content_writer=write_content,
            filename="clip.mp4",
            media_type="video",
            prompt="prompt",
        )
        if fail:
            with pytest.raises(RuntimeError, match="provider failed"):
                await call
        else:
            assert await call == "clip.mp4"
        assert not client.paths[0].exists()


@pytest.mark.asyncio
async def test_staged_path_is_removed_after_cancellation():
    client = _PathProbe(max_concurrent=1)

    async def write_content(path):
        path.write_bytes(b"data")

    task = asyncio.create_task(
        client.understand_from_writer(
            content_writer=write_content,
            filename="clip.mp4",
            media_type="video",
            prompt="prompt",
        )
    )
    await client.path_entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not client.paths[0].exists()


def test_base_client_can_be_reused_across_event_loops():
    client = _LoopProbe()

    async def run_once(filename):
        return await client.understand(
            content=b"data",
            filename=filename,
            media_type="video",
            prompt="prompt",
        )

    assert asyncio.run(run_once("first.mp4")) == "first.mp4"
    assert asyncio.run(run_once("second.mp4")) == "second.mp4"


def test_base_client_does_not_retain_closed_event_loop_after_contention():
    client = _LoopRetentionProbe()
    loop = asyncio.new_event_loop()
    loop_ref = weakref.ref(loop)

    async def run_contended_calls():
        first = asyncio.create_task(
            client.understand(
                content=b"data",
                filename="first.mp4",
                media_type="video",
                prompt="prompt",
            )
        )
        while client.active == 0:
            await asyncio.sleep(0)

        second = asyncio.create_task(
            client.understand(
                content=b"data",
                filename="second.mp4",
                media_type="video",
                prompt="prompt",
            )
        )
        for _ in range(3):
            await asyncio.sleep(0)

        semaphore = client._get_semaphore()
        assert semaphore._loop is asyncio.get_running_loop()
        client.release = True
        assert await asyncio.gather(first, second) == ["first.mp4", "second.mp4"]

    loop.run_until_complete(run_contended_calls())
    loop.close()
    del loop
    gc.collect()

    assert loop_ref() is None
    assert len(client._semaphores) == 0


def test_factory_creates_volcengine_client_and_rejects_other_providers():
    client = MediaUnderstandingFactory.create(
        {"provider": " VolcEngine ", "api_key": "key", "model": "model"}
    )

    assert isinstance(client, VolcengineMediaUnderstandingClient)
    with pytest.raises(ValueError, match="Unsupported media understanding provider"):
        MediaUnderstandingFactory.create(
            {"provider": "other", "api_key": "key", "model": "model"}
        )


def test_build_async_client_disables_sdk_retries():
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "model",
            "api_base": "https://ark.example/v3",
            "timeout": 42,
        }
    )

    with patch(
        "openviking.models.media_understanding.backends.volcengine."
        "volcenginesdkarkruntime.AsyncArk"
    ) as async_ark:
        built = client._build_async_client()

    assert built is async_ark.return_value
    async_ark.assert_called_once_with(
        api_key="key",
        base_url="https://ark.example/v3",
        timeout=42.0,
        max_retries=0,
    )


@pytest.mark.asyncio
async def test_audio_uses_files_and_input_audio(monkeypatch):
    ark = _ark("audio result")
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "audio-model",
            "max_output_tokens": 1024,
            "extra_headers": {"x-request-id": "request-1"},
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    result = await client.understand(
        content=b"ID3-audio",
        filename="meeting.mp3",
        media_type="audio",
        prompt="analyze audio",
    )

    assert result == "audio result"
    assert ark.files.create.await_args.kwargs["purpose"] == "user_data"
    assert ark.files.create.await_args.kwargs["preprocess_configs"] is None
    assert ark.files.create.await_args.kwargs["extra_headers"] == {
        "x-request-id": "request-1"
    }
    ark.files.wait_for_processing.assert_awaited_once_with(
        "file-1", poll_interval=3.0, max_wait_seconds=1800.0
    )
    request = ark.responses.create.await_args.kwargs
    assert request == {
        "model": "audio-model",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "file_id": "file-1"},
                    {"type": "input_text", "text": "analyze audio"},
                ],
            }
        ],
        "max_output_tokens": 1024,
        "store": False,
        "extra_headers": {"x-request-id": "request-1"},
    }
    ark.files.delete.assert_awaited_once_with(
        "file-1", extra_headers={"x-request-id": "request-1"}
    )


@pytest.mark.asyncio
async def test_requests_use_defensive_remote_retention_controls(monkeypatch):
    ark = _ark("audio result")
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "audio-model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)
    monkeypatch.setattr(volcengine_backend.time, "time", lambda: 1_000.0)

    await client.understand(
        content=b"ID3-audio",
        filename="meeting.mp3",
        media_type="audio",
        prompt="analyze audio",
    )

    assert ark.files.create.await_args.kwargs["expire_at"] == 4_600
    assert ark.responses.create.await_args.kwargs["store"] is False


@pytest.mark.asyncio
async def test_video_passes_fps_and_cleans_local_file(monkeypatch):
    ark = _ark("video result")
    uploaded_paths = []

    async def capture_file(**kwargs):
        uploaded_paths.append(Path(kwargs["file"].name))
        assert uploaded_paths[-1].suffix == ".mov"
        assert uploaded_paths[-1].exists()
        return SimpleNamespace(id="file-1")

    ark.files.create.side_effect = capture_file
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "video-model",
            "fps": 0.5,
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    result = await client.understand(
        content=b"video-bytes",
        filename="clip.mov",
        media_type="video",
        prompt="analyze video",
    )

    assert result == "video result"
    assert ark.files.create.await_args.kwargs["preprocess_configs"] == {
        "video": {"fps": 0.5}
    }
    assert not uploaded_paths[0].exists()
    ark.files.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_transient_upload_error_retries_with_same_staged_path(monkeypatch):
    ark = _ark("recovered")
    uploaded_paths = []

    async def fail_then_succeed(**kwargs):
        uploaded_paths.append(Path(kwargs["file"].name))
        if len(uploaded_paths) == 1:
            raise _StatusError(503)
        return SimpleNamespace(id="file-2")

    ark.files.create.side_effect = fail_then_succeed
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "model",
            "max_retries": 1,
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", AsyncMock())

    async def write_content(path):
        path.write_bytes(b"data")

    assert (
        await client.understand_from_writer(
            content_writer=write_content,
            filename="clip.mp4",
            media_type="video",
            prompt="prompt",
        )
        == "recovered"
    )
    assert ark.files.create.await_count == 2
    assert uploaded_paths[0] == uploaded_paths[1]
    assert all(not path.exists() for path in uploaded_paths)
    ark.files.delete.assert_awaited_once_with("file-2", extra_headers={})


@pytest.mark.asyncio
async def test_volcengine_staged_path_does_not_materialize_bytes(monkeypatch):
    ark = _ark("result")
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "model",
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    async def write_content(path):
        path.write_bytes(b"data")

    with patch.object(
        Path,
        "read_bytes",
        side_effect=AssertionError("staged media must not be materialized"),
    ):
        result = await client.understand_from_writer(
            content_writer=write_content,
            filename="clip.mp4",
            media_type="video",
            prompt="prompt",
        )

    assert result == "result"
    ark.files.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_timeout_upload_error_retries(monkeypatch):
    ark = _ark("recovered")
    ark.files.create.side_effect = [
        TimeoutError(),
        SimpleNamespace(id="file-2"),
    ]
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "model",
            "max_retries": 1,
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", AsyncMock())

    assert (
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
        == "recovered"
    )
    assert ark.files.create.await_count == 2


@pytest.mark.asyncio
async def test_native_ark_connection_error_retries(monkeypatch):
    ark = _ark("recovered")
    request = httpx.Request("POST", "https://ark.example/files")
    ark.files.create.side_effect = [
        ArkAPIConnectionError(message="", request=request, request_id="request-1"),
        SimpleNamespace(id="file-2"),
    ]
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "model",
            "max_retries": 1,
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", AsyncMock())

    assert (
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
        == "recovered"
    )
    assert ark.files.create.await_count == 2


@pytest.mark.asyncio
async def test_auth_error_does_not_retry(monkeypatch):
    ark = _ark()
    ark.files.create.side_effect = RuntimeError("status code 401 unauthorized")
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "model",
            "max_retries": 3,
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    with pytest.raises(RuntimeError, match="401"):
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
    assert ark.files.create.await_count == 1


@pytest.mark.asyncio
async def test_remote_cleanup_error_does_not_replace_success(monkeypatch):
    ark = _ark("usable result")
    ark.files.delete.side_effect = RuntimeError("cleanup unavailable")
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    assert (
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
        == "usable result"
    )


@pytest.mark.asyncio
async def test_remote_cleanup_has_a_short_independent_deadline(monkeypatch):
    ark = _ark("usable result")

    async def slow_delete(*args, **kwargs):
        await asyncio.sleep(0.1)

    ark.files.delete.side_effect = slow_delete
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    client._cleanup_timeout = 0.01
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)
    started = asyncio.get_running_loop().time()

    result = await client.understand(
        content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
    )

    assert result == "usable result"
    assert asyncio.get_running_loop().time() - started < 0.05


@pytest.mark.asyncio
async def test_cancellation_still_attempts_remote_and_local_cleanup(monkeypatch):
    ark = _ark("unused")
    entered_wait = asyncio.Event()

    async def wait_forever(*args, **kwargs):
        entered_wait.set()
        await asyncio.Future()

    ark.files.wait_for_processing.side_effect = wait_forever
    uploaded_paths = []

    async def capture_file(**kwargs):
        uploaded_paths.append(Path(kwargs["file"].name))
        return SimpleNamespace(id="file-1")

    ark.files.create.side_effect = capture_file
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    task = asyncio.create_task(
        client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
    )
    await entered_wait.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    ark.files.delete.assert_awaited_once_with("file-1", extra_headers={})
    assert not uploaded_paths[0].exists()


@pytest.mark.asyncio
async def test_cancellation_during_remote_delete_finishes_cleanup(monkeypatch):
    ark = _ark("unused")
    entered_delete = asyncio.Event()
    allow_delete = asyncio.Event()
    delete_finished = asyncio.Event()
    uploaded_paths = []

    async def capture_file(**kwargs):
        uploaded_paths.append(Path(kwargs["file"].name))
        return SimpleNamespace(id="file-1")

    async def controlled_delete(*args, **kwargs):
        entered_delete.set()
        await allow_delete.wait()
        delete_finished.set()

    ark.files.create.side_effect = capture_file
    ark.files.delete.side_effect = controlled_delete
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    task = asyncio.create_task(
        client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
    )
    await entered_delete.wait()
    task.cancel()
    allow_delete.set()

    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert delete_finished.is_set()
        assert not uploaded_paths[0].exists()
    finally:
        for path in uploaded_paths:
            path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_failed_processing_is_an_error_and_cleans_up(monkeypatch):
    ark = _ark("")
    ark.files.wait_for_processing.return_value = SimpleNamespace(
        status="failed", error=SimpleNamespace(message="unsupported codec")
    )
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    with pytest.raises(RuntimeError, match="unsupported codec"):
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
    ark.files.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_failed_processing_is_terminal_even_with_transient_text(monkeypatch):
    ark = _ark("")
    ark.files.wait_for_processing.return_value = SimpleNamespace(
        status="failed", error=SimpleNamespace(message="temporary timeout status 503")
    )
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "model",
            "max_retries": 3,
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", AsyncMock())

    with pytest.raises(RuntimeError) as caught:
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )

    assert type(caught.value).__name__ == "ArkFileProcessingFailedError"
    assert ark.files.create.await_count == 1


@pytest.mark.asyncio
async def test_partial_temp_file_is_removed_when_write_fails(monkeypatch, tmp_path):
    partial_path = tmp_path / "partial.mp4"

    class FailingTempFile:
        name = str(partial_path)

        def __enter__(self):
            self._handle = partial_path.open("wb")
            return self

        def write(self, content):
            self._handle.write(content[:1])
            self._handle.flush()
            raise OSError("disk full")

        def __exit__(self, *args):
            self._handle.close()

    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(
        volcengine_backend.tempfile,
        "NamedTemporaryFile",
        lambda **kwargs: FailingTempFile(),
    )

    try:
        with pytest.raises(OSError, match="disk full"):
            await client.understand(
                content=b"data",
                filename="clip.mp4",
                media_type="video",
                prompt="prompt",
            )
        assert not partial_path.exists()
    finally:
        partial_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_retry_and_cleanup_logs_never_include_provider_message_sentinels(
    monkeypatch, caplog
):
    ark = _ark("recovered")
    ark.files.create.side_effect = [
        _StatusError(
            503,
            "SECRET_API_KEY SECRET_PROMPT SECRET_MEDIA_BYTES SECRET_RESPONSE",
            code="ServiceUnavailable",
            request_id="request-safe",
        ),
        SimpleNamespace(id="file-2"),
    ]
    ark.files.delete.side_effect = RuntimeError("SECRET_CLEANUP_RESPONSE")
    client = VolcengineMediaUnderstandingClient(
        {
            "provider": "volcengine",
            "api_key": "key",
            "model": "model",
            "max_retries": 1,
        }
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", AsyncMock())

    with caplog.at_level(logging.WARNING, logger=volcengine_backend.logger.name):
        assert (
            await client.understand(
                content=b"data",
                filename="clip.mp4",
                media_type="video",
                prompt="prompt",
            )
            == "recovered"
        )

    assert "SECRET_" not in caplog.text
    assert "_StatusError" in caplog.text
    assert "status=503" in caplog.text
    assert "request_id=request-safe" in caplog.text


@pytest.mark.asyncio
async def test_cleanup_error_preserves_original_inference_error(monkeypatch):
    ark = _ark("")
    ark.files.wait_for_processing.return_value = SimpleNamespace(
        status="failed", error=SimpleNamespace(message="unsupported codec")
    )
    ark.files.delete.side_effect = RuntimeError("cleanup unavailable")
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    with pytest.raises(RuntimeError, match="unsupported codec"):
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response("", status="completed"), "contained no output text"),
        (_response("unused", status="failed"), "did not complete"),
    ],
)
async def test_invalid_response_is_an_error_and_cleans_up(monkeypatch, response, message):
    ark = _ark()
    ark.responses.create.return_value = response
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    with pytest.raises(RuntimeError, match=message):
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
    ark.files.delete.assert_awaited_once()


def test_extract_response_text_only_uses_message_output_text():
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(type="output_text", text="first"),
                    SimpleNamespace(type="refusal", text="ignore"),
                    SimpleNamespace(type="output_text", text="second"),
                ],
            ),
            SimpleNamespace(
                type="reasoning",
                content=[SimpleNamespace(type="output_text", text="ignore")],
            ),
        ]
    )

    assert VolcengineMediaUnderstandingClient._extract_response_text(response) == (
        "first\nsecond"
    )


@pytest.mark.asyncio
async def test_success_records_usage_without_changing_result(monkeypatch):
    ark = _ark("plain string")
    telemetry = MagicMock()
    record_call = MagicMock()
    monkeypatch.setattr(telemetry_module, "get_current_telemetry", lambda: telemetry)
    monkeypatch.setattr(
        telemetry_module, "get_current_telemetry_stage", lambda: "media_stage"
    )
    monkeypatch.setattr(
        "openviking.metrics.datasources.VLMEventDataSource.record_call", record_call
    )
    monkeypatch.setattr(
        "openviking.observability.context.get_root_observability_context",
        lambda: SimpleNamespace(account_id="account-1"),
    )
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    result = await client.understand(
        content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
    )

    assert result == "plain string"
    telemetry.add_token_usage.assert_called_once_with(
        120,
        40,
        stage="media_stage",
        prompt_cached_tokens=2,
        completion_reasoning_tokens=3,
    )
    record_call.assert_called_once_with(
        provider="volcengine",
        model_name="model",
        duration_seconds=ANY,
        prompt_tokens=120,
        completion_tokens=40,
        account_id="account-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        SimpleNamespace(input_tokens="not-a-number", output_tokens=1),
        SimpleNamespace(input_tokens=1, output_tokens=object()),
    ],
)
async def test_malformed_usage_never_replaces_valid_result(monkeypatch, usage):
    ark = _ark("usable result")
    ark.responses.create.return_value.usage = usage
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    assert (
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
        == "usable result"
    )


@pytest.mark.asyncio
async def test_usage_attribute_failure_never_replaces_result(monkeypatch):
    class ExplodingUsageResponse:
        id = "response-1"
        status = "completed"
        output = _response("usable result").output

        @property
        def usage(self):
            raise RuntimeError("malformed usage")

    ark = _ark()
    ark.responses.create.return_value = ExplodingUsageResponse()
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    assert (
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
        == "usable result"
    )


@pytest.mark.asyncio
async def test_telemetry_dependency_failures_never_replace_result(monkeypatch):
    ark = _ark("usable result")
    monkeypatch.setattr(
        telemetry_module,
        "get_current_telemetry",
        MagicMock(side_effect=RuntimeError("telemetry unavailable")),
    )
    monkeypatch.setattr(
        "openviking.metrics.datasources.VLMEventDataSource.record_call",
        MagicMock(side_effect=RuntimeError("metrics unavailable")),
    )
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    assert (
        await client.understand(
            content=b"data", filename="clip.mp4", media_type="video", prompt="prompt"
        )
        == "usable result"
    )


@pytest.mark.asyncio
async def test_oversized_content_is_rejected_before_upload(monkeypatch):
    class OversizedContent:
        def __len__(self):
            return 512 * 1024 * 1024 + 1

    ark = _ark()
    client = VolcengineMediaUnderstandingClient(
        {"provider": "volcengine", "api_key": "key", "model": "model"}
    )
    monkeypatch.setattr(client, "_build_async_client", lambda: ark)

    with pytest.raises(ValueError, match="512 MB"):
        await client.understand(
            content=OversizedContent(),
            filename="clip.mp4",
            media_type="video",
            prompt="prompt",
        )
    ark.files.create.assert_not_awaited()
