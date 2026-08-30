# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for #4289: unbounded listing limits on /api/v1/fs.

`node_limit`, `limit`, `level_limit`, and `abs_limit` were declared with a
default but no `ge`/`le`, so one authenticated request could ask the server to
collect and serialize an arbitrarily large tree in memory. These assert the
ceilings are enforced by request validation -- before the handler runs, so no
listing work is done -- and that every value the shipped clients actually use
still passes.
"""

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers.filesystem import (
    MAX_ABS_LIMIT,
    MAX_LEVEL_LIMIT,
    MAX_NODE_LIMIT,
)
from openviking.service.fs_service import FSService
from openviking.service.session_service import SessionService
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture
def service(temp_dir):
    mock_agfs = MockLocalAGFS(root_path=temp_dir / "mock_agfs_root")
    viking_fs = VikingFS(agfs=mock_agfs)
    return SimpleNamespace(
        fs=FSService(viking_fs=viking_fs),
        sessions=SessionService(viking_fs=viking_fs),
        viking_fs=viking_fs,
    )


async def _make_dir(service, uri):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.viking_fs.mkdir(uri, exist_ok=True, ctx=ctx)
    return uri


# ── Rejected: above the ceiling ──


@pytest.mark.parametrize(
    "endpoint,params",
    [
        # The exact shape from the report.
        ("/api/v1/fs/tree", {"node_limit": 1_000_000, "level_limit": 100}),
        ("/api/v1/fs/ls", {"node_limit": MAX_NODE_LIMIT + 1}),
        ("/api/v1/fs/tree", {"node_limit": MAX_NODE_LIMIT + 1}),
        # The alias must be capped too, or it is a way around node_limit.
        ("/api/v1/fs/ls", {"limit": MAX_NODE_LIMIT + 1}),
        ("/api/v1/fs/tree", {"limit": MAX_NODE_LIMIT + 1}),
        ("/api/v1/fs/tree", {"level_limit": MAX_LEVEL_LIMIT + 1}),
        ("/api/v1/fs/ls", {"abs_limit": MAX_ABS_LIMIT + 1}),
        ("/api/v1/fs/tree", {"abs_limit": MAX_ABS_LIMIT + 1}),
    ],
)
async def test_over_ceiling_is_rejected_by_validation(client, service, endpoint, params):
    uri = await _make_dir(service, "viking://resources/limit-bounds")
    response = await client.get(endpoint, params={"uri": uri, **params})
    # The app maps FastAPI's RequestValidationError onto 400/INVALID_ARGUMENT.
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "endpoint,params",
    [
        ("/api/v1/fs/ls", {"node_limit": 0}),
        ("/api/v1/fs/ls", {"node_limit": -1}),
        ("/api/v1/fs/ls", {"limit": 0}),
        ("/api/v1/fs/tree", {"level_limit": 0}),
        ("/api/v1/fs/ls", {"abs_limit": -1}),
    ],
)
async def test_non_positive_limits_are_rejected(client, service, endpoint, params):
    uri = await _make_dir(service, "viking://resources/limit-bounds-low")
    response = await client.get(endpoint, params={"uri": uri, **params})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


# ── Accepted: everything real callers ask for ──


@pytest.mark.parametrize(
    "endpoint,params",
    [
        # Defaults.
        ("/api/v1/fs/ls", {}),
        ("/api/v1/fs/tree", {}),
        # What the MCP tools and the agent file tool ask for.
        ("/api/v1/fs/ls", {"node_limit": 1000}),
        ("/api/v1/fs/tree", {"node_limit": 1000, "level_limit": 3}),
        # What the WebDAV PROPFIND path asks for, and the ceilings themselves.
        ("/api/v1/fs/ls", {"node_limit": 10_000}),
        ("/api/v1/fs/ls", {"node_limit": MAX_NODE_LIMIT, "abs_limit": MAX_ABS_LIMIT}),
        ("/api/v1/fs/tree", {"node_limit": MAX_NODE_LIMIT, "level_limit": MAX_LEVEL_LIMIT}),
        ("/api/v1/fs/ls", {"limit": MAX_NODE_LIMIT}),
        # abs_limit=0 stays allowed: an empty abstract cannot cost anything.
        ("/api/v1/fs/ls", {"abs_limit": 0}),
    ],
)
async def test_values_real_callers_use_are_accepted(client, service, endpoint, params):
    uri = await _make_dir(service, "viking://resources/limit-bounds-ok")
    response = await client.get(endpoint, params={"uri": uri, "output": "original", **params})
    # The assertion is about validation, not about the handler: the request must
    # reach it. (`/tree` returns 500 under MockLocalAGFS on `main` as well, with
    # or without limits, so asserting 200 there would test the mock, not the cap.)
    assert response.status_code != 400, response.text
    if endpoint.endswith("/ls"):
        assert response.status_code == 200, response.text


async def test_listing_still_truncates_at_the_requested_limit(client, service):
    """The cap is a ceiling on the request, not a change to listing behaviour."""
    uri = await _make_dir(service, "viking://resources/limit-bounds-trunc")
    for index in range(12):
        await _make_dir(service, f"{uri}/d-{index:02d}")

    response = await client.get(
        "/api/v1/fs/ls", params={"uri": uri, "output": "original", "node_limit": 5}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["result"]) == 5


async def test_ceilings_stay_above_every_default():
    """A ceiling below its own default would reject the no-argument request."""
    assert MAX_NODE_LIMIT > 1000
    assert MAX_LEVEL_LIMIT > 3
    assert MAX_ABS_LIMIT > 256
