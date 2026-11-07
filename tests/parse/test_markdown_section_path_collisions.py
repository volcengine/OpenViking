"""Regression tests for duplicate section paths in the Markdown parser (#7182).

Two sibling sections whose sanitized titles match used to be planned at the same
``<parent>/<title>.md`` URI, so the later write silently replaced the earlier one.
Merged groups could collide the same way because their filename hash used the
sections' character counts instead of their contents.
"""

import asyncio

from openviking.parse.parsers.markdown import MarkdownParser


def _writes(layout):
    return [op for op in layout.ops if op.kind == "write"]


def test_sibling_sections_with_the_same_title_both_survive():
    # Each section is larger than the default per-file budget, so the parser plans
    # them as separate directories/files instead of one document file.
    first = "first body must survive " * 120
    second = "second body must survive " * 120
    content = f"# Report\n\n## Notes\n\n{first}\n\n## Notes\n\n{second}\n"
    layout = asyncio.run(
        MarkdownParser()._compute_layout(
            content, "viking://temp/section-collision", resource_name="report"
        )
    )

    writes = _writes(layout)
    paths = [op.uri for op in writes]
    assert len(paths) == len(set(paths)), paths
    planned = "\n".join(op.content or "" for op in writes)
    assert "first body must survive" in planned
    assert "second body must survive" in planned


def test_merged_filenames_differ_for_same_titles_and_same_lengths():
    parser = MarkdownParser()
    first = [("Company", "aaa", 3), ("Company", "bbb", 3)]
    second = [("Company", "ccc", 3), ("Company", "ddd", 3)]

    assert parser._generate_merged_filename(first) != parser._generate_merged_filename(second)


def test_deduplicate_layout_paths_renames_duplicate_writes_and_directories():
    from openviking.parse.parsers.markdown import _LayoutOp

    ops = [
        _LayoutOp("mkdir", "viking://temp/doc/Run"),
        _LayoutOp("write", "viking://temp/doc/Run/Notes.md", "first"),
        _LayoutOp("mkdir", "viking://temp/doc/Run"),
        _LayoutOp("write", "viking://temp/doc/Run/Notes.md", "second"),
    ]
    deduplicated = MarkdownParser._deduplicate_layout_paths(ops)

    assert [op.kind for op in deduplicated] == ["mkdir", "write", "mkdir", "write"]
    paths = [op.uri for op in deduplicated]
    assert len(paths) == len(set(paths)), paths
    # The renamed directory keeps its second write under it.
    assert deduplicated[3].uri.startswith(deduplicated[2].uri + "/")
    assert deduplicated[3].content == "second"
