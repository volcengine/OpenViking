# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for percent-encoded image destinations in MarkdownParser._resolve_image_path
(see issue about URI-encoded local image paths not being resolved)."""

from pathlib import Path

import pytest

from openviking.parse.parsers.markdown import MarkdownParser


class TestEncodedImageDestinations:
    def _parser(self) -> MarkdownParser:
        return MarkdownParser()

    def _site_with(self, name: str, body: bytes = b"PNGDATA") -> Path:
        import tempfile

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
