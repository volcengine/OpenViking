# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for filename safety: hash & shorten when names are too long (issue #171)."""

import hashlib


class TestSanitizeForPath:
    """Test _sanitize_for_path in MarkdownParser and HTMLParser."""

    def _make_md_parser(self):
        from openviking.parse.parsers.markdown import MarkdownParser

        return MarkdownParser()

    def test_short_text_unchanged(self):
        parser = self._make_md_parser()
        assert parser._sanitize_for_path("Hello World") == "Hello_World"

    def test_empty_text_returns_section(self):
        parser = self._make_md_parser()
        assert parser._sanitize_for_path("") == "section"
        assert parser._sanitize_for_path("!!!") == "section"

    def test_special_chars_removed(self):
        parser = self._make_md_parser()
        result = parser._sanitize_for_path("Hello, World! (test)")
        assert result == "Hello_World_test"

    def test_chinese_preserved(self):
        parser = self._make_md_parser()
        result = parser._sanitize_for_path("你好世界")
        assert result == "你好世界"

    def test_long_text_truncated_with_hash(self):
        parser = self._make_md_parser()
        long_text = "a" * 100
        result = parser._sanitize_for_path(long_text)
        assert len(result) <= 50
        expected_hash = hashlib.sha256(long_text.encode()).hexdigest()[:8]
        assert result.endswith(f"_{expected_hash}")

    def test_exact_boundary_not_hashed(self):
        parser = self._make_md_parser()
        text = "a" * 50
        result = parser._sanitize_for_path(text)
        assert result == text
        assert len(result) == 50

    def test_one_over_boundary_hashed(self):
        parser = self._make_md_parser()
        text = "a" * 51
        result = parser._sanitize_for_path(text)
        assert len(result) <= 50
        assert "_" in result  # has hash suffix

    def test_custom_max_length(self):
        parser = self._make_md_parser()
        text = "a" * 30
        result = parser._sanitize_for_path(text, max_length=20)
        assert len(result) <= 20
        expected_hash = hashlib.sha256(text.encode()).hexdigest()[:8]
        assert result.endswith(f"_{expected_hash}")

    def test_shell_comment_heading(self):
        """Simulate shell script comments being treated as markdown headings."""
        parser = self._make_md_parser()
        heading = "Usage: curl -fsSL https://raw.githubusercontent.com/volcengine/openviking/refs/tags/cli@0.1.0/crates/ov_cli/install.sh | bash"
        result = parser._sanitize_for_path(heading)
        assert len(result) <= 50


class TestGenerateMergedFilename:
    """Test _generate_merged_filename in MarkdownParser."""

    def _make_md_parser(self):
        from openviking.parse.parsers.markdown import MarkdownParser

        return MarkdownParser()

    def test_single_short_section(self):
        parser = self._make_md_parser()
        result = parser._generate_merged_filename([("intro", "content", 100)])
        assert result == "intro"

    def test_multiple_sections(self):
        parser = self._make_md_parser()
        sections = [("intro", "c1", 10), ("body", "c2", 20), ("end", "c3", 30)]
        result = parser._generate_merged_filename(sections)
        assert "3more" in result
        assert len(result) <= 32

    def test_empty_sections(self):
        parser = self._make_md_parser()
        assert parser._generate_merged_filename([]) == "merged"

    def test_long_single_name_hashed(self):
        parser = self._make_md_parser()
        long_name = "a" * 100
        result = parser._generate_merged_filename([(long_name, "content", 50)])
        assert len(result) <= 32

    def test_result_never_exceeds_limit(self):
        parser = self._make_md_parser()
        # Create many sections with long names
        sec_list = [(f"very_long_section_name_{i}", f"content_{i}", 10) for i in range(20)]
        result = parser._generate_merged_filename(sec_list)
        assert len(result) <= 32


class TestShortenComponent:
    """Test VikingFS._shorten_component."""

    def test_short_component_unchanged(self):
        from openviking.storage.viking_fs import VikingFS

        assert VikingFS._shorten_component("hello") == "hello"

    def test_long_component_shortened(self):
        from openviking.storage.viking_fs import VikingFS

        long_name = "a" * 300
        result = VikingFS._shorten_component(long_name)
        assert len(result.encode("utf-8")) <= 255

    def test_exact_255_bytes_unchanged(self):
        from openviking.storage.viking_fs import VikingFS

        name = "a" * 255
        assert VikingFS._shorten_component(name) == name

    def test_256_bytes_shortened(self):
        from openviking.storage.viking_fs import VikingFS

        name = "a" * 256
        result = VikingFS._shorten_component(name)
        assert len(result.encode("utf-8")) <= 255
        expected_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        assert result.endswith(f"_{expected_hash}")

    def test_unicode_multibyte_handling(self):
        from openviking.storage.viking_fs import VikingFS

        # Chinese chars are 3 bytes each in UTF-8
        name = "你" * 100  # 300 bytes
        result = VikingFS._shorten_component(name)
        assert len(result.encode("utf-8")) <= 255

    def test_realistic_long_filename(self):
        """Simulate the exact bug from issue #171."""
        from openviking.storage.viking_fs import VikingFS

        long_filename = (
            "tmp5vacylnx_OpenViking_CLI_Installer_Usage_curl_-fsSL_"
            "httpsrawgithubusercontentcomvolce_Example_curl_-fsSL_"
            "httpsrawgithubusercontentcomvol_Skip_checksum_"
            "SKIP_CHECKSUM1_curl_-fsSL_bash_Colors_for_output_"
            "Detect_platform_and_architecture_Get_latest_release_info_"
            "Download_and_extract_binary"
        )
        result = VikingFS._shorten_component(long_filename)
        assert len(result.encode("utf-8")) <= 255


class TestDownloaderGenerateFilename:
    """Test _generate_filename in downloader."""

    def test_short_url(self):
        from openviking_cli.utils.downloader import _generate_filename

        result = _generate_filename("https://example.com/file.pdf")
        assert result == "file"

    def test_long_path_url(self):
        from openviking_cli.utils.downloader import _generate_filename

        url = "https://example.com/" + "a" * 200 + ".pdf"
        result = _generate_filename(url)
        assert len(result) <= 50

    def test_host_only_url(self):
        from openviking_cli.utils.downloader import _generate_filename

        result = _generate_filename("https://example.com/")
        assert result == "example_com"
