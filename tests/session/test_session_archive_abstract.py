# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for _extract_abstract_from_summary.

These call the method directly without a runtime Session to avoid importing
the full client / config / storage fixture chain.
"""

import pytest

from openviking.session import Session


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    """Override the conftest autouse fixture that depends on client."""
    yield


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

WM_V2_EXAMPLE = """# Working Memory

## Session Title
OV archive count verification + post-reset pending token investigation

## Current State
Still investigating why the pending token counters reset after a deployment restart.
No root cause identified yet. Next step: check observer metrics dump.

## Task & Goals
Investigate the archive count mismatch between OV internal counters and the FUSE mount.
Verify whether post-reset pending tokens are a regression or expected behavior.

## Key Facts & Decisions
- Archive count from ov status: 97, FUSE mount: 71 (26 missing)
- The difference is reproducible across multiple restarts.
- Hypothesis: some archives are never indexed or the index is stale.

## Files & Context
- /home/ov/data/archives – inspected
- /var/log/openviking/server.log – reviewed for indexer errors

## Errors & Corrections
- Initial assumption: FUSE mount lag. Corrected: mount is fresh but still 26 archives short.

## Open Issues
- Why are 26 archives missing from FUSE?
- Is the pending token reset a bug or an expected cleanup?
"""

LEGACY_STRUCTURED_SUMMARY = """# Session Summary

**One-sentence overview**: The user investigated search recall quality and discovered a 90% result drop with sparse dense models.

## Historical Context Carried Forward
- None
"""

NO_VLM_FALLBACK = """# Session Summary

**Overview**: 5 turns, 12 messages"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_wm_v2_extracts_session_title():
    """The WM v2 format starts with ``# Working Memory`` then
    ``## Session Title`` then the actual title.  The old code returned
    ``# Working Memory`` as the abstract for **every** archive."""
    abstract = Session._extract_abstract_from_summary(None, WM_V2_EXAMPLE)
    assert abstract == "OV archive count verification + post-reset pending token investigation"


def test_legacy_structured_summary_still_works():
    """Regression guard: the regex path for ``**Key**: value`` summaries
    must not be changed."""
    abstract = Session._extract_abstract_from_summary(None, LEGACY_STRUCTURED_SUMMARY)
    assert "search recall" in abstract


def test_no_vlm_fallback_extracts_overview_value():
    """When the VLM is unavailable the code generates a simple
    ``**Overview**: N turns, M messages`` summary.  The regex path
    should extract the value after the colon."""
    abstract = Session._extract_abstract_from_summary(None, NO_VLM_FALLBACK)
    assert "5 turns" in abstract


def test_empty_summary_returns_empty_string():
    assert Session._extract_abstract_from_summary(None, "") == ""


def test_whitespace_only_returns_empty():
    assert Session._extract_abstract_from_summary(None, "   \n\t\n  ") == ""


def test_only_headings_returns_stripped_heading():
    """If every line is a heading, the fallback strips '#' from the first
    line.  This is a degenerate case — it should return *something*."""
    abstract = Session._extract_abstract_from_summary(None, "# Just a heading")
    assert abstract == "Just a heading"


def test_template_placeholder_line_skipped():
    """The ov_wm_v2 template wraps section descriptions in ``_..._``.
    If the model leaves them unchanged, they should be skipped."""
    abstract = Session._extract_abstract_from_summary(
        None,
        "# Working Memory\n"
        "_A short and distinctive 5-10 word descriptive title._\n"
        "Real session title goes here\n"
        "## Current State\n",
    )
    assert abstract == "Real session title goes here"
