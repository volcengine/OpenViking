# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Directory abstract extraction must not emit bare thematic breaks (issue #4336).

A ``---`` separator between the overview headings and the brief description was
treated as the first content line, so ``.abstract.md`` ended up containing
exactly ``---`` and the semantic-sidecar parser failed with
"frontmatter is not closed" for the whole subtree.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from openviking.storage.abstract_overview import write_abstract_overview
from openviking.storage.queuefs.semantic_processor import SemanticProcessor

REPRO_OVERVIEW = """### Directory
#### wiki

---

#### Brief Description:
This directory contains public-service and legal-aid documents.

## Details
Other section.
"""


def _extract(content: str) -> str:
    processor = object.__new__(SemanticProcessor)
    return processor._extract_abstract_from_overview(content)


def test_repro_thematic_break_before_brief_description() -> None:
    abstract = _extract(REPRO_OVERVIEW)
    assert abstract == "This directory contains public-service and legal-aid documents."


def test_bare_thematic_break_only_overview_yields_empty_abstract() -> None:
    assert _extract("### Directory\n#### wiki\n\n---\n") == ""


def test_plain_overview_still_extracts_first_section() -> None:
    content = "## Overview\nFirst paragraph.\nSecond line.\n\n## Next\nlater"
    assert _extract(content) == "First paragraph.\nSecond line."


def test_inline_brief_heading_yields_body_after_label() -> None:
    content = "### Directory\n#### wiki\n\n---\n\n#### Brief Description: This directory contains public-service and legal-aid documents.\n"
    assert _extract(content) == "This directory contains public-service and legal-aid documents."


def test_inline_brief_heading_fullwidth_colon_zh() -> None:
    content = "#### \u7b80\u8981\u63cf\u8ff0\uff1a\u672c\u76ee\u5f55\u5305\u542b\u516c\u5171\u670d\u52a1\u4e0e\u6cd5\u5f8b\u63f4\u52a9\u6587\u6863\u3002"
    assert _extract(content) == "\u672c\u76ee\u5f55\u5305\u542b\u516c\u5171\u670d\u52a1\u4e0e\u6cd5\u5f8b\u63f4\u52a9\u6587\u6863\u3002"


def test_heading_of_any_level_ends_abstract_section() -> None:
    content = (
        "#### Brief Description:\nPart one.\n\n#### Notes\nstray notes text\n"
    )
    assert _extract(content) == "Part one."


def test_spaced_thematic_break_variant_is_skipped() -> None:
    content = "### Directory\n#### wiki\n\n- - -\n\nFirst prose line.\n"
    assert _extract(content) == "First prose line."


def test_structural_headings_do_not_leak_into_abstract() -> None:
    content = "### Directory\n#### wiki\n#### Brief Description:\nReal prose here.\n"
    assert _extract(content) == "Real prose here."


def test_thematic_break_inside_brief_description_is_skipped() -> None:
    content = "#### Brief Description:\nPart one.\n\n---\n\nPart two.\n\n## Next\nx"
    assert _extract(content) == "Part one.\nPart two."


class _RecordingFS:
    """Minimal VikingFS double: records writes, no existing sidecars."""

    def __init__(self) -> None:
        self.writes: Dict[str, str] = {}

    async def _uri_to_path(self, uri: str, ctx: Optional[Any] = None) -> str:
        return uri

    async def read_file(self, uri: str, ctx: Optional[Any] = None) -> str:
        if uri in self.writes:
            return self.writes[uri]
        raise KeyError(uri)

    async def write_file(self, uri: str, body: str, ctx: Optional[Any] = None, **_: Any) -> None:
        self.writes[uri] = body


class _NullLease:
    pass


class _StubAGFS:
    async def pathlock_acquire_exact_batch(self, paths: List[str]) -> _NullLease:
        return _NullLease()

    async def pathlock_release(self, lease: _NullLease) -> None:
        return None


class _LockableFS(_RecordingFS):
    def __init__(self) -> None:
        super().__init__()
        self._async_agfs = _StubAGFS()


@pytest.mark.asyncio
async def test_empty_abstract_is_not_persisted() -> None:
    fs = _LockableFS()
    result = await write_abstract_overview(
        viking_fs=fs,
        dir_uri="viking://user/u/resources/dir",
        overview="Meaningful overview prose.",
        abstract="",
        ctx=None,
        is_stale=lambda: False,
    )
    assert result.wrote is True
    assert any(uri.endswith(".overview.md") for uri in fs.writes)
    assert not any(uri.endswith(".abstract.md") for uri in fs.writes), (
        "a content-free abstract must not be persisted"
    )


@pytest.mark.asyncio
async def test_prose_abstract_is_still_persisted() -> None:
    fs = _LockableFS()
    await write_abstract_overview(
        viking_fs=fs,
        dir_uri="viking://user/u/resources/dir",
        overview="Meaningful overview prose.",
        abstract="A real abstract sentence.",
        ctx=None,
        is_stale=lambda: False,
    )
    assert any(uri.endswith(".abstract.md") for uri in fs.writes)
