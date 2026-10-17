# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for the ``preference_full_count`` budget accounting.

Bug: in ``MemoryStore._parse_viking_memory`` the preference full-render cap
counter was incremented *before* the budget check::

    elif use_type_budgets and memory_type == "preferences":
        preference_full_count += 1          # incremented unconditionally
        if total_chars + full_chars <= max_chars:
            ...

so the first ``preference_full_limit`` preferences that *failed* the
character budget (e.g. oversized content) permanently wasted the cap.
Subsequent preferences that *would* have fit were then denied full rendering
because ``preference_full_count`` had already reached the limit.

These are standalone unit tests of ``MemoryStore._parse_viking_memory`` — no
server fixtures, config file, or network access required.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.memory import (
    _TYPE_QUOTA_EVENT_CHAR_RATIO,
    _TYPE_QUOTA_PREFERENCE_FULL_LIMIT,
    MemoryStore,
)

_MEM_ROOT = "viking://user/test_user/memories"


def _preference(index: int, score: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(
        uri=f"{_MEM_ROOT}/preferences/pref_{index}.md",
        score=score,
        abstract=f"preference {index}",
        _recall_type="preferences",
    )


def _type_char_budgets(max_chars: int) -> dict[str, int]:
    """Mirror production: only ``events``/``entities`` carry a per-type budget.

    ``preferences`` is deliberately absent, which is what routes preference
    memories through the ``preference_full_limit`` branch under test.
    """
    event_budget = int(max_chars * _TYPE_QUOTA_EVENT_CHAR_RATIO)
    return {"events": event_budget, "entities": max_chars - event_budget}


def _read_from(contents: dict[str, str]):
    async def fake_read_content(uri: str, level: str = "read") -> str:
        return contents.get(uri, "")

    return fake_read_content


def _rendered_by_uri(rendered: str) -> dict[str, str]:
    """Map ``uri -> rendered mode`` from the ``<memory index=... type=...>`` blocks."""
    modes: dict[str, str] = {}
    for block in rendered.split("<memory index=")[1:]:
        mode = block.split('type="', 1)[1].split('"', 1)[0]
        uri = block.split("<uri>", 1)[1].split("</uri>", 1)[0]
        modes[uri] = mode
    return modes


async def _render(store: MemoryStore, memories, contents, max_chars: int) -> dict[str, str]:
    rendered = await store._parse_viking_memory(
        memories,
        client=None,
        min_score=0.1,
        max_chars=max_chars,
        full_limit=0,
        type_char_budgets=_type_char_budgets(max_chars),
        preference_full_limit=_TYPE_QUOTA_PREFERENCE_FULL_LIMIT,
        include_uri_entries=True,
        read_content=_read_from(contents),
    )
    return _rendered_by_uri(rendered)


@pytest.mark.asyncio
async def test_preference_full_count_only_consumed_by_successful_full_render(tmp_path: Path):
    """The cap must only be consumed by a preference that actually rendered full.

    ``_TYPE_QUOTA_PREFERENCE_FULL_LIMIT`` preferences are returned with content
    so large that the full fragment blows the global ``max_chars`` budget, plus
    two more preferences with small content.  Because the oversized entries
    never rendered as full, they must not consume the cap — the small
    preferences must still render as ``full``.
    """
    limit = _TYPE_QUOTA_PREFERENCE_FULL_LIMIT
    pref_count = limit + 2
    max_chars = 2000

    memories = [_preference(i, score=0.9 - i * 0.01) for i in range(pref_count)]
    contents = {}
    for i in range(pref_count):
        uri = memories[i].uri
        if i < limit:
            # Unique per-memory payload so the content-hash deduper cannot
            # collapse the distinct oversized entries into one.
            contents[uri] = f"oversized preference body {i} " + "x" * 5000
        else:
            contents[uri] = f"short preference body {i}"

    store = MemoryStore(workspace=tmp_path)
    modes = await _render(store, memories, contents, max_chars)

    oversized_uris = {memories[i].uri for i in range(limit)}
    small_uris = {memories[i].uri for i in range(limit, pref_count)}

    assert oversized_uris <= modes.keys(), (
        f"oversized entries dropped: {oversized_uris - modes.keys()}"
    )
    assert small_uris <= modes.keys(), f"small entries dropped: {small_uris - modes.keys()}"

    full_uris = {uri for uri, mode in modes.items() if mode == "full"}
    assert not (full_uris & oversized_uris), (
        f"oversized preference(s) unexpectedly rendered as full: {sorted(full_uris & oversized_uris)}"
    )
    assert len(full_uris) == limit, (
        f"expected exactly {limit} full preference(s), got {sorted(full_uris)}; modes={modes}"
    )
    assert full_uris <= small_uris, (
        f"a full render came from the oversized set: {sorted(full_uris - small_uris)}"
    )


@pytest.mark.asyncio
async def test_preference_full_limit_still_capped_when_full_fits(tmp_path: Path):
    """The cap must still hold when every preference *does* fit the budget.

    Once ``_TYPE_QUOTA_PREFERENCE_FULL_LIMIT`` preferences have rendered as
    full, later preferences must fall back to non-full entries even though
    budget remains.
    """
    limit = _TYPE_QUOTA_PREFERENCE_FULL_LIMIT
    pref_count = limit + 3
    max_chars = 10000

    memories = [_preference(i, score=0.9 - i * 0.01) for i in range(pref_count)]
    contents = {memory.uri: f"short preference body {i}" for i, memory in enumerate(memories)}

    store = MemoryStore(workspace=tmp_path)
    modes = await _render(store, memories, contents, max_chars)

    full_uris = [uri for uri, mode in modes.items() if mode == "full"]
    assert len(full_uris) == limit, (
        f"expected exactly {limit} full preference(s), got {full_uris}; modes={modes}"
    )
    # Preferences are ordered by descending score, so the rendered full entry
    # must be the top-scored preference.
    assert full_uris == [memories[0].uri], f"unexpected full rendering: {full_uris}"
