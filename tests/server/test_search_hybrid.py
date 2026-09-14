# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-level hybrid flag plumbing for /find and /search."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

FIND = "/api/v1/search/find"
SEARCH = "/api/v1/search/search"


def test_request_models_expose_hybrid():
    """The router handlers read ``request.hybrid``; the models must define it."""
    from openviking.server.routers.search import FindRequest, SearchRequest

    assert FindRequest(query="q").hybrid is None
    assert FindRequest(query="q", hybrid=True).hybrid is True
    assert SearchRequest(query="q", hybrid=False).hybrid is False
    with pytest.raises(ValidationError):
        FindRequest(query="q", hybird=True)


@pytest.mark.parametrize("endpoint", [FIND, SEARCH])
@pytest.mark.parametrize("hybrid", [None, True, False])
@pytest.mark.asyncio
async def test_hybrid_flag_is_accepted(client, endpoint, hybrid):
    body = {"query": "visible"}
    if hybrid is not None:
        body["hybrid"] = hybrid
    response = await client.post(endpoint, json=body)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_unknown_field_is_still_rejected(client):
    response = await client.post(FIND, json={"query": "visible", "hybird": True})
    assert response.status_code == 422
