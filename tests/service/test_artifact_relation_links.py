"""Markdown semantics and URI resolution independent of a live filesystem."""

import pytest

from openviking.service.artifact_relation_links import (
    extract_relation_source,
    git_blob_oid,
    resolve_relation_link,
)

ROOT = "viking://resources/wiki"
PAGE = f"{ROOT}/sub/page.md"


def test_inline_reference_and_autolinks_keep_raw_destinations():
    content = """
[中文](目录/页面.md)
[缺失][future]
[重复](目录/页面.md)
[锚点](#section)
[外部](https://example.com/docs)
<https://example.com/auto>

[future]: ../未来.md "页面标题"
""".encode()
    source = extract_relation_source(content)
    assert source.links == [
        "目录/页面.md",
        "../未来.md",
        "#section",
        "https://example.com/docs",
        "https://example.com/auto",
    ]
    assert source.blob_oid == git_blob_oid(content)


def test_images_code_html_and_unused_definitions_are_not_page_links():
    content = b"""
![image](image.png)
`[inline code](inline.md)`

    [indented code](indented.md)

```markdown
[fenced code](fenced.md)
```

<a href="html.md">HTML</a>
[unused]: unused.md
[real](real.md)
"""
    assert extract_relation_source(content).links == ["real.md"]


def test_destinations_preserve_spaces_encoding_and_markdown_escapes():
    content = rb"""
[space](<a b.md>)
[encoded](a%23b.md#section)
[escaped](a\(b\).md)
[entity](a&amp;b.md)
[image link](!image.md)
[thumbnail](page.md "title") ![thumbnail](page.md)
"""
    assert extract_relation_source(content).links == [
        "a b.md",
        "a%23b.md#section",
        "a(b).md",
        "a&b.md",
        "!image.md",
        "page.md",
    ]


def test_reference_links_inside_lists_and_quotes():
    content = b"""
- [full][page]
- [collapsed][]
> [shortcut]

[page]: ../page.md
[collapsed]: collapsed.md
[shortcut]: shortcut.md
"""
    assert extract_relation_source(content).links == ["../page.md", "collapsed.md", "shortcut.md"]


def test_empty_content_and_invalid_utf8():
    assert extract_relation_source(b"").links == []
    with pytest.raises(UnicodeDecodeError):
        extract_relation_source(b"[invalid](\xff.md)")


@pytest.mark.parametrize(
    "href,expected",
    [
        ("../missing.md", f"{ROOT}/missing.md"),
        ("./child.md#intro", f"{ROOT}/sub/child.md"),
        ("../目录/页面.md", f"{ROOT}/目录/页面.md"),
        ("../%E7%9B%AE%E5%BD%95/%E9%A1%B5%E9%9D%A2.md", f"{ROOT}/目录/页面.md"),
        ("../a%23b.md#intro", f"{ROOT}/a#b.md"),
        ("../a%2523b.md", f"{ROOT}/a%23b.md"),
        ("../a%20b.md", f"{ROOT}/a_b.md"),
        ("../a b.md", f"{ROOT}/a_b.md"),
        ("../目录/", f"{ROOT}/目录"),
        ("../", ROOT),
        ("/resources/wiki/a.md", f"{ROOT}/a.md"),
        ("viking://resources/wiki/a.md#intro", f"{ROOT}/a.md"),
        ("child.md?view=1#intro", f"{ROOT}/sub/child.md"),
    ],
)
def test_resolve_paths_without_checking_existence(href, expected):
    assert resolve_relation_link(PAGE, href, target_root=ROOT) == expected


@pytest.mark.parametrize(
    "href",
    [
        "",
        "#intro",
        "?view=1",
        "https://example.com/a.md",
        "//example.com/a.md",
        "mailto:alice@example.com",
        "javascript:alert(1)",
        "../../outside.md",
        "/resources/wiki-other/a.md",
        "viking://resources/other/a.md",
        "viking:resources/wiki/a.md",
        "../%2e%2e/escape.md",
        "../a%2fb.md",
        "../a%5cb.md",
        "../invalid%ff.md",
        "../invalid%3fname.md",
        "https://[invalid",
    ],
)
def test_external_unsafe_and_out_of_target_links_have_no_target(href):
    assert resolve_relation_link(PAGE, href, target_root=ROOT) is None


def test_same_raw_link_resolves_again_after_source_page_moves():
    link = extract_relation_source(b"[next](next.md)").links[0]
    assert resolve_relation_link(PAGE, link, target_root=ROOT) == f"{ROOT}/sub/next.md"
    assert (
        resolve_relation_link(f"{ROOT}/moved/page.md", link, target_root=ROOT)
        == f"{ROOT}/moved/next.md"
    )


def test_source_page_outside_target_is_a_caller_error():
    with pytest.raises(ValueError, match="outside Compile target"):
        resolve_relation_link("viking://resources/other/a.md", "b.md", target_root=ROOT)


def test_git_empty_blob_known_oid():
    assert git_blob_oid(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
