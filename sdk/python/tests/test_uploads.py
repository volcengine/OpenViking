import tempfile
import zipfile
from pathlib import Path

import pytest
from openviking_sdk import AsyncHTTPClient


class _FakeHTTPClient:
    def __init__(self):
        self.calls = []

    async def post(self, path, json=None, files=None, data=None):
        self.calls.append({"path": path, "json": json, "files": files, "data": data})
        return object()


def test_zip_directory_creates_forward_slash_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        root_dir = tmpdir / "test_project"
        root_dir.mkdir()
        (root_dir / "file1.txt").write_text("content1")
        (root_dir / "subdir").mkdir()
        (root_dir / "subdir" / "file2.txt").write_text("content2")

        client = AsyncHTTPClient(url="http://localhost:1933")
        zip_path = client._zip_directory(str(root_dir))

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                assert "file1.txt" in names
                assert "subdir/file2.txt" in names
                assert all("\\" not in name for name in names)
        finally:
            Path(zip_path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_upload_temp_file_forwards_upload_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        upload_file = Path(tmpdir) / "demo.md"
        upload_file.write_text("# Demo\n")

        client = AsyncHTTPClient(
            url="http://localhost:1933",
            upload_mode="shared",
        )
        fake_http = _FakeHTTPClient()
        client._http = fake_http
        client._handle_response = lambda _response: {"temp_file_id": "shared_abc"}

        temp_file_id = await client._upload_temp_file(str(upload_file))

        assert temp_file_id == "shared_abc"
        call = fake_http.calls[-1]
        assert call["path"] == "/api/v1/resources/temp_upload"
        assert call["data"] == {"upload_mode": "shared"}
