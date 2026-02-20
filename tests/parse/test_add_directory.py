# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for DirectoryParser.

Verifies that:
- DirectoryParser correctly scans directories and classifies files;
- Files WITH a parser are delegated via ``parser.parse()`` and their
  VikingFS temp output is merged into the main directory temp;
- Files WITHOUT a parser are written directly to VikingFS;
- Empty directories are handled gracefully;
- PDF files are converted via PDFParser;
- The directory structure is preserved;
- Errors during parsing are captured as warnings.
"""

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.base import (
    NodeType,
    ResourceNode,
    create_parse_result,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.directory import DirectoryParser

# ---------------------------------------------------------------------------
# Fake VikingFS – records mkdir / write / move / ls operations
# ---------------------------------------------------------------------------


class FakeVikingFS:
    """Minimal VikingFS mock that records calls and supports merge ops."""

    def __init__(self):
        self.dirs: List[str] = []
        self.files: Dict[str, bytes] = {}
        self._temp_counter = 0

    # ---- write operations ------------------------------------------------

    async def mkdir(self, uri: str, exist_ok: bool = False, **kw) -> None:
        if uri not in self.dirs:
            self.dirs.append(uri)

    async def write(self, uri: str, data: Any) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.files[uri] = data
        return uri

    async def write_file(self, uri: str, content: Any) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.files[uri] = content

    async def write_file_bytes(self, uri: str, content: bytes) -> None:
        self.files[uri] = content

    # ---- read / list operations ------------------------------------------

    async def read(self, uri: str, offset: int = 0, size: int = -1) -> bytes:
        return self.files.get(uri, b"")

    async def ls(self, uri: str) -> List[Dict[str, Any]]:
        """List direct children of *uri* (mirrors real AGFS entry format)."""
        prefix = uri.rstrip("/") + "/"
        children: Dict[str, bool] = {}  # name → is_dir
        for key in list(self.files.keys()) + self.dirs:
            if key.startswith(prefix):
                rest = key[len(prefix) :]
                if rest:
                    child_name = rest.split("/")[0]
                    is_deeper = "/" in rest[len(child_name) :]
                    child_full = f"{prefix}{child_name}"
                    is_dir = children.get(child_name, False) or is_deeper or child_full in self.dirs
                    children[child_name] = is_dir
        result = []
        for name in sorted(children):
            child_uri = f"{uri.rstrip('/')}/{name}"
            is_dir = children[name]
            result.append(
                {
                    "name": name,
                    "uri": child_uri,
                    # Match real AGFS format: "isDir" boolean field
                    "isDir": is_dir,
                    "type": "directory" if is_dir else "file",
                }
            )
        return result

    # ---- move / delete operations ----------------------------------------

    async def move_file(self, from_uri: str, to_uri: str) -> None:
        if from_uri in self.files:
            self.files[to_uri] = self.files.pop(from_uri)

    async def delete_temp(self, temp_uri: str) -> None:
        prefix = temp_uri.rstrip("/") + "/"
        to_del = [k for k in self.files if k == temp_uri or k.startswith(prefix)]
        for k in to_del:
            del self.files[k]
        self.dirs = [d for d in self.dirs if d != temp_uri and not d.startswith(prefix)]

    # ---- temp URI --------------------------------------------------------

    def create_temp_uri(self) -> str:
        self._temp_counter += 1
        return f"viking://temp/dir_{self._temp_counter}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_fs():
    return FakeVikingFS()


@pytest.fixture
def parser(fake_fs):
    """DirectoryParser with VikingFS patched for ALL BaseParser instances."""
    with patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs):
        yield DirectoryParser()


# ---- directory fixtures --------------------------------------------------


@pytest.fixture
def tmp_code(tmp_path: Path) -> Path:
    """Flat directory with code files (no dedicated parser)."""
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "util.py").write_text("def add(a, b): return a + b", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('hi')", encoding="utf-8")
    return tmp_path


@pytest.fixture
def tmp_nested_code(tmp_path: Path) -> Path:
    """Nested directory with code files only (no dedicated parser)::

    tmp_path/
        a/
            b/
                c.py
                d.py
            x.py
        top.py
    """
    ab = tmp_path / "a" / "b"
    ab.mkdir(parents=True)
    (ab / "c.py").write_text("# c", encoding="utf-8")
    (ab / "d.py").write_text("# d", encoding="utf-8")
    (tmp_path / "a" / "x.py").write_text("# x", encoding="utf-8")
    (tmp_path / "top.py").write_text("# top", encoding="utf-8")
    return tmp_path


@pytest.fixture
def tmp_empty(tmp_path: Path) -> Path:
    """Directory with no processable files."""
    (tmp_path / ".hidden").write_text("hidden", encoding="utf-8")
    (tmp_path / "empty.txt").write_bytes(b"")
    return tmp_path


@pytest.fixture
def tmp_mixed(tmp_path: Path) -> Path:
    """Directory with processable and unsupported files."""
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "data.xyz").write_text("unknown", encoding="utf-8")
    (tmp_path / "archive.rar").write_bytes(b"RAR\x00")
    return tmp_path


@pytest.fixture
def tmp_media_files(tmp_path: Path) -> Path:
    """Directory with various media files and regular files."""
    (tmp_path / "docs.md").write_text("# Documentation", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "audio.mp3").write_bytes(b"ID3")
    (tmp_path / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftyp")
    (tmp_path / "script.js").write_text("console.log('test')", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: basic properties
# ---------------------------------------------------------------------------


class TestDirectoryParserBasic:
    """Basic DirectoryParser properties."""

    def test_supported_extensions_empty(self):
        p = DirectoryParser()
        assert p.supported_extensions == []

    def test_can_parse_directory(self, tmp_path: Path):
        p = DirectoryParser()
        assert p.can_parse(tmp_path) is True

    def test_can_parse_file(self, tmp_path: Path):
        f = tmp_path / "test.md"
        f.write_text("hello")
        p = DirectoryParser()
        assert p.can_parse(f) is False

    @pytest.mark.asyncio
    async def test_parse_content_not_implemented(self):
        p = DirectoryParser()
        with pytest.raises(NotImplementedError):
            await p.parse_content("some content")

    @pytest.mark.asyncio
    async def test_not_a_directory_raises(self, tmp_path: Path, parser):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        with pytest.raises(NotADirectoryError):
            await parser.parse(str(f))


# ---------------------------------------------------------------------------
# Tests: empty directory
# ---------------------------------------------------------------------------


class TestEmptyDirectory:
    """Empty directories should be handled gracefully."""

    @pytest.mark.asyncio
    async def test_empty_dir_returns_zero_files(self, tmp_empty: Path, parser, fake_fs) -> None:
        result = await parser.parse(str(tmp_empty))

        assert result.parser_name == "DirectoryParser"
        assert result.source_format == "directory"
        assert result.temp_dir_path is not None
        assert result.meta.get("file_count", 0) == 0 or len(fake_fs.files) == 0


# ---------------------------------------------------------------------------
# Tests: files without a parser (direct write)
# ---------------------------------------------------------------------------


class TestDirectWriteFiles:
    """Code files with no dedicated parser should be written directly."""

    @pytest.mark.asyncio
    async def test_all_files_uploaded(self, tmp_code: Path, parser, fake_fs) -> None:
        result = await parser.parse(str(tmp_code))

        assert result.parser_name == "DirectoryParser"
        assert result.temp_dir_path is not None

        uploaded_names = {uri.split("/")[-1] for uri in fake_fs.files}
        assert "main.py" in uploaded_names
        assert "util.py" in uploaded_names
        assert "app.js" in uploaded_names

    @pytest.mark.asyncio
    async def test_dir_name_in_uri(self, tmp_code: Path, parser, fake_fs) -> None:
        await parser.parse(str(tmp_code))

        dir_name = tmp_code.name
        for uri in fake_fs.files:
            assert f"/{dir_name}/" in uri

    @pytest.mark.asyncio
    async def test_content_preserved(self, tmp_path: Path, parser, fake_fs) -> None:
        (tmp_path / "hello.py").write_text("print('world')", encoding="utf-8")
        await parser.parse(str(tmp_path))

        for uri, content in fake_fs.files.items():
            if uri.endswith("hello.py"):
                assert content == b"print('world')"
                break
        else:
            pytest.fail("hello.py not found in uploaded files")


# ---------------------------------------------------------------------------
# Tests: nested directory structure
# ---------------------------------------------------------------------------


class TestNestedDirectory:
    """Nested directory structure should be preserved."""

    @pytest.mark.asyncio
    async def test_structure_preserved(self, tmp_nested_code: Path, parser, fake_fs) -> None:
        await parser.parse(str(tmp_nested_code))

        dir_name = tmp_nested_code.name
        rel_paths = set()
        for uri in fake_fs.files:
            idx = uri.find(f"/{dir_name}/")
            if idx >= 0:
                rel = uri[idx + len(f"/{dir_name}/") :]
                rel_paths.add(rel)

        assert "top.py" in rel_paths
        assert "a/x.py" in rel_paths
        assert "a/b/c.py" in rel_paths
        assert "a/b/d.py" in rel_paths

    @pytest.mark.asyncio
    async def test_file_count(self, tmp_nested_code: Path, parser, fake_fs) -> None:
        await parser.parse(str(tmp_nested_code))
        assert len(fake_fs.files) == 4


# ---------------------------------------------------------------------------
# Tests: unsupported files handled
# ---------------------------------------------------------------------------


class TestMixedDirectory:
    """Unsupported files should be skipped with warnings (non-strict)."""

    @pytest.mark.asyncio
    async def test_only_processable_uploaded(self, tmp_mixed: Path, parser, fake_fs) -> None:
        await parser.parse(str(tmp_mixed))

        uploaded_names = {uri.split("/")[-1] for uri in fake_fs.files}
        assert "main.py" in uploaded_names
        assert "data.xyz" not in uploaded_names
        assert "archive.rar" not in uploaded_names

    @pytest.mark.asyncio
    async def test_warnings_for_unsupported(self, tmp_mixed: Path, parser, fake_fs) -> None:
        result = await parser.parse(str(tmp_mixed))
        assert len(result.warnings) > 0


# ---------------------------------------------------------------------------
# Tests: files with a parser (parser.parse() path)
# ---------------------------------------------------------------------------


class TestParserDelegation:
    """Files with a dedicated parser should be processed via parser.parse()."""

    @pytest.mark.asyncio
    async def test_md_file_goes_through_parser(self, tmp_path: Path, parser, fake_fs) -> None:
        """Markdown files should be processed by MarkdownParser.parse()."""
        (tmp_path / "readme.md").write_text("# Hello\nworld", encoding="utf-8")

        result = await parser.parse(str(tmp_path))

        # MarkdownParser creates a temp dir and stores processed content.
        # After merging, the content should appear under our temp.
        assert result.meta["file_count"] == 1
        assert len(fake_fs.files) > 0

    @pytest.mark.asyncio
    async def test_txt_file_goes_through_parser(self, tmp_path: Path, parser, fake_fs) -> None:
        """Text files should be processed by TextParser (delegates to Markdown)."""
        (tmp_path / "notes.txt").write_text("some notes here", encoding="utf-8")

        result = await parser.parse(str(tmp_path))

        assert result.meta["file_count"] == 1
        assert len(fake_fs.files) > 0


# ---------------------------------------------------------------------------
# Tests: PDF conversion via parser.parse()
# ---------------------------------------------------------------------------


class TestPDFConversion:
    """PDF files should be processed via PDFParser.parse()."""

    @pytest.mark.asyncio
    async def test_pdf_processed_by_parser(self, tmp_path: Path, parser, fake_fs) -> None:
        pdf_file = tmp_path / "document.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake pdf")

        # Mock PDFParser.parse to return a ParseResult with fake content
        # in VikingFS (simulating conversion).
        mock_temp = fake_fs.create_temp_uri()  # e.g. viking://temp/dir_2
        doc_dir = f"{mock_temp}/document"
        await fake_fs.mkdir(mock_temp)
        await fake_fs.mkdir(doc_dir)
        await fake_fs.write_file(f"{doc_dir}/document.md", "# Converted PDF")

        fake_result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_path=str(pdf_file),
            source_format="pdf",
            parser_name="PDFParser",
            parse_time=0.1,
        )
        fake_result.temp_dir_path = mock_temp

        with patch(
            "openviking.parse.parsers.directory.DirectoryParser._assign_parser",
        ) as mock_assign:
            from openviking.parse.parsers.pdf import PDFParser as _PDF

            mock_pdf = AsyncMock(spec=_PDF)
            mock_pdf.parse = AsyncMock(return_value=fake_result)

            def assign_side_effect(cf, registry):
                if cf.path.suffix == ".pdf":
                    return mock_pdf
                return registry.get_parser_for_file(cf.path)

            mock_assign.side_effect = assign_side_effect

            await parser.parse(str(tmp_path))

        # The converted .md should be under our directory temp
        dir_name = tmp_path.name
        found_md = any(
            uri.endswith("document.md") and f"/{dir_name}/" in uri for uri in fake_fs.files
        )
        assert found_md, f"document.md not found. Files: {list(fake_fs.files.keys())}"

    @pytest.mark.asyncio
    async def test_pdf_parse_failure_adds_warning(self, tmp_path: Path, parser, fake_fs) -> None:
        pdf_file = tmp_path / "bad.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 broken")

        with patch(
            "openviking.parse.parsers.directory.DirectoryParser._assign_parser",
        ) as mock_assign:
            from openviking.parse.parsers.pdf import PDFParser as _PDF

            mock_pdf = AsyncMock(spec=_PDF)
            mock_pdf.parse = AsyncMock(side_effect=RuntimeError("conversion failed"))

            def assign_side_effect(cf, registry):
                if cf.path.suffix == ".pdf":
                    return mock_pdf
                return registry.get_parser_for_file(cf.path)

            mock_assign.side_effect = assign_side_effect

            result = await parser.parse(str(tmp_path))

        # Should have a warning, not a crash
        assert any("bad.pdf" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Tests: ParseResult metadata
# ---------------------------------------------------------------------------


class TestParseResultMetadata:
    """ParseResult should contain correct metadata."""

    @pytest.mark.asyncio
    async def test_result_fields(self, tmp_code: Path, parser, fake_fs) -> None:
        result = await parser.parse(str(tmp_code))

        assert result.parser_name == "DirectoryParser"
        assert result.source_format == "directory"
        assert result.source_path == str(tmp_code.resolve())
        assert result.temp_dir_path is not None
        assert result.parse_time is not None
        assert result.parse_time > 0
        assert result.meta["dir_name"] == tmp_code.name
        assert result.meta["total_processable"] == 3
        assert result.meta["file_count"] == 3


# ---------------------------------------------------------------------------
# Tests: directly_upload_media parameter
# ---------------------------------------------------------------------------


class TestDirectlyUploadMedia:
    """Test the directly_upload_media parameter behavior."""

    @pytest.mark.asyncio
    async def test_default_directly_upload_media_true(self, tmp_media_files: Path, fake_fs) -> None:
        """Test that with directly_upload_media=True (default), media files are uploaded directly."""
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs):
            parser = DirectoryParser()
            await parser.parse(str(tmp_media_files))

        uploaded_names = {uri.split("/")[-1] for uri in fake_fs.files}

        assert "docs.md" in uploaded_names
        assert "image.png" in uploaded_names
        assert "photo.jpg" in uploaded_names
        assert "audio.mp3" in uploaded_names
        assert "video.mp4" in uploaded_names
        assert "script.js" in uploaded_names

    @pytest.mark.asyncio
    async def test_directly_upload_media_false(self, tmp_media_files: Path, fake_fs) -> None:
        """Test that with directly_upload_media=False, media files go through their parsers."""
        mock_image_result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_path=str(tmp_media_files / "image.png"),
            source_format="image",
            parser_name="ImageParser",
            parse_time=0.1,
        )
        mock_image_result.temp_dir_path = fake_fs.create_temp_uri()

        mock_audio_result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_path=str(tmp_media_files / "audio.mp3"),
            source_format="audio",
            parser_name="AudioParser",
            parse_time=0.1,
        )
        mock_audio_result.temp_dir_path = fake_fs.create_temp_uri()

        mock_video_result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_path=str(tmp_media_files / "video.mp4"),
            source_format="video",
            parser_name="VideoParser",
            parse_time=0.1,
        )
        mock_video_result.temp_dir_path = fake_fs.create_temp_uri()

        with patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs):
            parser = DirectoryParser()

            with patch.object(parser, "_assign_parser") as mock_assign:
                from openviking.parse.parsers.media.audio import AudioParser
                from openviking.parse.parsers.media.image import ImageParser
                from openviking.parse.parsers.media.video import VideoParser

                mock_image = AsyncMock(spec=ImageParser)
                mock_image.parse = AsyncMock(return_value=mock_image_result)

                mock_audio = AsyncMock(spec=AudioParser)
                mock_audio.parse = AsyncMock(return_value=mock_audio_result)

                mock_video = AsyncMock(spec=VideoParser)
                mock_video.parse = AsyncMock(return_value=mock_video_result)

                def assign_side_effect(cf, registry):
                    if cf.path.suffix in {".png", ".jpg"}:
                        return mock_image
                    elif cf.path.suffix in {".mp3"}:
                        return mock_audio
                    elif cf.path.suffix in {".mp4"}:
                        return mock_video
                    return registry.get_parser_for_file(cf.path)

                mock_assign.side_effect = assign_side_effect

                await parser.parse(str(tmp_media_files), directly_upload_media=False)

        assert mock_image.parse.call_count == 2
        mock_audio.parse.assert_called_once()
        mock_video.parse.assert_called_once()
