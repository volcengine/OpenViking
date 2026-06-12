# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for HTML-to-Markdown cleanup helpers."""

from openviking.parse.parsers.html import HTMLParser


def test_clean_inline_images_removes_noise_without_dropping_remote_images():
    markdown = """
before
![](data:image/png;base64,abc)
<img src="data:image/png;base64,def" />
![](https://example.com/a.png =4881x)
<span id="anchor"></span>
![](https://example.com/keep.png)



after
"""

    cleaned = HTMLParser._clean_inline_images(markdown)

    assert "data:image" not in cleaned
    assert '<span id="anchor"></span>' not in cleaned
    assert "![](https://example.com/a.png)" in cleaned
    assert "![](https://example.com/keep.png)" in cleaned
    assert "\n\n\n" not in cleaned
