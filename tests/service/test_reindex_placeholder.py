"""Regression tests for directory-meta placeholder filtering in reindex (issue #2434).

VikingFS.abstract()/overview() render a ``# <uri>`` header followed only by a
``[Directory ... is not ready]`` marker for directories without a generated
``.abstract.md``/``.overview.md``. Before this fix the reindex executor embedded those
placeholders as real ABSTRACT (L0) / OVERVIEW (L1) vectors, so they surfaced in
search/memory-prefetch and displaced genuine memories. Detection is uri-agnostic and
shape-anchored so legitimate directory content is never dropped.
"""

from openviking.service.reindex_executor import (
    _ABSTRACT_NOT_READY_SUFFIX,
    _OVERVIEW_NOT_READY_SUFFIX,
    _is_not_ready_sentinel,
)


def test_real_abstract_sentinel_is_detected():
    rendered = "# viking://memory/projects/foo [Directory abstract is not ready]"
    assert _is_not_ready_sentinel(rendered, _ABSTRACT_NOT_READY_SUFFIX)


def test_real_overview_sentinel_is_detected():
    rendered = "# viking://memory/projects/foo\n\n[Directory overview is not ready]"
    assert _is_not_ready_sentinel(rendered, _OVERVIEW_NOT_READY_SUFFIX)


def test_sentinel_detection_is_uri_agnostic():
    for uri in ("viking://a/b/", "viking://x%20y/z", "viking://"):
        assert _is_not_ready_sentinel(f"# {uri} [Directory abstract is not ready]", _ABSTRACT_NOT_READY_SUFFIX)


def test_content_mentioning_phrase_mid_body_is_preserved():
    real = "# Notes\n\nWe used to show '[Directory abstract is not ready]' here, but now it has content."
    assert not _is_not_ready_sentinel(real, _ABSTRACT_NOT_READY_SUFFIX)


def test_authored_content_ending_with_marker_is_preserved():
    # multi-line authored body that ends with the exact user-facing marker must survive
    real = "# Project notes\n\nCurrent CLI output: [Directory abstract is not ready]"
    assert not _is_not_ready_sentinel(real, _ABSTRACT_NOT_READY_SUFFIX)


def test_real_content_preserved():
    assert not _is_not_ready_sentinel("# Project\n\nReal abstract body.", _ABSTRACT_NOT_READY_SUFFIX)


def test_empty_is_not_a_sentinel():
    assert not _is_not_ready_sentinel("", _ABSTRACT_NOT_READY_SUFFIX)
