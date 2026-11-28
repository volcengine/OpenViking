# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for MemorySearchTool semantic→grep fallback.

Pure unit tests: ctx.viking_fs is mocked, no vectordb/embedder initialized.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.session.memory.tools import MemorySearchTool


def _fake_find_result(memories):
    """Minimal stand-in for FindResult with the .to_dict() shape the tool reads."""
    return SimpleNamespace(to_dict=lambda: {"memories": memories})


def _make_ctx(search_return, grep_return=None, grep_raises=None):
    viking_fs = SimpleNamespace()
    viking_fs.search = AsyncMock(return_value=search_return)
    if grep_raises is not None:
        viking_fs.grep = AsyncMock(side_effect=grep_raises)
    else:
        viking_fs.grep = AsyncMock(return_value=grep_return or {"matches": []})
    return SimpleNamespace(
        viking_fs=viking_fs,
        default_search_uris="viking://user/u/memories",
        request_ctx=None,
    )


async def test_search_with_results_does_not_grep():
    """When semantic search returns memories, grep must NOT be called."""
    find = _fake_find_result([{"uri": "viking://user/u/m1.md", "score": 0.9}])
    ctx = _make_ctx(find, grep_return={"matches": [{"uri": "should-not-appear"}]})
    tool = MemorySearchTool()

    result = await tool.execute(ctx, query="anything", limit=10)

    assert isinstance(result, list)
    assert result and result[0]["uri"].endswith("m1.md")
    ctx.viking_fs.grep.assert_not_called()


async def test_search_empty_falls_back_to_grep():
    """Empty semantic recall triggers grep; matches map to {uri, score} memories."""
    find = _fake_find_result([])
    grep_ret = {
        "matches": [
            {"uri": "viking://user/u/notes.md", "line": 42, "content": "hermes agent"},
        ]
    }
    ctx = _make_ctx(find, grep_return=grep_ret)
    tool = MemorySearchTool()

    result = await tool.execute(ctx, query="hermes", limit=10)

    ctx.viking_fs.grep.assert_awaited_once()
    # grep called with regex-escaped pattern, case_insensitive=True
    _, kwargs = ctx.viking_fs.grep.call_args
    assert kwargs["pattern"] == "hermes"  # re.escape("hermes") == "hermes"
    assert kwargs["case_insensitive"] is True
    assert result and result[0]["uri"].endswith("notes.md")


async def test_search_empty_and_grep_empty_returns_empty_list():
    """Both empty → optimize_search_result([]) → [] (not a dict, not error)."""
    find = _fake_find_result([])
    ctx = _make_ctx(find, grep_return={"matches": []})
    tool = MemorySearchTool()

    result = await tool.execute(ctx, query="nothinghere", limit=10)

    assert result == []


async def test_grep_exception_does_not_break_search():
    """If grep raises, the tool returns [] instead of propagating."""
    find = _fake_find_result([])
    ctx = _make_ctx(find, grep_raises=RuntimeError("vikingdb down"))
    tool = MemorySearchTool()

    result = await tool.execute(ctx, query="x", limit=10)

    assert result == []


async def test_empty_query_does_not_grep():
    """Empty query must not trigger grep — re.escape('') == '' matches everything."""
    find = _fake_find_result([])
    ctx = _make_ctx(find, grep_return={"matches": [{"uri": "viking://user/u/x.md"}]})
    tool = MemorySearchTool()

    result = await tool.execute(ctx, query="", limit=10)

    ctx.viking_fs.grep.assert_not_called()
    assert result == []


async def test_grep_pattern_is_regex_escaped():
    """Query with regex metacharacters must be escaped, not interpreted."""
    find = _fake_find_result([])
    ctx = _make_ctx(find, grep_return={"matches": []})
    tool = MemorySearchTool()

    await tool.execute(ctx, query="a.b*c+", limit=10)

    _, kwargs = ctx.viking_fs.grep.call_args
    assert kwargs["pattern"] == r"a\.b\*c\+"  # re.escape escapes . * +
