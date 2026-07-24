# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP endpoint for resolving OpenViking Assets configuration."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext
from openviking.server.openviking_assets import resolve_openviking_assets
from openviking.server.responses import response_from_result

router = APIRouter(prefix="/api/v1/openviking-assets", tags=["openviking-assets"])


class ResolveOpenVikingAssetsRequest(BaseModel):
    """Raw YAML inputs for resolving one flat manifest against one catalog."""

    model_config = ConfigDict(extra="forbid")

    manifest_yaml: str = Field(min_length=1, max_length=1_000_000)
    catalog_yaml: str = Field(min_length=1, max_length=4_000_000)
    manifest_label: str = Field(default="manifest.yaml", min_length=1, max_length=1024)
    catalog_label: str = Field(default="assets.yaml", min_length=1, max_length=1024)


@router.post("/resolve")
async def resolve_assets(
    request: ResolveOpenVikingAssetsRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Parse and validate configuration without submitting resources."""

    result = resolve_openviking_assets(
        manifest_yaml=request.manifest_yaml,
        catalog_yaml=request.catalog_yaml,
        manifest_label=request.manifest_label,
        catalog_label=request.catalog_label,
    )
    return response_from_result(result.model_dump())
