import asyncio
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openviking.parse.image_rewrite import (
    IMAGE_MAPPINGS_FILENAME,
    build_artifact_image_mappings,
)
from openviking.parse.understanding_api import UnderstandingAPI


class _FakeVikingFS:
    def __init__(self):
        self.files = {}
        self.dirs = set()
        self.deleted = []

    def create_temp_uri(self):
        return "viking://temp/artifact"

    async def mkdir(self, uri, exist_ok=False):
        self.dirs.add(uri)

    async def write_file_bytes(self, uri, content):
        self.files[uri] = content

    async def write_file(self, uri, content):
        self.files[uri] = content.encode("utf-8")

    async def delete_temp(self, uri):
        self.deleted.append(uri)


def test_build_artifact_image_mappings_uses_existing_sibling_images(tmp_path: Path):
    chapter = tmp_path / "章节"
    chapter.mkdir()
    (chapter / "正文_img1.png").write_bytes(b"png")
    (chapter / "正文_img2.jpg").write_bytes(b"jpg")
    (chapter / "正文.md").write_text(
        "\n".join(
            [
                "![image](正文_img1.png)",
                '<img src="./正文_img2.jpg">',
                "![remote](https://example.com/a.png)",
                "![missing](missing.png)",
                "```markdown",
                "![example](正文_img1.png)",
                "```",
            ]
        ),
        encoding="utf-8",
    )

    assert build_artifact_image_mappings(tmp_path) == {
        "章节/正文.md": {
            "正文_img1.png": "正文_img1.png",
            "./正文_img2.jpg": "正文_img2.jpg",
        }
    }


@pytest.mark.asyncio
async def test_unpack_artifact_writes_image_mapping_sidecar(tmp_path: Path):
    zip_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("artifact/Ov测试_1.md", "![image](Ov测试_1_img1.png)\n")
        archive.writestr("artifact/Ov测试_1_img1.png", b"png")
        archive.writestr(
            f"artifact/{IMAGE_MAPPINGS_FILENAME}",
            '{"untrusted.md":{"bad.png":"bad.png"}}',
        )

    fake_fs = _FakeVikingFS()
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    with patch("openviking.parse.understanding_api.get_viking_fs", return_value=fake_fs):
        temp_uri = await api._unpack_zip_to_temp_dir(zip_path, "resource")

    assert temp_uri == "viking://temp/artifact"
    sidecar_uri = f"{temp_uri}/resource/{IMAGE_MAPPINGS_FILENAME}"
    assert json.loads(fake_fs.files[sidecar_uri]) == {
        "Ov测试_1.md": {"Ov测试_1_img1.png": "Ov测试_1_img1.png"}
    }
    assert fake_fs.files[f"{temp_uri}/resource/Ov测试_1_img1.png"] == b"png"


@pytest.mark.asyncio
async def test_unpack_failure_deletes_temp_tree(tmp_path: Path):
    zip_path = tmp_path / "broken.zip"
    zip_path.write_bytes(b"not a zip")
    fake_fs = _FakeVikingFS()
    api = UnderstandingAPI.__new__(UnderstandingAPI)

    with (
        patch("openviking.parse.understanding_api.get_viking_fs", return_value=fake_fs),
        pytest.raises(zipfile.BadZipFile),
    ):
        await api._unpack_zip_to_temp_dir(zip_path, "resource")

    assert fake_fs.deleted == ["viking://temp/artifact"]


@pytest.mark.asyncio
async def test_unpack_cancellation_deletes_temp_tree(tmp_path: Path):
    fake_fs = _FakeVikingFS()

    async def cancel(_uri, exist_ok=False):
        raise asyncio.CancelledError

    fake_fs.mkdir = cancel
    api = UnderstandingAPI.__new__(UnderstandingAPI)

    with (
        patch("openviking.parse.understanding_api.get_viking_fs", return_value=fake_fs),
        pytest.raises(asyncio.CancelledError),
    ):
        await api._unpack_zip_to_temp_dir(tmp_path / "unused.zip", "resource")

    assert fake_fs.deleted == ["viking://temp/artifact"]


@pytest.mark.asyncio
async def test_unpack_cleanup_failure_preserves_original_error(tmp_path: Path):
    zip_path = tmp_path / "broken.zip"
    zip_path.write_bytes(b"not a zip")
    fake_fs = _FakeVikingFS()

    async def fail_cleanup(_uri):
        raise RuntimeError("cleanup failed")

    fake_fs.delete_temp = fail_cleanup
    api = UnderstandingAPI.__new__(UnderstandingAPI)

    with (
        patch("openviking.parse.understanding_api.get_viking_fs", return_value=fake_fs),
        pytest.raises(zipfile.BadZipFile),
    ):
        await api._unpack_zip_to_temp_dir(zip_path, "resource")
