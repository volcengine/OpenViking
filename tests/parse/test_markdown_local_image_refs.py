# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MarkdownParser._layout_has_local_image_refs, the in-memory image-ref
probe that lets _apply_layout skip _ingest_local_images's glob+read pass over
every generated markdown chunk when a layout (e.g. an Excel-derived table) has
no local image references at all."""

from openviking.parse.parsers.markdown import MarkdownParser, _LayoutOp


class TestLayoutHasLocalImageRefs:
    def _parser(self) -> MarkdownParser:
        return MarkdownParser()

    def _write_ops(self, *contents: str) -> list:
        return [_LayoutOp("write", f"viking://temp/x/{i}.md", c) for i, c in enumerate(contents)]

    def test_pure_text_returns_false(self):
        ops = self._write_ops("# Title\n\nJust some plain text with no images at all.")
        assert self._parser()._layout_has_local_image_refs(ops) is False

    def test_local_markdown_image_returns_true(self):
        ops = self._write_ops("# Title\n\n![alt](local.png)")
        assert self._parser()._layout_has_local_image_refs(ops) is True

    def test_remote_markdown_image_returns_false(self):
        ops = self._write_ops("# Title\n\n![alt](https://example.com/a.png)")
        assert self._parser()._layout_has_local_image_refs(ops) is False

    def test_local_html_img_returns_true(self):
        ops = self._write_ops('# Title\n\n<img src="local.png">')
        assert self._parser()._layout_has_local_image_refs(ops) is True

    def test_mixed_ops_true_if_any_op_has_local_image(self):
        ops = self._write_ops(
            "no images here",
            "https://example.com/remote.png only, still remote: ![alt](https://example.com/remote.png)",
            "![alt](assets/local.jpg)",
        )
        assert self._parser()._layout_has_local_image_refs(ops) is True

    def test_empty_write_ops_returns_false(self):
        assert self._parser()._layout_has_local_image_refs([]) is False
