# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""``grep`` must not report a failure as "no matches".

The tool fans out over its patterns and used to swallow every per-pattern exception into
an empty match list, which the tail then rendered as ``No matches found``. A missing URI,
a permission denial, a malformed regex and an unreachable backend were therefore
indistinguishable from a search that genuinely found nothing, so the caller concluded the
content was not there.

``POST /api/v1/search/grep`` maps the exception and re-raises it, so the two faces
disagreed about whether a failure is a result.

The fan-out is kept: one bad pattern must not cost the others their matches. What changes
is that the reason travels with the empty list instead of being dropped.
"""

from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import NotFoundError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier

CTX = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)
URI = "viking://user/test_user/memories"


def _install(grep):
    set_service(SimpleNamespace(fs=SimpleNamespace(grep=grep)))


@pytest.fixture(autouse=True)
def _identity():
    token = mcp_endpoint._mcp_ctx.set(CTX)
    yield
    mcp_endpoint._mcp_ctx.reset(token)
    set_service(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (NotFoundError(URI, "file"), "NOT_FOUND"),
        (PermissionDeniedError("access denied"), "PERMISSION_DENIED"),
        (RuntimeError("vector store unreachable"), "RuntimeError"),
    ],
    ids=["not-found", "permission-denied", "backend-error"],
)
async def test_a_failure_is_not_reported_as_no_matches(exc, code):
    async def grep(uri, pattern, **kwargs):
        raise exc

    _install(grep)

    result = await mcp_endpoint.mcp.call_tool("grep", {"uri": URI, "pattern": "anything"})
    assert isinstance(result, CallToolResult) and result.isError
    out = result.content[0].text
    assert "No matches found" not in out
    assert f"anything: {code}: {exc}" in out


@pytest.mark.asyncio
async def test_every_pattern_failing_reports_each_one():
    async def grep(uri, pattern, **kwargs):
        raise RuntimeError(f"boom-{pattern}")

    _install(grep)

    result = await mcp_endpoint.mcp.call_tool("grep", {"uri": URI, "pattern": ["one", "two"]})
    assert isinstance(result, CallToolResult) and result.isError
    out = result.content[0].text
    assert out.startswith("grep failed for every pattern:")
    assert "one: RuntimeError: boom-one" in out
    assert "two: RuntimeError: boom-two" in out


@pytest.mark.asyncio
async def test_a_clean_multi_pattern_search_is_unchanged():
    async def grep(uri, pattern, **kwargs):
        return {"matches": [{"uri": f"{URI}/a.md", "line": 1, "content": pattern}]}

    _install(grep)

    out = await mcp_endpoint.grep(uri=URI, pattern=["x", "y"])

    assert out.startswith("Found 2 match(es) across 2 pattern(s):")
    assert "could not be searched" not in out


@pytest.mark.asyncio
async def test_grep_names_only_patterns_that_reach_the_limit():
    async def grep(uri, pattern, **kwargs):
        assert kwargs["node_limit"] == 2
        count = 2 if pattern == "capped" else 1
        return {
            "matches": [
                {"uri": f"{URI}/a.md", "line": index + 1, "content": pattern}
                for index in range(count)
            ],
        }

    _install(grep)

    out = await mcp_endpoint.grep(uri=URI, pattern=["capped", "complete"], node_limit=2)

    assert "Result limit reached (2 matches per pattern)" in out
    assert "more matches may exist" in out
    assert "Capped patterns: capped\n" in out
    assert "[complete]: complete" in out


@pytest.mark.asyncio
@pytest.mark.parametrize("visible_count", [0, 1])
async def test_grep_preserves_backend_limit_after_permission_filtering(visible_count):
    async def grep(uri, pattern, **kwargs):
        return {
            "matches": [
                {"uri": f"{URI}/a.md", "line": 1, "content": "visible hit"}
                for _ in range(visible_count)
            ],
            "limit_reached": True,
        }

    _install(grep)

    out = await mcp_endpoint.grep(uri=URI, pattern="filtered")

    assert "Capped patterns: filtered" in out
    assert "more matches may exist" in out
    if visible_count:
        assert "Found 1 match(es)" in out
        assert "visible hit" in out
    else:
        assert out.startswith("No matches returned for pattern(s): filtered")


@pytest.mark.asyncio
@pytest.mark.parametrize("visible_count", [0, 1])
async def test_grep_reports_candidate_limit_only_for_affected_pattern(visible_count):
    async def grep(uri, pattern, **kwargs):
        if pattern == "candidate-capped":
            return {"matches": [], "candidate_limit_reached": True}
        return {
            "matches": [{"uri": f"{URI}/a.md", "line": 1, "content": "hit"}]
            if visible_count
            else []
        }

    _install(grep)

    out = await mcp_endpoint.grep(uri=URI, pattern=["candidate-capped", "complete"])

    assert "Candidate search may have been capped for pattern(s): candidate-capped." in out
    assert "Result limit reached" not in out
    if visible_count:
        assert "L1 [complete]: hit" in out
    else:
        assert out.startswith("No matches returned for pattern(s): candidate-capped, complete")


@pytest.mark.asyncio
async def test_grep_reports_matches_limits_and_errors_for_separate_patterns():
    async def grep(uri, pattern, **kwargs):
        if pattern == "broken":
            raise RuntimeError("boom")
        if pattern == "capped":
            return {"matches": [], "limit_reached": True}
        return {"matches": [{"uri": f"{URI}/a.md", "line": 3, "content": "hit"}]}

    _install(grep)

    out = await mcp_endpoint.grep(uri=URI, pattern=["complete", "capped", "broken"])

    assert out.startswith("Found 1 match(es) across 3 pattern(s):")
    assert "L3 [complete]: hit" in out
    assert "Capped patterns: capped\n" in out
    assert "Patterns that could not be searched:\n  broken: RuntimeError: boom" in out
