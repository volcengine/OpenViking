"""Regression test: _gh_slug must match GitHub's heading-anchor algorithm.

GitHub's reference slugger (github-slugger) lowercases, strips a set of
punctuation, then maps *each* space to a hyphen individually
(``.replace(/ /g, '-')``) — it does not collapse consecutive whitespace.

Because punctuation is removed *before* spaces are converted, a heading like
"Foo & Bar" leaves two adjacent spaces where "&" was, which GitHub renders as
a double hyphen ("foo--bar"). The intra-document link rewriter compares
_gh_slug(heading) against the link fragment, so collapsing whitespace made
links such as `guide.md#foo--bar` fail to resolve.
"""

from openviking.parse.parsers.markdown import _gh_slug


def test_gh_slug_matches_github_for_removed_punctuation():
    # "&" removed -> "foo  bar" (two spaces) -> "foo--bar" on GitHub.
    assert _gh_slug("Foo & Bar") == "foo--bar"
    assert _gh_slug("Terms & Conditions") == "terms--conditions"


def test_gh_slug_simple_headings_unchanged():
    assert _gh_slug("Hello World") == "hello-world"
    assert _gh_slug("Section 1") == "section-1"
    assert _gh_slug("C++ Guide") == "c-guide"
