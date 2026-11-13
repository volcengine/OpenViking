import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openviking.parse.image_rewrite import (
    IMAGE_MAPPINGS_FILENAME,
    build_artifact_image_mappings,
)
from openviking.parse.output import LocalParseOutputStore, read_artifact_manifest
from openviking.parse.understanding_api import UnderstandingAPI


class _FakeVikingFS:
    def __init__(self):
        self.files = {}
        self.dirs = set()
        self.deleted_temps = []

    def create_temp_uri(self):
        return "viking://temp/artifact"

    async def mkdir(self, uri, exist_ok=False):
        self.dirs.add(uri)

    async def write_file_bytes(self, uri, content):
        self.files[uri] = content

    async def write_file(self, uri, content):
        self.files[uri] = content.encode("utf-8")

    async def delete_temp(self, uri):
        self.deleted_temps.append(uri)


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
        artifact_ref = await api._unpack_zip_to_temp_dir(zip_path, "resource")

    assert artifact_ref.root == "viking://temp/artifact"
    assert artifact_ref.resource_rel == "resource"
    sidecar_uri = f"{artifact_ref.root}/resource/{IMAGE_MAPPINGS_FILENAME}"
    assert json.loads(fake_fs.files[sidecar_uri]) == {
        "Ov测试_1.md": {"Ov测试_1_img1.png": "Ov测试_1_img1.png"}
    }
    assert fake_fs.files[f"{artifact_ref.root}/resource/Ov测试_1_img1.png"] == b"png"


@pytest.mark.asyncio
async def test_unpack_artifact_cleans_temp_on_failure(tmp_path: Path):
    invalid_zip = tmp_path / "invalid.zip"
    invalid_zip.write_bytes(b"not-a-zip")
    fake_fs = _FakeVikingFS()
    api = UnderstandingAPI.__new__(UnderstandingAPI)

    with (
        patch("openviking.parse.understanding_api.get_viking_fs", return_value=fake_fs),
        pytest.raises(zipfile.BadZipFile),
    ):
        await api._unpack_zip_to_temp_dir(invalid_zip, "resource")

    assert fake_fs.deleted_temps == ["viking://temp/artifact"]


@pytest.mark.asyncio
async def test_unpack_artifact_supports_local_output_store(tmp_path: Path):
    zip_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("artifact/section.md", "content")

    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    ref = await api._unpack_zip_to_temp_dir(zip_path, "resource", parse_output_store=store)

    assert ref.backend == "local"
    assert ref.resource_rel == "resource"
    assert Path(ref.root, "resource", "section.md").read_text() == "content"
    assert set(await read_artifact_manifest(store, ref)) == {"resource/section.md"}


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["Title ", "Title\t\n", "Title\u00a0\u3000"])
@pytest.mark.parametrize("backend", ["agfs", "local"])
async def test_title_whitespace_uses_one_artifact_root(tmp_path, title, backend):
    class TrimmingFS(_FakeVikingFS):
        async def mkdir(self, uri, exist_ok=False):
            self.dirs.add(uri.strip())

        async def write_file_bytes(self, uri, content):
            self.dirs.add(uri.rsplit("/", 1)[0])
            await super().write_file_bytes(uri, content)

    archive_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("document/body.md", "content")
    fs = TrimmingFS()
    options = {}
    if backend == "local":
        options["parse_output_store"] = LocalParseOutputStore(str(tmp_path / "artifacts"))
    with patch("openviking.parse.understanding_api.get_viking_fs", return_value=fs):
        ref = await UnderstandingAPI.__new__(UnderstandingAPI)._unpack_zip_to_temp_dir(
            archive_path, title, **options
        )
    assert ref.resource_rel == "Title"
    if backend == "local":
        assert [p.name for p in Path(ref.root).iterdir() if p.is_dir()] == ["Title"]
        assert Path(ref.root, "Title/body.md").read_text() == "content"
    else:
        assert fs.dirs - {ref.root} == {f"{ref.root}/Title"}
        assert fs.files[f"{ref.root}/Title/body.md"] == b"content"
