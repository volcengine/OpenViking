from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openviking.parse.understanding_api import UnderstandingAPI


@pytest.mark.asyncio
async def test_parse_uses_downloaded_file_and_resolved_extension(monkeypatch, tmp_path):
    downloaded = tmp_path / "download"
    downloaded.write_bytes(b"%PDF-1.7")
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
        original_source="https://example.com/download?id=123",
        resource_name="report",
        resolved_extension=".pdf",
    )

    assert uploaded == [downloaded]
    assert result.source_path == "https://example.com/download?id=123"
    assert result.source_format == "pdf"
    assert result.root.title == "report"


@pytest.mark.asyncio
async def test_submit_file_returns_response_id(tmp_path):
    source = tmp_path / "download.pdf"
    source.write_bytes(b"%PDF-1.7")
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._create_file = AsyncMock(return_value={"id": "file-1"})
    api._create_response_for_file = AsyncMock(return_value={"id": "response-1"})

    response_id = await api.submit_file(source)

    assert response_id == "response-1"
    api._create_file.assert_awaited_once_with(local_path=source)
    api._create_response_for_file.assert_awaited_once_with(file_id="file-1")


async def _return(value):
    return value
