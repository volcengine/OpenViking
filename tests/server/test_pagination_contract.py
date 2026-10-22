# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from pydantic import ValidationError

from openviking.server.app import _install_openapi_generator
from openviking.server.models import CursorPage, OffsetPage, PageResponse
from openviking.server.pagination import (
    AuditOffsetPagination,
    FilesystemOffsetPagination,
    LargeOffsetPagination,
    OffsetPagination,
    offset_pagination,
)


def test_offset_page_serializes_unknown_total_as_null():
    response = PageResponse[OffsetPage[str]](
        result=OffsetPage(
            items=["one"],
            offset=0,
            limit=50,
            has_more=False,
            total=None,
        )
    )

    assert response.model_dump() == {
        "status": "ok",
        "result": {
            "items": ["one"],
            "offset": 0,
            "limit": 50,
            "has_more": False,
            "total": None,
        },
        "error": None,
        "telemetry": None,
        "profile": None,
    }


def test_cursor_page_response_requires_result_and_rejects_error_status():
    with pytest.raises(ValidationError):
        PageResponse[CursorPage[str]]()

    with pytest.raises(ValidationError):
        PageResponse[CursorPage[str]](
            status="error",
            result=CursorPage(
                items=[],
                limit=50,
                has_more=False,
                next_cursor=None,
            ),
        )


@pytest.mark.parametrize(
    ("model", "valid_limit", "invalid_limit"),
    [
        (OffsetPagination, 200, 201),
        (LargeOffsetPagination, 1000, 1001),
        (AuditOffsetPagination, 100, 101),
    ],
)
def test_pagination_models_enforce_endpoint_limits(model, valid_limit, invalid_limit):
    assert model(limit=valid_limit).limit == valid_limit
    with pytest.raises(ValidationError):
        model(limit=invalid_limit)


def test_filesystem_pagination_has_no_upper_limit():
    assert FilesystemOffsetPagination(limit=100_000).limit == 100_000


def test_openapi_rewrites_only_automatic_validation_422():
    app = FastAPI()

    @app.get("/auto")
    async def auto(pagination: Annotated[OffsetPagination, Depends(offset_pagination)]):
        return pagination

    @app.get("/business", responses={422: {"description": "Business conflict"}})
    async def business():
        return {}

    _install_openapi_generator(app)
    schema = app.openapi()

    auto_responses = schema["paths"]["/auto"]["get"]["responses"]
    assert "422" not in auto_responses
    assert auto_responses["400"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    assert schema["components"]["schemas"]["ErrorResponse"]["properties"]["error"] == {
        "$ref": "#/components/schemas/ErrorInfo"
    }
    assert schema["paths"]["/business"]["get"]["responses"]["422"]["description"] == (
        "Business conflict"
    )
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ValidationError" not in schema["components"]["schemas"]


def _resolve_schema(schema: dict, document: dict) -> dict:
    while "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


async def test_target_list_endpoints_publish_concrete_page_responses(app):
    document = app.openapi()
    expected_result_fields = {
        "/api/v1/admin/accounts": {"items", "offset", "limit", "has_more", "total"},
        "/api/v1/admin/accounts/{account_id}/users": {
            "items",
            "offset",
            "limit",
            "has_more",
            "total",
            "account_total",
            "manager_count",
            "key_count",
        },
        "/api/v1/console/audit": {
            "items",
            "offset",
            "limit",
            "has_more",
            "total",
            "success_rate",
        },
        "/api/v1/fs/ls": {"items", "offset", "limit", "has_more", "total"},
        "/api/v1/fs/tree": {"items", "offset", "limit", "has_more", "total"},
        "/api/v1/agent-evolution/experiences/trajectories": {
            "items",
            "offset",
            "limit",
            "has_more",
            "total",
            "experience_uri",
        },
        "/api/v1/tasks": {"items", "limit", "has_more", "next_cursor"},
        "/api/v1/skills": {
            "items",
            "offset",
            "limit",
            "has_more",
            "total",
            "root_uris",
        },
        "/api/v1/sessions": {"items", "offset", "limit", "has_more", "total"},
        "/api/v1/watches": {"items", "offset", "limit", "has_more", "total"},
    }

    for path, expected_fields in expected_result_fields.items():
        response_schema = document["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        envelope = _resolve_schema(response_schema, document)
        assert envelope != {}
        result = _resolve_schema(envelope["properties"]["result"], document)
        assert expected_fields <= set(result["properties"])
