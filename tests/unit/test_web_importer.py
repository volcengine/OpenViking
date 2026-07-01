from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from openviking.parse.accessors.web_importer import (
    WebImportOptions,
    WebImporter,
    parse_web_import_options,
)
from openviking_cli.exceptions import InvalidArgumentError


class TestParseWebImportOptions:
    def test_defaults(self):
        opts = parse_web_import_options({})
        assert opts.depth == 0
        assert opts.max_pages == 50
        assert opts.allow_external_links is False
        assert opts.skip_download_links is True
        assert opts.include_paths is None
        assert opts.exclude_paths is None

    def test_depth_and_max_pages_parsed(self):
        opts = parse_web_import_options({"depth": 2, "max_pages": 30})
        assert opts.depth == 2
        assert opts.max_pages == 30

    def test_max_pages_zero_rejected(self):
        with pytest.raises(InvalidArgumentError):
            parse_web_import_options({"max_pages": 0})

    def test_depth_minus_two_rejected(self):
        with pytest.raises(InvalidArgumentError):
            parse_web_import_options({"depth": -2})

    def test_unlimited_combo_with_external_rejected(self):
        with pytest.raises(InvalidArgumentError):
            parse_web_import_options(
                {"depth": -1, "max_pages": -1, "allow_external_links": True}
            )

    def test_unlimited_depth_with_bounded_pages_ok(self):
        opts = parse_web_import_options(
            {"depth": -1, "max_pages": 100, "allow_external_links": True}
        )
        assert opts.depth == -1
        assert opts.max_pages == 100
        assert opts.allow_external_links is True

    def test_string_int_coerced(self):
        opts = parse_web_import_options({"depth": "3"})
        assert opts.depth == 3

    def test_invalid_int_rejected(self):
        with pytest.raises(InvalidArgumentError):
            parse_web_import_options({"depth": "abc"})

    def test_bool_parsed_from_string(self):
        opts = parse_web_import_options({"allow_external_links": "true"})
        assert opts.allow_external_links is True

    def test_skip_download_links_parsed_from_string(self):
        opts = parse_web_import_options({"skip_download_links": "true"})
        assert opts.skip_download_links is True

    def test_skip_download_links_can_be_disabled(self):
        opts = parse_web_import_options({"skip_download_links": "false"})
        assert opts.skip_download_links is False

    def test_invalid_bool_rejected(self):
        with pytest.raises(InvalidArgumentError):
            parse_web_import_options({"allow_external_links": "maybe"})

    def test_include_paths_csv(self):
        opts = parse_web_import_options({"include_paths": "/docs/,/api/"})
        assert opts.include_paths == ["/docs/", "/api/"]

    def test_include_paths_list(self):
        opts = parse_web_import_options({"include_paths": ["/a", "/b"]})
        assert opts.include_paths == ["/a", "/b"]

    def test_include_paths_empty_drops_to_none(self):
        opts = parse_web_import_options({"include_paths": ""})
        assert opts.include_paths is None

    def test_processor_args_popped_after_parse(self):
        args = {"depth": 2, "max_pages": 30, "other": "keep"}
        parse_web_import_options(args)
        assert args == {"other": "keep"}


class TestWebImportOptionsDataclass:
    def test_frozen(self):
        opts = WebImportOptions()
        with pytest.raises(FrozenInstanceError):
            opts.depth = 5  # type: ignore[misc]


class TestWebImporter:
    async def test_import_to_directory_writes_html_and_dedupes(self, monkeypatch):
        class FakeCrawler:
            def __init__(self, config):
                self.config = config

            async def crawl(self, root_url):
                return SimpleNamespace(
                    pages=[
                        SimpleNamespace(
                            url=root_url,
                            final_url=root_url,
                            depth=0,
                            status="success",
                            html="<html><body><h1>Home</h1></body></html>",
                        ),
                        SimpleNamespace(
                            url=f"{root_url}/guide",
                            final_url=f"{root_url}/guide",
                            depth=1,
                            status="success",
                            html="<html><body><h1>Guide</h1></body></html>",
                        ),
                        SimpleNamespace(
                            url=f"{root_url}/guide#frag",
                            final_url=f"{root_url}/guide#frag",
                            depth=1,
                            status="success",
                            html="<html><body><h1>Duplicate</h1></body></html>",
                        ),
                    ],
                    downloads=[],
                    total_crawled=3,
                    total_downloads=0,
                    total_failed=0,
                    total_skipped=0,
                    fallback_rendered=0,
                )

        monkeypatch.setattr("openviking.parse.accessors.web_importer.ScrapyWebCrawler", FakeCrawler)

        result = await WebImporter().import_to_directory(
            root_url="https://example.com",
            options=WebImportOptions(depth=1, max_pages=3),
        )
        try:
            files = sorted(path.relative_to(result.path).as_posix() for path in result.path.rglob("*.html"))
            assert files == ["Guide.html", "Home.html"]
            assert result.path.name == "example.com"
            assert (result.path / "Home.html").read_text(encoding="utf-8") == (
                "<html><body><h1>Home</h1></body></html>"
            )
            assert result.meta["page_count"] == 2
            assert result.meta["original_filename"] == "example.com"
        finally:
            import shutil

            shutil.rmtree(result.path.parent, ignore_errors=True)

    async def test_import_to_directory_rejects_missing_entry(self, monkeypatch):
        class FakeCrawler:
            def __init__(self, config):
                self.config = config

            async def crawl(self, root_url):
                return SimpleNamespace(
                    pages=[
                        SimpleNamespace(
                            url=f"{root_url}/child",
                            final_url=f"{root_url}/child",
                            depth=1,
                            status="success",
                            html="<html>child</html>",
                        )
                    ],
                    downloads=[],
                    total_crawled=1,
                    total_downloads=0,
                    total_failed=0,
                    total_skipped=0,
                    fallback_rendered=0,
                )

        monkeypatch.setattr("openviking.parse.accessors.web_importer.ScrapyWebCrawler", FakeCrawler)

        with pytest.raises(RuntimeError, match="Failed to fetch entry page"):
            await WebImporter().import_to_directory(
                root_url="https://example.com",
                options=WebImportOptions(),
            )

    async def test_entry_failure_surfaces_renderer_hint(self, monkeypatch):
        class FakeCrawler:
            def __init__(self, config):
                self.config = config

            async def crawl(self, root_url):
                return SimpleNamespace(
                    pages=[
                        SimpleNamespace(
                            url=root_url,
                            final_url=root_url,
                            depth=0,
                            status="failed",
                            html=None,
                            error="Playwright fallback was needed, but the Python package is not installed.",
                        )
                    ],
                    downloads=[],
                    total_crawled=0,
                    total_downloads=0,
                    total_failed=1,
                    total_skipped=0,
                    fallback_rendered=0,
                )

        monkeypatch.setattr("openviking.parse.accessors.web_importer.ScrapyWebCrawler", FakeCrawler)

        with pytest.raises(RuntimeError, match="Playwright fallback was needed"):
            await WebImporter().import_to_directory(
                root_url="https://example.com",
                options=WebImportOptions(),
            )

    async def test_child_render_hint_surfaces_in_meta(self, monkeypatch):
        from openviking.parse.accessors.web_crawler.playwright_renderer import (
            PLAYWRIGHT_PACKAGE_INSTALL_HINT,
        )

        class FakeCrawler:
            def __init__(self, config):
                self.config = config

            async def crawl(self, root_url):
                return SimpleNamespace(
                    pages=[
                        SimpleNamespace(
                            url=root_url,
                            final_url=root_url,
                            depth=0,
                            status="success",
                            html="<html><body><h1>Home</h1></body></html>",
                            error=None,
                        ),
                        SimpleNamespace(
                            url=f"{root_url}/spa",
                            final_url=f"{root_url}/spa",
                            depth=1,
                            status="failed",
                            html=None,
                            error=PLAYWRIGHT_PACKAGE_INSTALL_HINT,
                        ),
                    ],
                    downloads=[],
                    total_crawled=1,
                    total_downloads=0,
                    total_failed=1,
                    total_skipped=0,
                    fallback_rendered=0,
                )

        monkeypatch.setattr("openviking.parse.accessors.web_importer.ScrapyWebCrawler", FakeCrawler)

        result = await WebImporter().import_to_directory(
            root_url="https://example.com",
            options=WebImportOptions(depth=1, max_pages=5),
        )
        try:
            assert result.meta["render_hints"] == [PLAYWRIGHT_PACKAGE_INSTALL_HINT]
            assert result.meta["page_count"] == 1
        finally:
            import shutil

            shutil.rmtree(result.path.parent, ignore_errors=True)

    async def test_import_to_directory_downloads_child_links(self, monkeypatch, tmp_path):
        class FakeCrawler:
            def __init__(self, config):
                self.config = config

            async def crawl(self, root_url):
                return SimpleNamespace(
                    pages=[
                        SimpleNamespace(
                            url=root_url,
                            final_url=root_url,
                            depth=0,
                            status="success",
                            html="<html><body><h1>Home</h1></body></html>",
                        )
                    ],
                    downloads=[
                        SimpleNamespace(url=f"{root_url}/docs/llms.txt", depth=1),
                    ],
                    total_crawled=1,
                    total_downloads=1,
                    total_failed=0,
                    total_skipped=0,
                    fallback_rendered=0,
                )

        download_file = tmp_path / "download.txt"
        download_file.write_text("plain docs", encoding="utf-8")

        async def fake_download_url(self, url, request_validator=None):
            return str(download_file), SimpleNamespace(value="download_txt"), {
                "original_filename": "llms.txt",
            }

        monkeypatch.setattr("openviking.parse.accessors.web_importer.ScrapyWebCrawler", FakeCrawler)
        monkeypatch.setattr(
            "openviking.parse.accessors.http_accessor.HTTPAccessor._download_url",
            fake_download_url,
        )

        result = await WebImporter().import_to_directory(
            root_url="https://example.com",
            options=WebImportOptions(depth=1, max_pages=2),
        )
        try:
            assert (result.path / "docs" / "llms.txt").read_text(encoding="utf-8") == (
                "plain docs"
            )
            assert result.meta["download_count"] == 1
            assert result.meta["crawl_result"]["total_downloads"] == 1
        finally:
            import shutil

            shutil.rmtree(result.path.parent, ignore_errors=True)
