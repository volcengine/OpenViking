# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Artifact relation graph API."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext

router = APIRouter(prefix="/api/openviking", tags=["artifact-relations"])


class GetArtifactRelationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    to: str = Field(alias="To", min_length=1)


@router.post("/GetArtifactRelations")
async def get_artifact_relations(
    body: GetArtifactRelationsRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Return the caller-visible graph for one normalized Compile target."""
    to = validate_request_viking_uri(
        resolve_path_variables(body.to),
        ctx,
        field_name="To",
    ).rstrip("/")
    return await get_service().artifact_relations.get(to, ctx)


__all__ = ["router"]
