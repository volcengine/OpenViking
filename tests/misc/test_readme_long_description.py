import re
from pathlib import Path

from scripts.build_support.readme import pypi_long_description, rewrite_relative_urls

RAW = "https://raw.githubusercontent.com/volcengine/OpenViking/main/"
BLOB = "https://github.com/volcengine/OpenViking/blob/main/"


def test_rewrites_relative_html_images_and_links():
    text = (
        '<a href="docs/a.md"><picture><source srcset="docs/images/x-dark.svg">'
        '<img src="docs/images/x.svg"></picture></a>'
    )
    assert rewrite_relative_urls(text) == (
        f'<a href="{BLOB}docs/a.md"><picture><source srcset="{RAW}docs/images/x-dark.svg">'
        f'<img src="{RAW}docs/images/x.svg"></picture></a>'
    )


def test_rewrites_relative_markdown_images_and_links():
    text = "![logo](./docs/images/l.png) see [LICENSE](./LICENSE) and [bench](./benchmark)"
    assert rewrite_relative_urls(text) == (
        f"![logo]({RAW}docs/images/l.png) see [LICENSE]({BLOB}LICENSE) and [bench]({BLOB}benchmark)"
    )


def test_keeps_absolute_urls_and_anchors():
    text = '[a](https://x.dev/p) [b](#section) <img src="https://x.dev/i.png"> <a href="mailto:a@b.c">m</a>'
    assert rewrite_relative_urls(text) == text


def test_repository_readme_has_no_relative_targets():
    readme = Path(__file__).resolve().parents[2] / "README.md"
    description = pypi_long_description(readme)
    targets = re.findall(r'(?:src|srcset|href)="([^"]*)"', description)
    targets += re.findall(r"\]\(([^)\s]+)", description)
    relative = [t for t in targets if not re.match(r"^(?:https?:|mailto:|#)", t)]
    assert relative == []
