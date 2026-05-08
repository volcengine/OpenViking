from openviking.storage.queuefs.overview import parse_overview_md


def test_parse_overview_md_supports_numbered_entries():
    content = "[1] a.py: Handles API routes.\n[2] b.py: Stores shared helpers."

    assert parse_overview_md(content) == {
        "a.py": "Handles API routes.",
        "b.py": "Stores shared helpers.",
    }


def test_parse_overview_md_supports_markdown_detail_headings():
    content = """# demo

## Detailed Description

### a.py
Handles API routes.

### b.py b.py
Stores shared helpers.
"""

    assert parse_overview_md(content) == {
        "a.py": "Handles API routes.",
        "b.py": "Stores shared helpers.",
    }


def test_parse_overview_md_supports_bullet_entries():
    content = "FILES:\n- a.py: old summary\n- b.py: unchanged summary"

    assert parse_overview_md(content) == {
        "a.py": "old summary",
        "b.py": "unchanged summary",
    }
