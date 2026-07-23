# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end substantive-content gate test (issue #3028).

Unlike ``test_semantic_substantive_gate.py`` (which hands ``_generate_overview``
pre-built summary dicts), this drives the REAL pipeline: real ``.md`` files on
disk -> the real ``_generate_single_file_summary`` -> ``_generate_text_summary``
which reads the file via ``get_viking_fs().read_file(...)`` and runs the real
``has_substantive_content`` gate. The VLM is a spy that records calls, so
"no hallucination" is proven as "spy VLM never awaited on non-substantive input".

How end-to-end this is:
  REAL   file bytes on disk; the read path through _generate_single_file_summary
         -> _generate_text_summary; _detect_file_type; the has_substantive_content
         detector + gate; the real _generate_overview neutral-overview branch; the
         real is_neutral_overview / reindex _is_not_ready_sentinel guards.
  STUBBED (external services only, all documented at their use site):
    - viking_fs.read_file  -> reads the real tmp file with open() (no ragfs/DB/queue).
    - config               -> SimpleNamespace (no config server); mirrors _fake_config
                              from test_semantic_substantive_gate.py.
    - vlm                   -> spy AsyncMock (no model server; the whole point is
                              proving the VLM is bypassed for non-substantive input).
    - resolve_output_language -> "en" (needs real config/language models; only
                              reached AFTER the gate, i.e. downstream of what we test).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.queuefs import semantic_processor as sp
from openviking.storage.queuefs.semantic_processor import (
    SemanticProcessor,
    _neutral_directory_overview,
    is_neutral_overview,
)

_VLM_MARKER = "a real VLM summary"

HEADING_ONLY = "# Example Wiki Page\n## Subheading\n"
FRONTMATTER_ONLY = "---\ntitle: Draft\ndate: 2026-01-01\n---\n\n   \n"
SUBSTANTIVE = "# Install\n\nRun the build script to compile the project.\n"


def _fake_config(vlm):
    # Mirrors test_semantic_substantive_gate.py::_fake_config (no config server).
    return SimpleNamespace(
        vlm=vlm,
        semantic=SimpleNamespace(
            min_substantive_chars=8,
            max_file_content_chars=100000,
            max_overview_prompt_chars=100000,
            overview_batch_size=50,
            overview_max_chars=4000,
            abstract_max_chars=256,
        ),
    )


class _DiskFS:
    """viking_fs stand-in whose read_file opens the REAL tmp file from disk.

    Only the read transport is stubbed (no ragfs/DB/queue) — the file CONTENT is
    100% real and flows unchanged through _generate_text_summary and the gate.
    """

    async def read_file(self, path, ctx=None):
        with open(path, encoding="utf-8") as fh:
            return fh.read()


def _make_spy_vlm(return_value=_VLM_MARKER):
    vlm = MagicMock()
    vlm.is_available.return_value = True
    vlm.get_completion_async = AsyncMock(return_value=return_value)
    return vlm


@pytest.fixture
def wired(monkeypatch):
    """Wire the real SemanticProcessor to the disk FS + spy VLM + fake config."""
    vlm = _make_spy_vlm()
    monkeypatch.setattr(sp, "get_openviking_config", lambda: _fake_config(vlm))
    monkeypatch.setattr(sp, "render_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(sp, "get_viking_fs", lambda: _DiskFS())
    # Downstream of the gate; needs real config/language models otherwise.
    monkeypatch.setattr(
        "openviking.session.memory.utils.language.resolve_output_language",
        lambda *a, **k: "en",
    )
    return SimpleNamespace(processor=SemanticProcessor(), vlm=vlm)


# --------------------------------------------------------------------------- #
# Points 1-3 — real file on disk -> real read path -> real gate
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, content, substantive",
    [
        ("heading_only.md", HEADING_ONLY, False),  # point 1: title-only
        ("frontmatter_only.md", FRONTMATTER_ONLY, False),  # point 2: frontmatter/whitespace
        ("install.md", SUBSTANTIVE, True),  # point 3: genuine content
    ],
)
async def test_gate_on_real_file(wired, tmp_path, name, content, substantive):
    real_path = tmp_path / name
    real_path.write_text(content, encoding="utf-8")

    result = await wired.processor._generate_single_file_summary(str(real_path))

    assert result["has_substantive_content"] is substantive
    if substantive:
        wired.vlm.get_completion_async.assert_awaited_once()
        assert result["summary"] == _VLM_MARKER
    else:
        wired.vlm.get_completion_async.assert_not_awaited()
        assert result["summary"] == ""


# --------------------------------------------------------------------------- #
# Point 4 — directory of only non-substantive files -> neutral overview, no VLM.
# Chains the REAL per-file summaries into the REAL _generate_overview.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_all_nonsubstantive_dir_neutral_overview_e2e(wired, tmp_path):
    (tmp_path / "heading_only.md").write_text(HEADING_ONLY, encoding="utf-8")
    (tmp_path / "frontmatter_only.md").write_text(FRONTMATTER_ONLY, encoding="utf-8")

    summaries = [
        await wired.processor._generate_single_file_summary(str(tmp_path / "heading_only.md")),
        await wired.processor._generate_single_file_summary(str(tmp_path / "frontmatter_only.md")),
    ]
    assert all(s["has_substantive_content"] is False for s in summaries)
    # Per-file gate already skipped the VLM; reset so the overview assertion is clean.
    wired.vlm.get_completion_async.reset_mock()

    dir_uri = "viking://user/u/docs"
    overview = await wired.processor._generate_overview(dir_uri, summaries, [])

    wired.vlm.get_completion_async.assert_not_awaited()
    assert overview == _neutral_directory_overview("docs")
    assert is_neutral_overview(overview) is True

    # Point 5 backstop: the reindex embedding guard refuses to embed it.
    from openviking.service.reindex_executor import (
        _NO_SUBSTANTIVE_CONTENT_SUFFIX,
        _is_not_ready_sentinel,
    )

    assert _is_not_ready_sentinel(overview, _NO_SUBSTANTIVE_CONTENT_SUFFIX) is True


if __name__ == "__main__":
    pytest.main([__file__])
