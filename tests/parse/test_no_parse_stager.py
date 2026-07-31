# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for add_resource's no-parse staging mode."""

from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.mode import ParseMode, normalize_parse_mode
from openviking.parse.parsers.direct import DirectResourceStager
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError


class FakeVikingFS:
    """Store staged bytes by URI while exercising the real stager."""

    def __init__(self, fail_suffix: str = "") -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.deleted: list[str] = []
        self.fail_suffix = fail_suffix
        self._temp_counter = 0

    def create_temp_uri(self) -> str:
        self._temp_counter += 1
        return f"viking://temp/no_parse_{self._temp_counter}"

    async def mkdir(self, uri: str, exist_ok: bool = False) -> None:
        del exist_ok
        self.dirs.add(uri)

    async def write_file(self, uri: str, content: str | bytes) -> None:
        if self.fail_suffix and uri.endswith(self.fail_suffix):
            raise OSError("injected write failure")
        self.files[uri] = content.encode() if isinstance(content, str) else content

    async def delete_temp(self, uri: str) -> None:
        self.deleted.append(uri)
        prefix = uri.rstrip("/") + "/"
        self.files = {key: value for key, value in self.files.items() if not key.startswith(prefix)}
        self.dirs = {key for key in self.dirs if key != uri and not key.startswith(prefix)}


class NeverParsingRegistry:
    """Classifies files without exposing a callable content parser."""

    def get_parser_for_file(self, _path: Path) -> None:
        return None


class StubAccessorRegistry:
    def __init__(self, resource: LocalResource) -> None:
        self.resource = resource
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def access(self, source: str, **kwargs: Any) -> LocalResource:
        self.calls.append((source, kwargs))
        return self.resource


class FailingParserRouter:
    def should_use_understanding_directly(self, _source: str, **_kwargs: Any) -> bool:
        return False

    async def parse(self, _resource: LocalResource, **_kwargs: Any) -> Any:
        raise AssertionError("ParserRouter must not run in no_parse mode")


class RecordingParserRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[LocalResource, dict[str, Any]]] = []

    def should_use_understanding_directly(self, _source: str, **_kwargs: Any) -> bool:
        return False

    async def parse(self, resource: LocalResource, **kwargs: Any) -> Any:
        self.calls.append((resource, kwargs))
        return create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title="parsed"),
            source_path=str(resource.path),
            source_format="markdown",
            parser_name="RecordingParser",
        )


@pytest.fixture
def fake_fs() -> FakeVikingFS:
    return FakeVikingFS()


def local_resource(path: Path) -> LocalResource:
    return LocalResource(
        path=path,
        source_type=SourceType.LOCAL,
        original_source=str(path),
        is_temporary=False,
    )


def test_normalize_parse_mode_accepts_supported_values() -> None:
    assert normalize_parse_mode("default") is ParseMode.DEFAULT
    assert normalize_parse_mode("no_parse") is ParseMode.NO_PARSE
    assert normalize_parse_mode(ParseMode.NO_PARSE) is ParseMode.NO_PARSE


def test_normalize_parse_mode_rejects_unknown_value() -> None:
    with pytest.raises(InvalidArgumentError, match="default, no_parse"):
        normalize_parse_mode("split")


@pytest.mark.asyncio
async def test_single_file_keeps_original_name_and_bytes(
    tmp_path: Path,
    fake_fs: FakeVikingFS,
) -> None:
    source = tmp_path / "upload_123.md"
    source.write_bytes(b"# title\n\nraw bytes\xff")

    result = await DirectResourceStager(fake_fs).stage(
        local_resource(source),
        resource_name="guide",
        source_name="guide.md",
    )

    assert result.temp_dir_path is not None
    assert fake_fs.files[f"{result.temp_dir_path}/guide/guide.md"] == source.read_bytes()
    assert result.source_path == str(source)
    assert result.parser_name == "DirectResourceStager"
    assert result.meta["file_count"] == 1


@pytest.mark.asyncio
async def test_directory_copies_supported_and_unknown_files_at_relative_paths(
    tmp_path: Path,
    fake_fs: FakeVikingFS,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_bytes(b"# guide")
    (tmp_path / "config.yaml").write_bytes(b"enabled: true")
    (tmp_path / "blob.unknown").write_bytes(b"\x00\x01")

    result = await DirectResourceStager(
        fake_fs,
        registry=NeverParsingRegistry(),
    ).stage(local_resource(tmp_path), resource_name="source")

    assert result.temp_dir_path is not None
    root = f"{result.temp_dir_path}/source"
    assert fake_fs.files == {
        f"{root}/blob.unknown": b"\x00\x01",
        f"{root}/config.yaml": b"enabled: true",
        f"{root}/docs/guide.md": b"# guide",
    }
    assert result.warnings == []
    assert result.meta["file_count"] == 3
    assert result.meta["failed_files"] == []


@pytest.mark.asyncio
async def test_git_directory_keeps_repository_source_format(
    tmp_path: Path,
    fake_fs: FakeVikingFS,
) -> None:
    (tmp_path / "main.py").write_bytes(b"print('unchanged')\n")
    resource = LocalResource(
        path=tmp_path,
        source_type=SourceType.GIT,
        original_source="https://example.com/acme/project.git",
        is_temporary=False,
    )

    result = await DirectResourceStager(
        fake_fs,
        registry=NeverParsingRegistry(),
    ).stage(resource, resource_name="project")

    assert result.source_format == "repository"
    assert result.source_path == "https://example.com/acme/project.git"
    assert fake_fs.files[f"{result.temp_dir_path}/project/main.py"] == b"print('unchanged')\n"


@pytest.mark.asyncio
async def test_directory_reuses_selection_filters(
    tmp_path: Path,
    fake_fs: FakeVikingFS,
) -> None:
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "keep.md").write_bytes(b"ignored dir")
    (tmp_path / "keep.md").write_bytes(b"keep")
    (tmp_path / "skip.md").write_bytes(b"skip")
    (tmp_path / ".hidden.md").write_bytes(b"hidden")
    (tmp_path / "empty.md").write_bytes(b"")

    result = await DirectResourceStager(
        fake_fs,
        registry=NeverParsingRegistry(),
    ).stage(
        local_resource(tmp_path),
        resource_name="source",
        ignore_dirs="ignored",
        include="*.md",
        exclude="skip.*",
    )

    assert result.temp_dir_path is not None
    root = f"{result.temp_dir_path}/source"
    assert fake_fs.files == {f"{root}/keep.md": b"keep"}
    skipped = {entry["path"] for entry in result.meta["skipped_files"]}
    assert {"ignored", "skip.md", ".hidden.md", "empty.md"}.issubset(skipped)


@pytest.mark.asyncio
async def test_directory_transport_zip_is_unpacked(
    tmp_path: Path,
    fake_fs: FakeVikingFS,
) -> None:
    archive = tmp_path / "upload_123.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("nested/a.md", b"raw markdown")
        zip_file.writestr("config.yaml", b"enabled: true")

    result = await DirectResourceStager(
        fake_fs,
        registry=NeverParsingRegistry(),
    ).stage(
        local_resource(archive),
        resource_name="docs",
        source_name="docs",
    )

    assert result.temp_dir_path is not None
    root = f"{result.temp_dir_path}/docs"
    assert fake_fs.files == {
        f"{root}/config.yaml": b"enabled: true",
        f"{root}/nested/a.md": b"raw markdown",
    }


@pytest.mark.asyncio
async def test_zip_source_file_is_preserved_without_unpacking(
    tmp_path: Path,
    fake_fs: FakeVikingFS,
) -> None:
    archive = tmp_path / "upload_123.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("nested/a.md", b"raw markdown")

    result = await DirectResourceStager(fake_fs).stage(
        local_resource(archive),
        resource_name="archive",
        source_name="archive.zip",
    )

    assert result.temp_dir_path is not None
    assert fake_fs.files == {f"{result.temp_dir_path}/archive/archive.zip": archive.read_bytes()}


@pytest.mark.asyncio
async def test_strict_directory_copy_failure_cleans_temp_tree(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_bytes(b"ok")
    (tmp_path / "broken.bin").write_bytes(b"broken")
    fake_fs = FakeVikingFS(fail_suffix="broken.bin")

    with pytest.raises(InvalidArgumentError, match="broken.bin"):
        await DirectResourceStager(
            fake_fs,
            registry=NeverParsingRegistry(),
        ).stage(local_resource(tmp_path), resource_name="source", strict=True)

    assert fake_fs.deleted == ["viking://temp/no_parse_1"]
    assert fake_fs.files == {}


@pytest.mark.asyncio
async def test_unified_processor_routes_no_parse_after_accessor(
    tmp_path: Path,
    fake_fs: FakeVikingFS,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "upload_123.md"
    source.write_bytes(b"# unchanged")
    accessor = StubAccessorRegistry(local_resource(source))
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._accessor_registry = accessor
    processor._parser_router = FailingParserRouter()
    monkeypatch.setattr(
        "openviking.parse.parsers.direct.get_viking_fs",
        lambda: fake_fs,
    )

    result = await processor.process(
        str(source),
        parse_mode="no_parse",
        source_name="guide.md",
    )

    assert accessor.calls == [(str(source), {"source_name": "guide.md"})]
    assert result.parser_name == "DirectResourceStager"
    assert fake_fs.files[f"{result.temp_dir_path}/guide/guide.md"] == b"# unchanged"


@pytest.mark.asyncio
async def test_unified_processor_default_mode_keeps_parser_router(
    tmp_path: Path,
) -> None:
    source = tmp_path / "guide.md"
    source.write_bytes(b"# parsed")
    accessor = StubAccessorRegistry(local_resource(source))
    parser_router = RecordingParserRouter()
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._accessor_registry = accessor
    processor._parser_router = parser_router

    result = await processor.process(str(source))

    assert result.parser_name == "RecordingParser"
    assert len(parser_router.calls) == 1
    assert accessor.calls == [(str(source), {})]


@pytest.mark.asyncio
async def test_unified_processor_rejects_no_parse_for_raw_content() -> None:
    processor = UnifiedResourceProcessor(vlm_processor=object())

    with pytest.raises(InvalidArgumentError, match="file, directory, or remote resource"):
        await processor.process("raw\ncontent", parse_mode="no_parse")


@pytest.mark.asyncio
async def test_unified_processor_rejects_no_parse_flattening_before_access(
    tmp_path: Path,
) -> None:
    source = tmp_path / "guide.md"
    source.write_bytes(b"# guide")
    accessor = StubAccessorRegistry(local_resource(source))
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._accessor_registry = accessor

    with pytest.raises(InvalidArgumentError, match="preserve_structure"):
        await processor.process(
            str(source),
            parse_mode="no_parse",
            preserve_structure=False,
        )

    assert accessor.calls == []
