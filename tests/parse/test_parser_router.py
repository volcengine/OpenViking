import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from openviking.parse.parser_router import ParserRouter
from openviking.parse.understanding_api import UnderstandingAPI


def test_should_use_understanding_api_for_signed_video_url(monkeypatch):
    config = SimpleNamespace(
        parser_api=SimpleNamespace(enable=True, extensions=["mp4"]),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )

    router = ParserRouter(parser_registry=object())

    assert router.should_use_understanding_api(
        "https://example.com/media/video.mp4?X-Tos-Signature=abc&X-Tos-Expires=60"
    )


def _make_parse_api():
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._api_base = "http://understanding.test/api/v3"
    api._api_key = "test-key"
    api._video_exts = {"mp4", "mov"}
    api._audio_exts = {"mp3", "wav"}
    api._image_exts = {"jpg", "png"}
    return api


def _patch_parse_io(api, captured):
    """Mock parse I/O and capture the resource name passed to unpack."""

    async def _fake_unpack(zip_path, resource_name):
        captured["resource_name"] = resource_name
        return "viking://temp/uuid"

    stack = contextlib.ExitStack()
    create_file = AsyncMock(return_value={"id": "tos-1"})
    captured["create_file"] = create_file
    stack.enter_context(patch.object(api, "_create_file", create_file))
    stack.enter_context(
        patch.object(api, "_create_response_for_file", AsyncMock(return_value={"id": "resp-1"}))
    )
    create_response_for_url = AsyncMock(return_value={"id": "resp-url"})
    captured["create_response_for_url"] = create_response_for_url
    stack.enter_context(patch.object(api, "_create_response_for_url", create_response_for_url))
    stack.enter_context(
        patch.object(api, "_poll_response", AsyncMock(return_value={"status": "completed"}))
    )
    stack.enter_context(patch.object(api, "_extract_zip_url", MagicMock(return_value="http://zip")))
    stack.enter_context(
        patch.object(api, "_download_zip", AsyncMock(return_value=Path("/tmp/x.zip")))
    )
    stack.enter_context(
        patch.object(api, "_unpack_zip_to_temp_dir", AsyncMock(side_effect=_fake_unpack))
    )
    return stack


@pytest.mark.asyncio
async def test_doc_name_prefers_resource_name(tmp_path):
    temp_file = tmp_path / "upload_2adcdcd01dde42ed82b16bc11ff7391d.pdf"
    temp_file.write_bytes(b"%PDF-1.4 dummy")

    api = _make_parse_api()
    captured = {}
    with _patch_parse_io(api, captured):
        result = await api.parse(str(temp_file), resource_name="Q1_Report")

    assert result.root.title == "Q1_Report"
    assert captured["resource_name"] == "Q1_Report"


@pytest.mark.asyncio
async def test_doc_name_source_name_stripped_of_extension(tmp_path):
    temp_file = tmp_path / "upload_abc.pdf"
    temp_file.write_bytes(b"%PDF-1.4 dummy")

    api = _make_parse_api()
    captured = {}
    with _patch_parse_io(api, captured):
        result = await api.parse(str(temp_file), source_name="Q1 Report.pdf")

    assert result.root.title == "Q1 Report"
    assert captured["resource_name"] == "Q1 Report"
    captured["create_file"].assert_awaited_once_with(
        local_path=temp_file,
        upload_name="Q1 Report.pdf",
    )


@pytest.mark.asyncio
async def test_doc_name_falls_back_to_filename_stem(tmp_path):
    temp_file = tmp_path / "report.pdf"
    temp_file.write_bytes(b"%PDF-1.4 dummy")

    api = _make_parse_api()
    captured = {}
    with _patch_parse_io(api, captured):
        result = await api.parse(str(temp_file))

    assert result.root.title == "report"
    assert captured["resource_name"] == "report"


@pytest.mark.asyncio
async def test_extensionless_original_url_uses_local_markdown_file(tmp_path):
    temp_file = tmp_path / "ov_feishu_ssm79cvh.md"
    temp_file.write_text("# Feishu doc\n\ncontent", encoding="utf-8")

    api = _make_parse_api()
    captured = {}
    with _patch_parse_io(api, captured):
        result = await api.parse(
            str(temp_file),
            original_source="https://bytedance.larkoffice.com/wiki/HxkOwDpoUirXgMkcEIHcmqDZnIg",
            resource_name="Feishu Doc",
        )

    assert result.root.title == "Feishu Doc"
    captured["create_file"].assert_awaited_once_with(
        local_path=temp_file,
        upload_name="Feishu Doc.md",
    )
    assert result.source_format == "md"
    captured["create_response_for_url"].assert_not_awaited()


def _make_multipart_api():
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._upload_part_max_concurrent = 2
    return api


def _wrapped_http_error(status_code):
    request = httpx.Request("PUT", "http://understanding.test/api/v3/files")
    response = httpx.Response(status_code, request=request)
    cause = httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)
    error = RuntimeError("uploads part failed")
    error.__cause__ = cause
    return error


@pytest.mark.asyncio
async def test_uploads_put_parts_concurrently_respects_limit_and_reads_offsets(tmp_path):
    source = tmp_path / "parts.bin"
    source.write_bytes(b"aaabbbccc")

    api = _make_multipart_api()
    active = 0
    max_active = 0
    seen = []

    async def fake_put_part(*, upload_id, object_key, part_number, data):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        seen.append((part_number, data))
        await asyncio.sleep(0)
        active -= 1
        return {"etag": f"etag-{part_number}"}

    api._uploads_put_part = fake_put_part

    uploaded = await api._uploads_put_parts_concurrently(
        file_path=source,
        upload_id="u1",
        object_key="openviking/tos-1",
        part_size=3,
        part_numbers=[1, 2, 3],
    )

    assert uploaded == {1: "etag-1", 2: "etag-2", 3: "etag-3"}
    assert sorted(seen) == [(1, b"aaa"), (2, b"bbb"), (3, b"ccc")]
    assert max_active <= 2


@pytest.mark.asyncio
async def test_multipart_create_file_skips_uploaded_parts_and_completes_sorted(tmp_path):
    source = tmp_path / "parts.bin"
    source.write_bytes(b"aaabbbccc")

    api = _make_multipart_api()
    completed = {}

    async def fake_init(*, file_path, upload_name=None):
        return {
            "upload_id": "u1",
            "object_key": "openviking/tos-1",
            "part_size": 3,
        }

    async def fake_status(*, upload_id, object_key):
        return {"parts": [{"part_number": 2, "etag": "etag-2"}]}

    async def fake_put_parts(**kwargs):
        assert kwargs["part_numbers"] == [1, 3]
        return {3: "etag-3", 1: "etag-1"}

    async def fake_complete(*, upload_id, object_key, parts):
        completed["parts"] = parts
        return {"id": "tos-1", "status": "active"}

    api._uploads_init = fake_init
    api._uploads_status = fake_status
    api._uploads_put_parts_concurrently = fake_put_parts
    api._uploads_complete = fake_complete

    result = await api._multipart_create_file(source)

    assert result == {"id": "tos-1", "status": "active"}
    assert completed["parts"] == [
        {"part_number": 1, "etag": "etag-1"},
        {"part_number": 2, "etag": "etag-2"},
        {"part_number": 3, "etag": "etag-3"},
    ]


@pytest.mark.asyncio
async def test_upload_part_does_not_retry_terminal_4xx(tmp_path):
    source = tmp_path / "part.bin"
    source.write_bytes(b"abc")

    api = _make_multipart_api()
    put_part = AsyncMock(side_effect=_wrapped_http_error(401))
    api._uploads_put_part = put_part

    with patch("asyncio.sleep", new=AsyncMock()) as sleep:
        with pytest.raises(RuntimeError, match="uploads part failed"):
            await api._uploads_put_parts_concurrently(
                file_path=source,
                upload_id="u1",
                object_key="openviking/tos-1",
                part_size=3,
                part_numbers=[1],
            )

    assert put_part.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_part_retries_transient_5xx(tmp_path):
    source = tmp_path / "part.bin"
    source.write_bytes(b"abc")

    api = _make_multipart_api()
    put_part = AsyncMock(
        side_effect=[
            _wrapped_http_error(503),
            _wrapped_http_error(503),
            {"etag": "etag-1"},
        ]
    )
    api._uploads_put_part = put_part

    with patch("asyncio.sleep", new=AsyncMock()) as sleep:
        uploaded = await api._uploads_put_parts_concurrently(
            file_path=source,
            upload_id="u1",
            object_key="openviking/tos-1",
            part_size=3,
            part_numbers=[1],
        )

    assert uploaded == {1: "etag-1"}
    assert put_part.await_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_upload_part_cancellation_stops_pending_tasks(tmp_path):
    source = tmp_path / "parts.bin"
    source.write_bytes(b"aaabbbccc")

    api = _make_multipart_api()
    started = 0
    cancelled = 0
    two_started = asyncio.Event()

    async def fake_put_part(**kwargs):
        nonlocal started, cancelled
        started += 1
        if started == 2:
            two_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled += 1
            raise

    api._uploads_put_part = fake_put_part
    upload_task = asyncio.create_task(
        api._uploads_put_parts_concurrently(
            file_path=source,
            upload_id="u1",
            object_key="openviking/tos-1",
            part_size=3,
            part_numbers=[1, 2, 3],
        )
    )

    await asyncio.wait_for(two_started.wait(), timeout=1.0)
    upload_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await upload_task

    assert started == 2
    assert cancelled == 2


def _make_poll_api():
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._api_base = "http://understanding.test/api/v3"
    api._api_key = "test-key"
    api._http_timeout_sec = 1.0
    api._timeout_sec = 1800
    api._default_poll_interval_ms = 1
    return api


def _response(status_code, body=None):
    response = MagicMock()
    response.status_code = status_code
    if status_code >= 400:
        http_response = httpx.Response(status_code, request=httpx.Request("GET", "http://x"))
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "http error",
            request=http_response.request,
            response=http_response,
        )
    else:
        response.raise_for_status.return_value = None
    response.json.return_value = body or {}
    return response


def test_safe_error_summary_keeps_failed_output():
    output = [
        {
            "type": "message",
            "role": "assistant",
            "status": "failed",
            "content": [{"type": "output_text", "text": "文件解析任务失败。"}],
        }
    ]

    summary = _make_poll_api()._safe_error_summary(
        {"id": "pp_task_1", "status": "failed", "output": output}
    )

    assert summary == {"id": "pp_task_1", "status": "failed", "output": output}


class _FakeAsyncClient:
    def __init__(self, results):
        self.get = AsyncMock(side_effect=results)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_poll_retries_transient_errors_then_succeeds():
    fake = _FakeAsyncClient(
        [
            httpx.ConnectError("blip"),
            _response(503),
            _response(200, {"id": "r1", "status": "completed"}),
        ]
    )
    with (
        patch("openviking.parse.understanding_api.httpx.AsyncClient", return_value=fake),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        body = await _make_poll_api()._poll_response(response_id="r1")

    assert body["status"] == "completed"
    assert fake.get.call_count == 3


@pytest.mark.asyncio
async def test_poll_raises_on_terminal_4xx_without_retry():
    fake = _FakeAsyncClient([_response(404)])
    with (
        patch("openviking.parse.understanding_api.httpx.AsyncClient", return_value=fake),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await _make_poll_api()._poll_response(response_id="r1")

    assert fake.get.call_count == 1


@pytest.mark.asyncio
async def test_poll_gives_up_after_max_transient_retries():
    fake = _FakeAsyncClient([httpx.ConnectError("blip")] * 10)
    with (
        patch("openviking.parse.understanding_api.httpx.AsyncClient", return_value=fake),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="transient errors"):
            await _make_poll_api()._poll_response(response_id="r1")
