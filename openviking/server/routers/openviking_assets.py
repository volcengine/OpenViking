# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP endpoint for resolving OpenViking Assets configuration."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext
from openviking.server.openviking_assets import (
    preflight_git_repository,
    resolve_openviking_assets,
)
from openviking.server.responses import response_from_result

router = APIRouter(prefix="/api/v1/openviking-assets", tags=["openviking-assets"])


class ResolveOpenVikingAssetsRequest(BaseModel):
    """Raw YAML inputs for resolving one flat manifest, with an optional separate catalog."""

    model_config = ConfigDict(extra="forbid")

    manifest_yaml: str = Field(min_length=1, max_length=4_000_000)
    catalog_yaml: str | None = Field(default=None, min_length=1, max_length=4_000_000)
    manifest_label: str = Field(default="manifest.yaml", min_length=1, max_length=1024)
    catalog_label: str = Field(default="catalog.yaml", min_length=1, max_length=1024)


class GitAuthConfig(BaseModel):
    """One-shot Git credentials; SecretStr keeps the token out of model reprs."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(default="oauth2", min_length=1, max_length=256)
    token: SecretStr | None = None


class PreflightOpenVikingAssetRequest(BaseModel):
    """One resolved asset whose source access must be verified before apply."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    connector: Literal["git"]
    repo_url: str = Field(min_length=1, max_length=8192)
    branch: str | None = Field(default=None, min_length=1, max_length=1024)
    commit: str | None = Field(default=None, min_length=1, max_length=40)
    auth_config: GitAuthConfig | None = None


@router.post("/resolve")
async def resolve_assets(
    request: ResolveOpenVikingAssetsRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Parse and validate configuration without submitting resources."""

    result = resolve_openviking_assets(
        manifest_yaml=request.manifest_yaml,
        catalog_yaml=request.catalog_yaml,
        manifest_label=request.manifest_label,
        catalog_label=request.catalog_label,
        ctx=ctx,
    )
    return response_from_result(result.model_dump())


@router.post("/preflight")
async def preflight_asset(
    request: PreflightOpenVikingAssetRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Verify source readability without cloning, importing, or creating a task."""

    auth = request.auth_config
    result = await preflight_git_repository(
        asset_name=request.name,
        repo_url=request.repo_url,
        branch=request.branch,
        commit=request.commit,
        username=auth.username if auth else None,
        token=auth.token.get_secret_value() if auth and auth.token else None,
    )
    return response_from_result(result)
