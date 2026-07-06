# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.queuefs.semantic_processor import has_substantive_content

# Plan §7 Table 1 (16 rows) + two robustness rows (CRLF, bold-run body).
_CASES = [
    ("empty", "", False),
    ("whitespace", "   \n\t\n", False),
    ("heading_only_atx", "# Title\n## Subtitle", False),
    ("heading_only_setext", "Title\n=====\n", False),
    ("heading_plus_body", "# Install\n\nRun `make build` to compile.", True),
    ("frontmatter_only", "---\ntitle: x\ntags: [a]\n---\n", False),
    ("frontmatter_only_eof", "---\ntitle: Test\n---", False),
    ("html_comment_only", "<!-- generated -->", False),
    ("table_only", "| A | B |\n|---|---|\n| foo | bar |", True),
    ("links_only_with_text", "- [Guide](a.md)\n- [API](b.md)", True),
    ("bare_urls_only", "https://a.com\nhttps://b.com", False),
    ("image_alt_only", "![architecture diagram of the system](d.png)", True),
    ("image_no_alt", "![](d.png)", False),
    ("code_only", "```python\ndef f(): return 1\n```", True),
    ("cjk_title_only", "# 概述\n## 目录", False),
    ("cjk_heading_plus_body", "# 概述\n\n这是一个测试文档。", True),
    ("divider_only", "---\n***\n", False),
    ("crlf_heading_only", "# Title\r\n", False),
    ("bold_run_body", "# H\r\n\r\nThis is **very** important.", True),
]


@pytest.mark.parametrize("name,text,expected", _CASES, ids=[c[0] for c in _CASES])
def test_has_substantive_content(name, text, expected):
    assert has_substantive_content(text)[0] == expected
