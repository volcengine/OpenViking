# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for percent-encoded image destinations in MarkdownParser._resolve_image_path
(see issue about URI-encoded local image paths not being resolved)."""

import tempfile
from pathlib import Path

from openviking.parse.image_rewrite import _rewrite_content
from openviking.parse.parsers.markdown import MarkdownParser


class TestEncodedImageDestinations:
    def _parser(self) -> MarkdownParser:
        return MarkdownParser()

    def _site_with(self, name: str, body: bytes = b"PNGDATA") -> Path:
        root = Path(tempfile.mkdtemp())
        (root / name).write_bytes(body)
        return root

    def test_encoded_space_resolves_to_existing_file(self):
        root = self._site_with("space chart.png")
        got = self._parser()._resolve_image_path("space%20chart.png", root, None)
        assert got is not None
        assert got.name == "space chart.png"

    def test_encoded_unicode_resolves(self):
        root = self._site_with("图表 图.png")
        got = self._parser()._resolve_image_path("%E5%9B%BE%E8%A1%A8%20%E5%9B%BE.png", root, None)
        assert got is not None
        assert got.name == "图表 图.png"

    def test_literal_percent_is_untouched(self):
        # "100%.png" has no valid escape and must stay as-is on disk.
        root = self._site_with("100%.png")
        got = self._parser()._resolve_image_path("100%.png", root, None)
        assert got is not None
        assert got.name == "100%.png"

    def test_literal_percent25_still_decodes(self):
        # A file literally named "100%.png" referenced as "100%25.png" decodes to it.
        root = self._site_with("100%.png")
        got = self._parser()._resolve_image_path("100%25.png", root, None)
        assert got is not None
        assert got.name == "100%.png"

    def test_unencoded_still_works(self):
        root = self._site_with("plain.png")
        got = self._parser()._resolve_image_path("plain.png", root, None)
        assert got is not None

    def test_missing_file_still_returns_none(self):
        root = self._site_with("plain.png")
        got = self._parser()._resolve_image_path("ghost.png", root, None)
        assert got is None

    def test_encoded_traversal_is_still_confined(self):
        # %2e%2e must decode to "../" *before* confinement so the escape route
        # out of the allowed root stays rejected.
        root = self._site_with("plain.png")
        outside = Path(tempfile.mkdtemp())
        (outside / "secret.png").write_bytes(b"NO")
        got = self._parser()._resolve_image_path("%2e%2e/secret.png", root, [outside.parent])
        assert got is None or (got is not None and root in got.parents)

    def test_html_encoded_reference_resolves(self):
        root = self._site_with("space chart.png")
        got = self._parser()._resolve_image_path("<space%20chart.png>", root, None)
        assert got is not None
        assert got.name == "space chart.png"


class TestEmittedDestinationEncoding:
    """The rewritten destination must survive the final Markdown/HTML renderer:
    raw spaces, ')', and '#' would truncate or misparse it (see the review on
    the sibling implementation, PR #7728). Only the emitted filename is
    percent-encoded; the mapping key and the filesystem name keep their
    original spelling.
    """

    IMAGE_DIR = "viking://resources/doc/page"

    def _rewrite(self, dest: str, image_name: str):
        mappings = {dest: image_name}
        content = f"![alt]({dest})"
        return _rewrite_content(content, self.IMAGE_DIR, {image_name}, mappings)

    def test_space_is_encoded(self):
        new, count = self._rewrite("space%20name.png", "space name.png")
        assert count == 1
        assert f"![alt]({self.IMAGE_DIR}/space%20name.png)" == new

    def test_unicode_with_space_is_encoded(self):
        new, count = self._rewrite("%E4%B8%AD%E6%96%87%20%E5%9B%BE.png", "中文 图.png")
        assert count == 1
        assert f"![alt]({self.IMAGE_DIR}/%E4%B8%AD%E6%96%87%20%E5%9B%BE.png)" == new

    def test_percent_sign_is_encoded(self):
        # A file literally named "percent%20.png" must not be re-read as an
        # encoded space by the renderer.
        new, count = self._rewrite("percent%2520.png", "percent%20.png")
        assert count == 1
        assert f"![alt]({self.IMAGE_DIR}/percent%2520.png)" == new

    def test_hash_is_encoded(self):
        new, count = self._rewrite("hash%23image.png", "hash#image.png")
        assert count == 1
        assert f"![alt]({self.IMAGE_DIR}/hash%23image.png)" == new

    def test_closing_paren_is_encoded(self):
        new, count = self._rewrite("closing%29paren.png", "closing)paren.png")
        assert count == 1
        assert f"![alt]({self.IMAGE_DIR}/closing%29paren.png)" == new

    def test_plain_name_is_unchanged(self):
        new, count = self._rewrite("plain.png", "plain.png")
        assert count == 1
        assert f"![alt]({self.IMAGE_DIR}/plain.png)" == new

    def test_plus_is_encoded(self):
        new, count = self._rewrite("plus%2Bimage.png", "plus+image.png")
        assert count == 1
        assert f"![alt]({self.IMAGE_DIR}/plus%2Bimage.png)" == new

    def test_html_img_tag_is_encoded(self):
        mappings = {"space%20name.png": "space name.png"}
        content = '<img src="space%20name.png" width="80%">'
        new, count = _rewrite_content(content, self.IMAGE_DIR, {"space name.png"}, mappings)
        assert count == 1
        assert f'<img src="{self.IMAGE_DIR}/space%20name.png" width="80%">' == new
