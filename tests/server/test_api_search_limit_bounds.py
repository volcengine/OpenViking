# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for #4289 (search half): unbounded limits on /api/v1/search.

`/find`, `/grep`, and `/glob` take their limits through POST body models rather
than `Query`, so the bounds added to the filesystem router do not reach them.
`_resolve_search_limit` prefers `node_limit` over `limit`, which means bounding
only one of the pair leaves the other as a documented way around it.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openviking.server.routers.search import (
    MAX_LEVEL_LIMIT,
    MAX_NODE_LIMIT,
    MAX_SEARCH_LIMIT,
    FindRequest,
    GlobRequest,
    GrepRequest,
    _resolve_search_limit,
)
from openviking.service.fs_service import FSService
from openviking.service.session_service import SessionService
from openviking.storage.viking_fs import VikingFS
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


DEFAULTS = {
    FindRequest: {"query": "x"},
    GrepRequest: {"uri": "viking://resources/x", "pattern": "p"},
    GlobRequest: {"pattern": "*"},
}


def _build(model, **overrides):
    return model(**{**DEFAULTS[model], **overrides})


# ── Rejected ──


@pytest.mark.parametrize("field", ["limit", "node_limit"])
@pytest.mark.parametrize("value", [MAX_SEARCH_LIMIT + 1, 1_000_000, 0, -1])
def test_find_rejects_out_of_range_result_counts(field, value):
    with pytest.raises(ValidationError):
        _build(FindRequest, **{field: value})


@pytest.mark.parametrize("model", [GrepRequest, GlobRequest])
@pytest.mark.parametrize("value", [MAX_NODE_LIMIT + 1, 1_000_000, 0, -1])
def test_traversals_reject_out_of_range_node_limits(model, value):
    with pytest.raises(ValidationError):
        _build(model, node_limit=value)


@pytest.mark.parametrize("value", [MAX_LEVEL_LIMIT + 1, 100, 0, -1])
def test_grep_rejects_out_of_range_level_limits(value):
    with pytest.raises(ValidationError):
        _build(GrepRequest, level_limit=value)


# ── Accepted ──


@pytest.mark.parametrize("value", [1, 10, 50, MAX_SEARCH_LIMIT])
def test_find_accepts_what_real_callers_ask_for(value):
    """The plugins in examples/ ask for 1-50; 10 is DEFAULT_LIMIT."""
    assert _build(FindRequest, limit=value).limit == value
    assert _build(FindRequest, node_limit=value).node_limit == value


@pytest.mark.parametrize("model", [GrepRequest, GlobRequest])
def test_traversals_accept_their_defaults_and_their_ceiling(model):
    assert _build(model).node_limit == 256
    assert _build(model, node_limit=MAX_NODE_LIMIT).node_limit == MAX_NODE_LIMIT
    assert _build(model, node_limit=None).node_limit is None


def test_grep_accepts_its_default_and_ceiling_depth():
    assert _build(GrepRequest).level_limit == 10
    assert _build(GrepRequest, level_limit=MAX_LEVEL_LIMIT).level_limit == MAX_LEVEL_LIMIT


# ── The pair has to be bounded together ──


def test_node_limit_is_the_one_that_wins():
    """Why bounding only `limit` would not be enough."""
    assert _resolve_search_limit(10, 500) == 500
    assert _resolve_search_limit(10, None) == 10


def test_ceilings_stay_above_every_default():
    """A ceiling below its own default would reject the no-argument request."""
    assert MAX_SEARCH_LIMIT > _build(FindRequest).limit
    assert MAX_NODE_LIMIT > 256
    assert MAX_LEVEL_LIMIT > 10


# ── Over HTTP ──


async def test_http_find_rejects_an_out_of_range_limit(client, service):
    response = await client.post(
        "/api/v1/search/find", json={"query": "x", "limit": 1_000_000}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_http_grep_rejects_an_out_of_range_node_limit(client, service):
    response = await client.post(
        "/api/v1/search/grep",
        json={"uri": "viking://resources", "pattern": "p", "node_limit": 1_000_000},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_http_glob_rejects_an_out_of_range_node_limit(client, service):
    response = await client.post(
        "/api/v1/search/glob", json={"pattern": "*", "node_limit": 1_000_000}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
