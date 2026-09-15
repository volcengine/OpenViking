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
    "exc",
    [
        NotFoundError(URI, "file"),
        PermissionDeniedError("access denied"),
        RuntimeError("vector store unreachable"),
    ],
    ids=["not-found", "permission-denied", "backend-error"],
)
async def test_a_failure_is_not_reported_as_no_matches(exc):
    async def grep(uri, pattern, **kwargs):
        raise exc

    _install(grep)

    out = await mcp_endpoint.grep(uri=URI, pattern="anything")

    assert "No matches found" not in out
    assert type(exc).__name__ in out


@pytest.mark.asyncio
async def test_a_genuine_miss_still_reads_as_no_matches():
    async def grep(uri, pattern, **kwargs):
        return {"matches": []}

    _install(grep)

    assert await mcp_endpoint.grep(uri=URI, pattern="anything") == (
        "No matches found for pattern(s): anything"
    )


@pytest.mark.asyncio
async def test_one_failing_pattern_does_not_cost_the_others_their_matches():
    async def grep(uri, pattern, **kwargs):
        if pattern == "bad":
            raise RuntimeError("boom")
        return {"matches": [{"uri": f"{URI}/a.md", "line": 3, "content": "hit"}]}

    _install(grep)

    out = await mcp_endpoint.grep(uri=URI, pattern=["good", "bad"])

    assert "hit" in out and "L3" in out
    assert "Patterns that could not be searched:" in out
    assert "bad: RuntimeError: boom" in out


@pytest.mark.asyncio
async def test_every_pattern_failing_reports_each_one():
    async def grep(uri, pattern, **kwargs):
        raise RuntimeError(f"boom-{pattern}")

    _install(grep)

    out = await mcp_endpoint.grep(uri=URI, pattern=["one", "two"])

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
