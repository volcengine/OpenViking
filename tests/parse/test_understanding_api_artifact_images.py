import json
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, unquote, urlsplit

import pytest
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from PIL import Image

from openviking.parse.image_rewrite import (
    IMAGE_MAPPINGS_FILENAME,
    _rewrite_content,
    build_artifact_image_mappings,
)
from openviking.parse.output import LocalParseOutputStore, read_artifact_manifest
from openviking.parse.parsers.markdown import MarkdownParser
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stem",
    [
        "plain",
        "space name",
        "中文 图",
        "percent%20",
        "hash#image",
        "plus+image",
        "中文",
        "closing)paren",
    ],
)
async def test_build_artifact_image_mappings_uses_existing_sibling_images(tmp_path: Path, stem):
    chapter = tmp_path / "章节"
    chapter.mkdir()
    png, jpg = f"{stem}.png", f"{stem}.jpg"
    Image.new("RGB", (64, 64), "red").save(chapter / png)
    Image.new("RGB", (64, 64), "blue").save(chapter / jpg)
    Image.new("RGB", (64, 64), "green").save(tmp_path / "secret.png")
    png_ref, jpg_ref = quote(png), f"./{quote(jpg)}"
    source = chapter / "正文.md"
    source.write_text(
        "\n".join(
            [
                f"![image]({png_ref})",
                f'<img src="{jpg_ref}">',
                "![remote](https://example.com/a.png)",
                "![missing](missing.png)",
                "![outside](%2e%2e/secret.png)",
                "![invalid](bad%FF.png)",
                "```markdown",
                f"![example]({png_ref})",
                "```",
            ]
        ),
        encoding="utf-8",
    )

    assert build_artifact_image_mappings(tmp_path) == {
        "章节/正文.md": {
            png_ref: png,
            jpg_ref: jpg,
        }
    }
    result = await MarkdownParser().parse(
        source, parse_output_store=LocalParseOutputStore(str(tmp_path / "output"))
    )
    assert result.artifact_ref is not None
    root = Path(result.artifact_ref.root) / result.artifact_ref.resource_rel
    mappings = json.loads((root / IMAGE_MAPPINGS_FILENAME).read_text(encoding="utf-8"))
    assert mappings == {"正文.md": {png_ref: png, jpg_ref: jpg}}
    for name in (png, jpg):
        assert (root / name).read_bytes() == (chapter / name).read_bytes()
    assert not (root / "secret.png").exists()
    rewritten, count = _rewrite_content(
        source.read_text(encoding="utf-8"),
        "viking://resources/doc",
        {png, jpg},
        mappings["正文.md"],
    )
    assert count == 2
    rendered = BeautifulSoup(MarkdownIt("commonmark").render(rewritten), "html.parser")
    images = rendered.find_all("img")
    assert len(images) == 6  # Two rewritten and four unchanged refs; the fenced example is text.
    for image, name in zip(images[:2], (png, jpg), strict=True):
        destination = urlsplit(str(image["src"]))
        assert destination.scheme == "viking" and destination.netloc == "resources"
        assert not destination.query and not destination.fragment
        assert unquote(destination.path) == f"/doc/{name}"
        assert (root / unquote(destination.path).removeprefix("/doc/")).read_bytes() == (
            chapter / name
        ).read_bytes()
    assert f"![example]({png_ref})" in rewritten


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
