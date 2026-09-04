from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from openviking.parse.understanding_api import (
    PREPARED_FILE_ID_ARG,
    UnderstandingAPI,
    UnderstandingAPIError,
)
from openviking_cli.exceptions import InvalidArgumentError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename,content,original_source,resolved_extension,source_format",
    [
        ("download", b"%PDF-1.7", "https://example.com/download?id=123", ".pdf", "pdf"),
        ("page.html", b"<h1>Page</h1>", "https://example.com/page.html?view=full", "", "html"),
        ("page.html", b"<h1>Page</h1>", "https://example.com/article?id=123", ".html", "html"),
    ],
)
async def test_parse_uses_downloaded_file_and_resolved_extension(
    monkeypatch, tmp_path, filename, content, original_source, resolved_extension, source_format
):
    downloaded = tmp_path / filename
    downloaded.write_bytes(content)
    zip_path = tmp_path / "result.zip"
    zip_path.write_bytes(b"zip")
    uploaded: list[Path] = []

    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._video_exts = {"mp4"}
    api._audio_exts = {"mp3"}
    api._image_exts = {"png"}

    async def create_file(*, local_path):
        uploaded.append(local_path)
        return {"id": "file-1"}

    async def create_response_for_file(*, file_id):
        assert file_id == "file-1"
        return {"id": "response-1"}

    async def poll_response(*, response_id):
        assert response_id == "response-1"
        return {"status": "completed"}

    monkeypatch.setattr(api, "_create_file", create_file)
    monkeypatch.setattr(api, "_create_response_for_file", create_response_for_file)
    monkeypatch.setattr(api, "_poll_response", poll_response)
    monkeypatch.setattr(api, "_extract_zip_url", lambda _: "https://example.com/result.zip")
    monkeypatch.setattr(api, "_download_zip", lambda _: _return(zip_path))
    monkeypatch.setattr(
        api,
        "_unpack_zip_to_temp_dir",
        lambda **_: _return("viking://temp/result"),
    )

    result = await api.parse(
        downloaded,
        original_source=original_source,
        resource_name="report",
        resolved_extension=resolved_extension,
    )

    assert uploaded == [downloaded]
    assert result.source_path == original_source
    assert result.source_format == source_format
    assert result.root.title == "report"


@pytest.mark.asyncio
async def test_upload_file_validates_input_and_returns_file_id(tmp_path):
    empty_source = tmp_path / "empty.pdf"
    empty_source.touch()
    api = UnderstandingAPI.__new__(UnderstandingAPI)

    with pytest.raises(
        InvalidArgumentError,
        match="Understanding parser does not support empty files",
    ) as exc_info:
        await api.upload_file(empty_source)

    assert exc_info.value.code == "INVALID_ARGUMENT"

    source = tmp_path / "download.pdf"
    source.write_bytes(b"%PDF-1.7")
    api._create_file = AsyncMock(return_value={"id": "file-1"})

    file_id = await api.upload_file(source)

    assert file_id == "file-1"
    api._create_file.assert_awaited_once_with(local_path=source)


@pytest.mark.asyncio
async def test_parse_prepared_file_id_creates_response_and_polls(monkeypatch, tmp_path):
    zip_path = tmp_path / "result.zip"
    zip_path.write_bytes(b"zip")
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._video_exts = {"mp4"}
    api._audio_exts = {"mp3"}
    api._image_exts = {"png"}
    api._create_file = AsyncMock(side_effect=AssertionError("prepared file must not reupload"))
    api._create_response_for_file = AsyncMock(return_value={"id": "response-1"})

    async def poll_response(*, response_id):
        assert response_id == "response-1"
        return {"status": "completed"}

    monkeypatch.setattr(api, "_poll_response", poll_response)
    monkeypatch.setattr(api, "_extract_zip_url", lambda _: "https://example.com/result.zip")
    monkeypatch.setattr(api, "_download_zip", lambda _: _return(zip_path))
    monkeypatch.setattr(
        api,
        "_unpack_zip_to_temp_dir",
        lambda **_: _return("viking://temp/result"),
    )

    result = await api.parse(
        "/tmp/upload_already_cleaned.pdf",
        source_name="uploaded.pdf",
        resolved_extension=".pdf",
        **{PREPARED_FILE_ID_ARG: "file-1"},
    )

    api._create_response_for_file.assert_awaited_once_with(file_id="file-1")
    assert result.meta["file_id"] == "file-1"
    assert result.meta["response_id"] == "response-1"
    assert result.source_format == "pdf"
    api._create_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_above_simple_limit_requires_resumable_upload(tmp_path):
    source = tmp_path / "large.pdf"
    source.write_bytes(b"123456789")
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._upload_simple_max_bytes = 8
    api._enable_resumable_upload = False

    with pytest.raises(ValueError, match="size=9, upload_simple_max_bytes=8"):
        await api._create_file(local_path=source)


@pytest.mark.asyncio
async def test_file_above_simple_limit_uses_multipart_when_enabled(tmp_path):
    source = tmp_path / "large.pdf"
    source.write_bytes(b"123456789")
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._upload_simple_max_bytes = 8
    api._enable_resumable_upload = True
    api._multipart_create_file = AsyncMock(return_value={"id": "file-1"})

    result = await api._create_file(local_path=source)

    assert result == {"id": "file-1"}
    api._multipart_create_file.assert_awaited_once_with(source)


@pytest.mark.asyncio
async def test_parse_failure_preserves_observed_remote_ids(monkeypatch, tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.7")
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._video_exts = {"mp4"}
    api._audio_exts = {"mp3"}
    api._image_exts = {"png"}
    warning = Mock()
    monkeypatch.setattr("openviking.parse.understanding_api.logger.warning", warning)

    monkeypatch.setattr(api, "_create_file", AsyncMock(return_value={"id": "file-1"}))
    monkeypatch.setattr(
        api,
        "_create_response_for_file",
        AsyncMock(return_value={"id": "response-1"}),
    )
    monkeypatch.setattr(
        api,
        "_poll_response",
        AsyncMock(side_effect=RuntimeError("remote parse failed")),
    )

    with pytest.raises(UnderstandingAPIError, match="remote parse failed") as exc_info:
        await api.parse(source, source_name="original.pdf")

    assert exc_info.value.meta == {
        "doc_name": "original",
        "doc_type": "pdf",
        "source_name": "original.pdf",
        "file_name": "report.pdf",
        "file_id": "file-1",
        "response_id": "response-1",
    }
    assert str(exc_info.value) == "remote parse failed"
    assert isinstance(exc_info.value, RuntimeError)
    warning.assert_called_once_with(
        "[UnderstandingAPI] Parse failed: %s; meta=%s",
        exc_info.value.__cause__,
        exc_info.value.meta,
    )


async def _return(value):
    return value


def _api_with_transport(monkeypatch, handler):
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._api_base = "https://parser.example.test/api/v3"
    api._api_key = "test-key"
    api._http_timeout_sec = 1.0
    api._timeout_sec = 2.0
    api._default_poll_interval_ms = 0
    api._upload_simple_max_bytes = 1024
    api._upload_part_size_bytes = 512
    api._video_exts = {"mp4"}
    api._audio_exts = {"mp3"}
    api._image_exts = {"png"}
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        "openviking.parse.understanding_api.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs),
    )
    return api


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["create_response", "poll_response"])
async def test_prepared_file_failure_preserves_reason_and_remote_ids(
    monkeypatch, tmp_path, failure_stage
):
    message = "文件解析任务失败：未生成可解析内容"
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/v3/responses"
            if failure_stage == "create_response":
                return httpx.Response(
                    400, json={"error": {"code": "InvalidParameter", "message": message}}
                )
            return httpx.Response(200, json={"id": "response-1"})
        assert request.url.path == "/api/v3/responses/response-1"
        return httpx.Response(
            200,
            json={
                "id": "response-1",
                "status": "failed",
                "output": [{"content": [{"type": "output_text", "text": message}]}],
            },
        )

    api = _api_with_transport(monkeypatch, handler)
    api._create_file = AsyncMock(side_effect=AssertionError("prepared file must not reupload"))
    source = tmp_path / "upload_already_cleaned.pdf"

    with pytest.raises(UnderstandingAPIError, match=message) as exc_info:
        await api.parse(
            source,
            source_name="report.pdf",
            resolved_extension=".pdf",
            **{PREPARED_FILE_ID_ARG: "file-1"},
        )

    error = exc_info.value
    assert error.meta["file_id"] == "file-1"
    assert error.meta["source_name"] == "report.pdf"
    if failure_stage == "create_response":
        assert "response_id" not in error.meta
        assert isinstance(error.__cause__, httpx.HTTPStatusError)
        assert error.__cause__.response.status_code == 400
        assert len(requests) == 1
    else:
        assert error.meta["response_id"] == "response-1"
        assert str(error) == f"understanding failed: {message}"
        assert len(requests) == 2
    api._create_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "_create_file",
        "_create_response_for_file",
        "_create_response_for_url",
        "_poll_response",
        "_uploads_init",
        "_uploads_status",
        "_uploads_put_part",
        "_uploads_complete",
    ],
)
@pytest.mark.parametrize("nested_error", [False, True])
async def test_http_errors_preserve_business_message(monkeypatch, tmp_path, method, nested_error):
    message = (
        "One or more parameters specified in the request are not valid. "
        "file too large (536870913 > 536870912), please use multipart upload"
    )
    body = {"message": message, "input": "private-input"}
    if nested_error:
        body = {
            "error": {
                "code": "InvalidParameter",
                "message": message,
                "internal": "private-internal",
            },
            "input": "private-input",
        }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(400, json=body)

    api = _api_with_transport(monkeypatch, handler)
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-fixture")
    arguments = {
        "_create_file": {"local_path": source},
        "_create_response_for_file": {"file_id": "file-1"},
        "_create_response_for_url": {"url": "https://example.test/a.pdf", "doc_type": "pdf"},
        "_poll_response": {"response_id": "response-1"},
        "_uploads_init": {"file_path": source},
        "_uploads_status": {"upload_id": "upload-1", "object_key": "object-1"},
        "_uploads_put_part": {
            "upload_id": "upload-1",
            "object_key": "object-1",
            "part_number": 1,
            "data": b"part",
        },
        "_uploads_complete": {"upload_id": "upload-1", "object_key": "object-1", "parts": []},
    }

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await getattr(api, method)(**arguments[method])

    assert len(requests) == 1
    assert exc_info.value.response.status_code == 400
    assert exc_info.value.request is requests[0]
    assert message in str(exc_info.value)
    assert "private-input" not in str(exc_info.value)
    assert "private-internal" not in str(exc_info.value)
    if nested_error:
        assert "InvalidParameter" in str(exc_info.value)


@pytest.mark.asyncio
async def test_response_http_error_preserves_param_and_type(monkeypatch):
    message = "One or more parameters specified in the request are not valid. request body is empty"
    body = {"error": {"code": "InvalidParameter", "message": message, "param": "", "type": ""}}
    api = _api_with_transport(monkeypatch, lambda _: httpx.Response(400, json=body))

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await api._create_response_for_file(file_id="file-1")

    assert api._safe_error_summary(body) == body
    assert "InvalidParameter" in str(exc_info.value)
    assert message in str(exc_info.value)
    assert "'param': ''" in str(exc_info.value)
    assert "'type': ''" in str(exc_info.value)


@pytest.mark.asyncio
async def test_non_json_http_error_preserves_http_exception(monkeypatch):
    api = _api_with_transport(
        monkeypatch, lambda _: httpx.Response(502, text="<html>gateway</html>")
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await api._create_response_for_file(file_id="file-1")

    assert exc_info.value.response.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_create_response_for_file", "_poll_response"])
async def test_api_calls_do_not_retry_transient_http_error(monkeypatch, method):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"code": "unavailable", "message": "try later"}})

    api = _api_with_transport(monkeypatch, handler)
    arguments = (
        {"file_id": "file-1"}
        if method == "_create_response_for_file"
        else {"response_id": "response-1"}
    )
    with pytest.raises(httpx.HTTPStatusError, match="try later"):
        await getattr(api, method)(**arguments)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_parse_http_failure_preserves_message_and_remote_ids(monkeypatch, tmp_path):
    api = _api_with_transport(
        monkeypatch,
        lambda _: httpx.Response(400, json={"error": {"code": "invalid", "message": "bad file"}}),
    )
    api._create_file = AsyncMock(return_value={"id": "file-1"})
    api._create_response_for_file = AsyncMock(return_value={"id": "response-1"})
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-fixture")

    with pytest.raises(UnderstandingAPIError, match="bad file") as exc_info:
        await api.parse(source)

    assert exc_info.value.meta["file_id"] == "file-1"
    assert exc_info.value.meta["response_id"] == "response-1"
    assert isinstance(exc_info.value.__cause__, httpx.HTTPStatusError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "文件解析任务失败。",
        "文件解析任务失败：未生成可解析内容",
        "文件解析任务失败：empty parse result",
    ],
)
async def test_parse_failed_response_preserves_output_text(monkeypatch, tmp_path, message):
    body = {
        "id": "response-1",
        "status": "failed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "failed",
                "content": [{"type": "output_text", "text": message}],
            }
        ],
    }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    api = _api_with_transport(monkeypatch, handler)
    api._create_file = AsyncMock(return_value={"id": "file-1"})
    api._create_response_for_file = AsyncMock(return_value={"id": "response-1"})
    api._download_zip = AsyncMock()
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-fixture")

    with pytest.raises(UnderstandingAPIError, match=message) as exc_info:
        await api.parse(source)

    assert len(requests) == 1
    assert str(exc_info.value) == f"understanding failed: {message}"
    assert exc_info.value.meta["file_id"] == "file-1"
    assert exc_info.value.meta["response_id"] == "response-1"
    assert api._safe_error_summary(body) == {
        "id": "response-1",
        "status": "failed",
        "output_text": [message],
    }
    api._download_zip.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "message", "expected"),
    [
        ({"message": "upstream reason"}, "top-level reason", "upstream reason"),
        ({"message": " "}, "top-level reason", "top-level reason"),
        ({}, None, "first reason; second reason"),
    ],
)
def test_error_message_prefers_business_reason(error, message, expected):
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    body = {
        "id": "response-1",
        "status": "failed",
        "error": error,
        "message": message,
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "first reason"},
                    {"type": "output_text", "text": "second reason"},
                ]
            }
        ],
    }
    assert api._error_message(body) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, "request failed"),
        ({"id": "response-1", "status": "failed"}, "request failed"),
        ({"error": {"code": "InvalidParameter"}}, "InvalidParameter"),
    ],
)
def test_error_message_fallback_does_not_include_remote_ids(body, expected):
    assert UnderstandingAPI.__new__(UnderstandingAPI)._error_message(body) == expected


def test_failed_response_summary_keeps_only_output_text():
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    body = {
        "id": "response-1",
        "status": "failed",
        "input": "private-input",
        "output": [
            None,
            {"content": None},
            {"content": {"text": "private-content"}},
            {
                "content": [
                    None,
                    {"type": "output_text", "text": None},
                    {"type": "output_text", "text": " "},
                    {"type": "input_text", "text": "private-input"},
                    {"type": "zip_url", "zip_url": {"url": "https://private.test/?token=secret"}},
                    {"type": "output_text", "text": "first reason", "internal": "private"},
                ]
            },
            {"content": [{"type": "output_text", "text": "second reason"}]},
        ],
    }

    assert api._safe_error_summary(body) == {
        "id": "response-1",
        "status": "failed",
        "output_text": ["first reason", "second reason"],
    }


@pytest.mark.parametrize("status", ["in_progress", "completed"])
def test_non_failed_response_summary_does_not_include_output_text(status):
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    body = {
        "id": "response-1",
        "status": status,
        "output": [{"content": [{"type": "output_text", "text": "private document content"}]}],
    }

    assert api._safe_error_summary(body) == {"id": "response-1", "status": status}
