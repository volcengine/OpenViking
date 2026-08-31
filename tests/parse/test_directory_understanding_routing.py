import asyncio
import zipfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.accessors.web_crawler.models import CrawledPage, CrawlResult
from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parser_router import ParserRouter
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.directory import DirectoryParser
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.pdf import PDFParser
from openviking.parse.understanding_api import UnderstandingAPI, UnderstandingAPIError
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking.utils.resource_processor import ResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError


class _FakeVikingFS:
    def __init__(self) -> None:
        self._temp_counter = 0
        self.files: dict[str, bytes] = {}

    def create_temp_uri(self) -> str:
        self._temp_counter += 1
        return f"viking://temp/directory_route_{self._temp_counter}"

    async def mkdir(self, _uri: str, exist_ok: bool = False) -> None:
        _ = exist_ok

    async def write(self, uri: str, data) -> str:
        self.files[uri] = data.encode("utf-8") if isinstance(data, str) else data
        return uri

    async def write_file(self, uri: str, content) -> None:
        self.files[uri] = content.encode("utf-8") if isinstance(content, str) else content

    async def write_file_bytes(self, uri: str, content: bytes) -> None:
        self.files[uri] = content


def _configure_understanding(
    monkeypatch,
    extensions: list[str],
    *,
    enabled: bool = True,
    max_concurrent: int = 4,
    max_files: int = 1000,
    max_depth: int = 10,
    upload_simple_max_bytes: int = 512 * 1024 * 1024,
    enable_resumable_upload: bool = False,
) -> None:
    config = SimpleNamespace(
        parser_api=SimpleNamespace(
            enable=enabled,
            host="https://parser.example.com",
            api_key="test-key",
            enable_feishu_url=False,
            extensions=extensions,
            response_timeout_seconds=1800,
            http_timeout_seconds=10.0,
            upload_simple_max_bytes=upload_simple_max_bytes,
            upload_part_size_bytes=8 * 1024 * 1024,
            enable_resumable_upload=enable_resumable_upload,
        ),
        directory=SimpleNamespace(
            preserve_structure=True,
            max_files=max_files,
            max_depth=max_depth,
            max_concurrent=max_concurrent,
        ),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: config,
    )


def _fake_understanding_parse(calls: list[Path]):
    async def parse(_self, source, **_kwargs):
        source_path = Path(source)
        calls.append(source_path)
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=source_path.stem),
            source_path=str(source_path),
            source_format=source_path.suffix.lstrip("."),
            parser_name="UnderstandingAPI",
        )
        result.temp_dir_path = f"viking://temp/understanding_{len(calls)}"
        return result

    return parse


@pytest.mark.asyncio
async def test_materialized_html_webpage_routes_to_understanding(monkeypatch, tmp_path: Path):
    _configure_understanding(monkeypatch, ["html"])
    site_dir = tmp_path / "example.com"
    nested_dir = site_dir / "articles"
    nested_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text("<html><h1>Home</h1></html>", encoding="utf-8")
    (nested_dir / "detail.html").write_text(
        "<html><h1>Detail</h1></html>",
        encoding="utf-8",
    )
    resource = LocalResource(
        path=site_dir,
        source_type=SourceType.HTTP,
        original_source="https://example.com/",
        meta={"web_import": True, "original_filename": "example.com"},
        is_temporary=False,
    )
    calls: list[Path] = []
    fake_fs = _FakeVikingFS()

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(UnderstandingAPI, "parse", new=_fake_understanding_parse(calls)),
        patch.object(DirectoryParser, "_merge_temp", new=AsyncMock(return_value=True)),
    ):
        result = await UnifiedResourceProcessor(vlm_processor=object()).process(
            "https://example.com/",
            prepared_resource=resource,
            strict=True,
        )

    assert {path.relative_to(site_dir).as_posix() for path in calls} == {
        "articles/detail.html",
        "index.html",
    }
    assert result.meta["file_count"] == 2
    assert {item["parser"] for item in result.meta["processed_files"]} == {"UnderstandingAPI"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,depth,enabled,extensions,expected_parser",
    [
        pytest.param(
            "https://example.com/article", 0, True, ["html"], "UnderstandingAPI", id="extensionless"
        ),
        pytest.param(
            "https://example.com/page.html", 0, True, ["html"], "UnderstandingAPI", id="html"
        ),
        pytest.param(
            "https://example.com/page.htm?view=full#top",
            0,
            True,
            ["html"],
            "UnderstandingAPI",
            id="htm-query",
        ),
        pytest.param(
            "https://example.com/", 1, True, ["html"], "UnderstandingAPI", id="multiple-pages"
        ),
        pytest.param(
            "https://example.com/page.html", 0, False, ["html"], "HTMLParser", id="disabled"
        ),
        pytest.param(
            "https://example.com/article",
            0,
            True,
            ["pdf"],
            "HTMLParser",
            id="extension-not-enabled",
        ),
    ],
)
async def test_html_url_routes_materialized_pages(
    monkeypatch, tmp_path: Path, url, depth, enabled, extensions, expected_parser
):
    _configure_understanding(monkeypatch, extensions, enabled=enabled)
    pages = [CrawledPage(url=url, html="<html><h1>Home</h1><p>Home content</p></html>")]
    if depth:
        pages.append(
            CrawledPage(
                url="https://example.com/articles/detail",
                depth=1,
                html="<html><h1>Detail</h1><p>Detail content</p></html>",
            )
        )
    crawler = MagicMock()
    crawler.return_value.crawl = AsyncMock(
        return_value=CrawlResult(pages=pages, total_crawled=len(pages))
    )
    monkeypatch.setattr("openviking.parse.accessors.web_importer.ScrapyWebCrawler", crawler)
    monkeypatch.setattr(
        "openviking.parse.accessors.web_importer.tempfile.mkdtemp",
        lambda **_kwargs: str(tmp_path / "web"),
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=pages[0].html
        )
    )
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs)
    )
    parsed_files = {}

    async def parse_html(parser, source, **_kwargs):
        path = Path(source)
        parsed_files[path.name] = (type(parser).__name__, path.read_text(encoding="utf-8"))
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=path.stem),
            parser_name=type(parser).__name__,
        )
        result.temp_dir_path = f"viking://temp/{path.stem}"
        return result

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(UnderstandingAPI, "parse", new=parse_html),
        patch.object(HTMLParser, "parse", new=parse_html),
        patch.object(DirectoryParser, "_merge_temp", new=AsyncMock(return_value=True)),
    ):
        result = await UnifiedResourceProcessor(vlm_processor=object()).process(
            url, depth=depth, strict=True
        )

    expected = {"Home.html": (expected_parser, pages[0].html)}
    if depth:
        expected["Detail.html"] = (expected_parser, pages[1].html)
    assert parsed_files == expected
    assert result.meta["failed_files"] == []
    assert result.meta["file_count"] == len(pages)
    assert {item["parser"] for item in result.meta["processed_files"]} == {expected_parser}
    crawler.return_value.crawl.assert_awaited_once_with(url)
    assert crawler.call_args.args[0].depth == depth


@pytest.mark.asyncio
async def test_disabled_understanding_uses_native_directory_parser(monkeypatch, tmp_path: Path):
    _configure_understanding(monkeypatch, ["pdf"], enabled=False)
    pdf_path = tmp_path / "native.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")
    native_parse = AsyncMock(return_value={"ok": True, "meta": {}, "error": None})
    understanding_parse = AsyncMock()

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(DirectoryParser, "_process_single_file", new=native_parse),
        patch.object(ParserRouter, "parse", new=understanding_parse),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    understanding_parse.assert_not_awaited()
    native_parse.assert_awaited_once()
    assert type(native_parse.await_args.args[1]).__name__ == "PDFParser"
    assert result.meta["processed_files"][0]["parser"] == "PDFParser"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "extensions"),
    [(False, ["pdf"]), (True, [])],
)
async def test_directory_limits_require_understanding_routing(
    monkeypatch,
    tmp_path: Path,
    enabled: bool,
    extensions: list[str],
):
    _configure_understanding(
        monkeypatch,
        extensions,
        enabled=enabled,
        max_files=1,
        max_depth=1,
    )
    nested = tmp_path / "level-1" / "level-2"
    nested.mkdir(parents=True)
    (tmp_path / "first.pdf").write_bytes(b"%PDF-1.7")
    (nested / "second.pdf").write_bytes(b"%PDF-1.7")
    native_parse = AsyncMock(return_value={"ok": True, "meta": {}, "error": None})

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(DirectoryParser, "_process_single_file", new=native_parse),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert native_parse.await_count == 2
    assert result.meta["file_count"] == 2
    assert result.meta["failed_files"] == []


@pytest.mark.asyncio
async def test_directory_recursively_routes_understanding_files(
    monkeypatch,
    tmp_path: Path,
):
    _configure_understanding(
        monkeypatch,
        ["pdf", "external", "png"],
        upload_simple_max_bytes=16,
    )
    nested_dir = tmp_path / "level-1" / "level-2"
    nested_dir.mkdir(parents=True)
    (nested_dir / "paper.pdf").write_bytes(b"%PDF-1.7")
    (nested_dir / "custom.external").write_bytes(b"external-only")
    (nested_dir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    calls: list[Path] = []
    fake_fs = _FakeVikingFS()
    direct_upload = AsyncMock(return_value=True)

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(ParserRouter, "parse", new=_fake_understanding_parse(calls)),
        patch.object(DirectoryParser, "_merge_temp", new=AsyncMock(return_value=True)),
        patch.object(DirectoryParser, "_upload_file_directly", new=direct_upload),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert {path.relative_to(tmp_path).as_posix() for path in calls} == {
        "level-1/level-2/custom.external",
        "level-1/level-2/image.png",
        "level-1/level-2/paper.pdf",
    }
    direct_upload.assert_not_awaited()
    assert result.meta["file_count"] == 3
    assert result.meta["unsupported_files"] == []
    assert {item["parser"] for item in result.meta["processed_files"]} == {"UnderstandingAPI"}


@pytest.mark.asyncio
async def test_directory_does_not_preflight_understanding_file_size(
    monkeypatch,
    tmp_path: Path,
):
    _configure_understanding(
        monkeypatch,
        ["pdf"],
        upload_simple_max_bytes=8,
        enable_resumable_upload=False,
    )
    large_pdf = tmp_path / "large.pdf"
    small_pdf = tmp_path / "small.pdf"
    large_pdf.write_bytes(b"%PDF-1.7+")
    small_pdf.write_bytes(b"%PDF-1.7")
    calls: list[Path] = []

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(ParserRouter, "parse", new=_fake_understanding_parse(calls)),
        patch.object(DirectoryParser, "_merge_temp", new=AsyncMock(return_value=True)),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert calls == [large_pdf, small_pdf]
    assert result.meta["file_count"] == 2
    assert result.meta["skipped_files"] == []


@pytest.mark.asyncio
async def test_native_parser_failures_include_each_file_error(
    monkeypatch,
    tmp_path: Path,
):
    _configure_understanding(monkeypatch, [], enabled=False)
    for name in ("first.pdf", "second.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.7")

    class _RejectingParser:
        async def parse(self, source, **_kwargs):
            raise ValueError(f"parser rejected {Path(source).name}")

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(DirectoryParser, "_assign_parser", return_value=_RejectingParser()),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert result.meta["file_count"] == 0
    assert result.meta["failed_files"] == [
        {
            "path": "first.pdf",
            "parser": "_RejectingParser",
            "error": "parser rejected first.pdf",
        },
        {
            "path": "second.pdf",
            "parser": "_RejectingParser",
            "error": "parser rejected second.pdf",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("use_understanding", [False, True])
@pytest.mark.parametrize("keep_good_file", [False, True])
@pytest.mark.parametrize(
    "output", ["missing", "empty-root", "empty", "hidden", "sidecar", "content", "media"]
)
async def test_directory_counts_only_parser_results_with_content(
    monkeypatch, tmp_path: Path, use_understanding: bool, keep_good_file: bool, output: str
):
    _configure_understanding(monkeypatch, ["pdf"], enabled=use_understanding)
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.7")
    if keep_good_file:
        (tmp_path / "keep.py").write_text("print('keep')", encoding="utf-8")

    sub_result = create_parse_result(
        root=ResourceNode(type=NodeType.ROOT),
        warnings=["parser warning"],
        meta={"file_id": "file-1", "response_id": "response-1"},
    )
    temp_uri = "viking://temp/parsed-file"
    wrapper_uri = f"{temp_uri}/paper"
    if output != "missing":
        sub_result.temp_dir_path = temp_uri
    payload_name = {
        "hidden": ".ignored",
        "sidecar": ".image_mappings.json",
        "content": "paper.md",
        "media": "image.png",
    }.get(output)
    listings = {
        temp_uri: [] if output == "empty-root" else [{"name": "paper", "isDir": True}],
        wrapper_uri: [{"name": payload_name, "isDir": False}] if payload_name else [],
    }
    fake_fs = _FakeVikingFS()
    fake_fs.ls = AsyncMock(side_effect=lambda uri, **_kwargs: listings[uri])
    fake_fs.mkdir = AsyncMock()
    fake_fs.move_file = AsyncMock()
    fake_fs.delete_temp = AsyncMock()
    native_parser = SimpleNamespace(parse=AsyncMock(return_value=sub_result))
    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(ParserRouter, "parse", new=AsyncMock(return_value=sub_result)),
        patch.object(
            DirectoryParser,
            "_assign_parser",
            side_effect=lambda cf, _registry: native_parser if cf.path.suffix == ".pdf" else None,
        ),
    ):
        result = await DirectoryParser().parse(str(tmp_path))

    has_content = output in {"content", "media"}
    assert result.meta["file_count"] == int(keep_good_file) + int(has_content)
    assert {item["path"] for item in result.meta["processed_files"]} == (
        ({"keep.py"} if keep_good_file else set()) | ({"paper.pdf"} if has_content else set())
    )
    if has_content:
        assert result.meta["failed_files"] == []
        fake_fs.move_file.assert_awaited_once()
    else:
        failure = result.meta["failed_files"][0]
        assert failure["path"] == "paper.pdf"
        assert "no content generated" in failure["error"]
        assert "parser warning" in failure["error"]
        if use_understanding:
            assert failure["file_id"] == "file-1"
            assert failure["response_id"] == "response-1"
        fake_fs.move_file.assert_not_awaited()
        assert not any(call.args[0].endswith("/paper") for call in fake_fs.mkdir.await_args_list)
    if output != "missing":
        fake_fs.delete_temp.assert_awaited_once_with(temp_uri)
    else:
        fake_fs.delete_temp.assert_not_awaited()


@pytest.mark.asyncio
async def test_directory_reports_empty_parser_result_without_warnings(monkeypatch, tmp_path: Path):
    _configure_understanding(monkeypatch, ["pdf"], enabled=False)
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.7")
    parser = SimpleNamespace(
        parse=AsyncMock(return_value=create_parse_result(root=ResourceNode(type=NodeType.ROOT)))
    )
    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(DirectoryParser, "_assign_parser", return_value=parser),
    ):
        result = await DirectoryParser().parse(str(tmp_path))

    assert result.meta["file_count"] == 0
    assert result.meta["failed_files"][0]["error"] == "Parse failed: no content generated"


@pytest.mark.asyncio
async def test_directory_preserves_native_pdf_soft_failure(monkeypatch, tmp_path: Path):
    _configure_understanding(monkeypatch, ["pdf"], enabled=False)
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.7")
    fake_fs = _FakeVikingFS()
    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(
            PDFParser, "_convert_to_markdown", new=AsyncMock(side_effect=ValueError("invalid PDF"))
        ),
    ):
        result = await DirectoryParser().parse(str(tmp_path))

    assert result.meta["file_count"] == 0
    assert result.meta["processed_files"] == []
    assert result.meta["failed_files"] == [
        {
            "path": "broken.pdf",
            "parser": "PDFParser",
            "error": "Parse failed: no content generated; Failed to parse PDF: invalid PDF",
        }
    ]

    fake_fs.bind_request_context = lambda _ctx: nullcontext()
    fake_fs.delete_temp = AsyncMock()
    fake_fs.persist_temp_tree = AsyncMock()
    processor = ResourceProcessor(
        vikingdb=SimpleNamespace(get_embedder=lambda: None), media_storage=None
    )
    processor._get_media_processor = lambda: SimpleNamespace(process=AsyncMock(return_value=result))
    processor.tree_builder.finalize_from_temp = AsyncMock()
    ctx = object()
    with (
        patch("openviking.utils.resource_processor.get_viking_fs", return_value=fake_fs),
        patch(
            "openviking.utils.resource_processor.get_current_telemetry", return_value=MagicMock()
        ),
    ):
        processed = await processor.process_resource(path=str(tmp_path), ctx=ctx)

    assert processed["status"] == "error"
    assert "all 1 processable file(s) failed" in processed["errors"][0]
    assert "broken.pdf" in processed["errors"][0]
    assert "invalid PDF" in processed["errors"][0]
    fake_fs.delete_temp.assert_awaited_once_with(result.temp_dir_path, ctx=ctx)
    fake_fs.persist_temp_tree.assert_not_awaited()
    processor.tree_builder.finalize_from_temp.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_upload_failure_includes_file_error(
    monkeypatch,
    tmp_path: Path,
):
    _configure_understanding(monkeypatch, [], enabled=False)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"01234567")
    fake_fs = _FakeVikingFS()
    fake_fs.write_file = AsyncMock(side_effect=OSError("storage rejected video.mp4"))

    with patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert result.meta["file_count"] == 0
    assert result.meta["failed_files"] == [
        {
            "path": "video.mp4",
            "parser": "direct_upload",
            "error": "storage rejected video.mp4",
        }
    ]


@pytest.mark.asyncio
async def test_understanding_directory_jobs_use_bounded_concurrency(monkeypatch, tmp_path: Path):
    _configure_understanding(monkeypatch, ["pdf"], max_concurrent=2)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.7")

    inflight = 0
    max_seen = 0

    async def parse(_self, source, **_kwargs):
        nonlocal inflight, max_seen
        inflight += 1
        max_seen = max(max_seen, inflight)
        await asyncio.sleep(0.01)
        inflight -= 1
        source_path = Path(source)
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=source_path.stem),
            source_path=str(source_path),
            source_format="pdf",
            parser_name="UnderstandingAPI",
        )
        result.temp_dir_path = f"viking://temp/{source_path.stem}"
        return result

    fake_fs = _FakeVikingFS()
    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(ParserRouter, "parse", new=parse),
        patch.object(DirectoryParser, "_merge_temp", new=AsyncMock(return_value=True)),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert max_seen == 2
    assert [item["path"] for item in result.meta["processed_files"]] == [
        "a.pdf",
        "b.pdf",
        "c.pdf",
    ]


@pytest.mark.asyncio
async def test_understanding_directory_jobs_share_limit_across_directories(
    monkeypatch,
    tmp_path: Path,
):
    _configure_understanding(monkeypatch, ["pdf"], max_concurrent=4)
    directories = [tmp_path / "first", tmp_path / "second"]
    for directory in directories:
        directory.mkdir()
        for index in range(4):
            (directory / f"{index}.pdf").write_bytes(b"%PDF-1.7")

    inflight = 0
    max_seen = 0
    limit_reached = asyncio.Event()
    release = asyncio.Event()

    async def parse(_self, source, **_kwargs):
        nonlocal inflight, max_seen
        inflight += 1
        max_seen = max(max_seen, inflight)
        if inflight == 4:
            limit_reached.set()
        try:
            await release.wait()
        finally:
            inflight -= 1

        source_path = Path(source)
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=source_path.stem),
            source_path=str(source_path),
            source_format="pdf",
            parser_name="UnderstandingAPI",
        )
        result.temp_dir_path = f"viking://temp/{source_path.parent.name}_{source_path.stem}"
        return result

    fake_fs = _FakeVikingFS()
    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(ParserRouter, "parse", new=parse),
        patch.object(DirectoryParser, "_merge_temp", new=AsyncMock(return_value=True)),
    ):
        tasks = [
            asyncio.create_task(DirectoryParser().parse(str(directory), strict=True))
            for directory in directories
        ]
        try:
            await asyncio.wait_for(limit_reached.wait(), timeout=1)
            await asyncio.sleep(0.01)
            assert max_seen == 4
        finally:
            release.set()
            results = await asyncio.gather(*tasks)

    assert sum(result.meta["file_count"] for result in results) == 8


@pytest.mark.asyncio
async def test_understanding_results_merge_in_source_order(monkeypatch, tmp_path: Path):
    _configure_understanding(monkeypatch, ["pdf"], max_concurrent=3)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.7")

    delays = {"a.pdf": 0.03, "b.pdf": 0.02, "c.pdf": 0.01}

    async def parse(_self, source, **_kwargs):
        source_path = Path(source)
        await asyncio.sleep(delays[source_path.name])
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=source_path.stem),
            source_path=str(source_path),
            source_format="pdf",
            parser_name="UnderstandingAPI",
        )
        result.temp_dir_path = f"viking://temp/{source_path.stem}"
        return result

    merge_order: list[str] = []

    async def merge(classified_file, *_args, **_kwargs):
        merge_order.append(classified_file.rel_path)

    fake_fs = _FakeVikingFS()
    merge_mock = AsyncMock(side_effect=merge)
    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(ParserRouter, "parse", new=parse),
        patch.object(DirectoryParser, "_merge_parser_result", new=merge_mock),
    ):
        await DirectoryParser().parse(str(tmp_path), strict=True)

    assert merge_order == ["a.pdf", "b.pdf", "c.pdf"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parser_type", "extension", "content"),
    [
        (PDFParser, "pdf", b"%PDF-1.7"),
        (HTMLParser, "html", b"<h1>Page</h1>"),
    ],
)
async def test_no_split_directory_falls_back_to_native_parser(
    monkeypatch,
    tmp_path: Path,
    parser_type,
    extension,
    content,
):
    _configure_understanding(monkeypatch, [extension])
    source_path = tmp_path / f"paper.{extension}"
    source_path.write_bytes(content)
    understanding_parse = AsyncMock(
        side_effect=AssertionError("Understanding must not be submitted")
    )
    native_calls = []

    async def native_parse(_self, source, **kwargs):
        native_calls.append((Path(source), kwargs))
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title="paper"),
            source_path=str(source),
            source_format=extension,
            parser_name=parser_type.__name__,
        )
        result.temp_dir_path = f"viking://temp/native_{extension}"
        return result

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(ParserRouter, "parse", new=understanding_parse),
        patch.object(parser_type, "parse", new=native_parse),
        patch.object(DirectoryParser, "_merge_parser_result", new=AsyncMock()),
    ):
        result = await DirectoryParser().parse(str(tmp_path), split_content=False, strict=True)

    understanding_parse.assert_not_awaited()
    assert len(native_calls) == 1
    assert native_calls[0][0] == source_path
    assert native_calls[0][1]["split_content"] is False
    assert result.meta["processed_files"] == [
        {"path": source_path.name, "parser": parser_type.__name__}
    ]
    assert result.meta["failed_files"] == []


@pytest.mark.asyncio
async def test_no_split_directory_records_missing_native_parser_per_file(
    monkeypatch,
    tmp_path: Path,
):
    _configure_understanding(monkeypatch, ["bin"])
    (tmp_path / "README").write_text("plain text", encoding="utf-8")
    (tmp_path / "payload.bin").write_bytes(b"\x00\x01")
    understanding_parse = AsyncMock(
        side_effect=AssertionError("Understanding must not be submitted")
    )

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(ParserRouter, "parse", new=understanding_parse),
    ):
        result = await DirectoryParser().parse(str(tmp_path), split_content=False, strict=True)

    understanding_parse.assert_not_awaited()
    assert result.meta["file_count"] == 1
    assert result.meta["processed_files"] == [{"path": "README", "parser": "direct"}]
    assert result.meta["failed_files"] == [
        {
            "path": "payload.bin",
            "parser": "native",
            "error": (
                "parse_mode='no_split' requires a native parser, but none is "
                "available for this file type"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_directory_limits_fail_before_understanding_submit(monkeypatch, tmp_path: Path):
    _configure_understanding(monkeypatch, ["pdf"], max_files=1)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.7")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.7")
    parse = AsyncMock(side_effect=AssertionError("Understanding must not be submitted"))

    with patch.object(ParserRouter, "parse", new=parse):
        with pytest.raises(InvalidArgumentError, match="file count exceeds"):
            await DirectoryParser().parse(str(tmp_path), strict=True)

    parse.assert_not_awaited()


@pytest.mark.asyncio
async def test_nested_zip_leaf_failures_are_promoted_with_remote_ids(
    monkeypatch,
    tmp_path: Path,
):
    _configure_understanding(monkeypatch, ["pdf"])
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("good.md", "# good\n")
        archive.writestr("bad.pdf", b"%PDF-1.7")

    async def parse(_self, source, **_kwargs):
        assert Path(source).name == "bad.pdf"
        raise UnderstandingAPIError(
            "remote parse failed",
            {"file_id": "file-bad", "response_id": "response-bad"},
        )

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(ParserRouter, "parse", new=parse),
        patch.object(DirectoryParser, "_merge_parser_result", new=AsyncMock()),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert result.meta["processed_files"] == [{"path": "bundle.zip", "parser": "ZipParser"}]
    assert result.meta["failed_files"] == [
        {
            "path": "bundle.zip/bad.pdf",
            "parser": "UnderstandingAPI",
            "file_id": "file-bad",
            "response_id": "response-bad",
            "error": "remote parse failed",
        }
    ]


@pytest.mark.asyncio
async def test_understanding_directory_job_timeout_is_recorded(monkeypatch, tmp_path: Path):
    _configure_understanding(monkeypatch, ["pdf"])
    (tmp_path / "slow.pdf").write_bytes(b"%PDF-1.7")

    async def parse(_self, _source, **_kwargs):
        await asyncio.sleep(1)

    fake_fs = _FakeVikingFS()
    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(ParserRouter, "parse", new=parse),
        patch.object(DirectoryParser, "_get_parser_api_job_timeout", return_value=0.01),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert result.meta["file_count"] == 0
    assert result.meta["failed_files"][0]["path"] == "slow.pdf"
    assert "timed out" in result.meta["failed_files"][0]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("extension,content", [("pdf", b"%PDF-1.7"), ("html", b"<h1>Page</h1>")])
async def test_understanding_directory_failure_preserves_remote_ids(
    monkeypatch, tmp_path: Path, extension, content
):
    _configure_understanding(monkeypatch, [extension])
    (tmp_path / f"failed.{extension}").write_bytes(content)

    async def parse(_self, _source, **_kwargs):
        raise UnderstandingAPIError(
            "remote parse failed",
            {"file_id": "file-1", "response_id": "response-1"},
        )

    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=_FakeVikingFS()),
        patch.object(ParserRouter, "parse", new=parse),
    ):
        result = await DirectoryParser().parse(str(tmp_path), strict=True)

    assert result.meta["failed_files"] == [
        {
            "path": f"failed.{extension}",
            "parser": "UnderstandingAPI",
            "file_id": "file-1",
            "response_id": "response-1",
            "error": "remote parse failed",
        }
    ]


@pytest.mark.asyncio
async def test_cancelling_directory_parse_cancels_understanding_workers(
    monkeypatch,
    tmp_path: Path,
):
    _configure_understanding(monkeypatch, ["pdf"], max_concurrent=2)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.7")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.7")
    started = asyncio.Event()
    cancelled = 0

    async def parse(_self, _source, **_kwargs):
        nonlocal cancelled
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled += 1
            raise

    fake_fs = _FakeVikingFS()
    with (
        patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs),
        patch.object(ParserRouter, "parse", new=parse),
    ):
        task = asyncio.create_task(DirectoryParser().parse(str(tmp_path), strict=True))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled >= 1
