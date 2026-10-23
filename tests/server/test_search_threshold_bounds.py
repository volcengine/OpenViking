# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Public search thresholds must reject non-finite values before retrieval."""

import math

import pytest
from pydantic import ValidationError

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.routers.search import FindRequest, RecallRequest, SearchRequest
from openviking.server.routers.skills import FindSkillsRequest

REQUEST_FIELDS = (
    (FindRequest, "score_threshold"),
    (SearchRequest, "score_threshold"),
    (RecallRequest, "min_score"),
    (RecallRequest, "score_threshold"),
    (FindSkillsRequest, "score_threshold"),
)


@pytest.mark.parametrize("model,field", REQUEST_FIELDS)
@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
def test_request_models_reject_non_finite_thresholds(model, field, value):
    with pytest.raises(ValidationError, match="finite number"):
        model.model_validate({"query": "q", field: value})


@pytest.mark.parametrize("model,field", REQUEST_FIELDS)
@pytest.mark.parametrize("value", (-2.0, 2.0))
def test_request_models_preserve_unbounded_finite_score_scales(model, field, value):
    request = model.model_validate({"query": "q", field: value})

    assert getattr(request, field) == value


@pytest.mark.parametrize("value", ("1e999", "-1e999", "NaN"))
def test_search_request_rejects_non_finite_json_thresholds(value):
    with pytest.raises(ValidationError, match="finite number"):
        SearchRequest.model_validate_json(f'{{"query":"q","score_threshold":{value}}}')


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ("find", "search"))
@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
async def test_mcp_tools_reject_non_finite_thresholds(tool_name, value):
    with pytest.raises(Exception) as exc_info:
        await mcp_endpoint.mcp.call_tool(tool_name, {"query": "q", "min_score": value})

    message = str(exc_info.value)
    assert "min_score" in message
    assert "finite number" in message
