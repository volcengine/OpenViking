# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/resources and /api/v1/skills endpoints."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class QueueStatus(BaseModel):
    """Shape of the ``queue_status`` sub-dict inside add_resource / add_skill.

    Populated only when the caller set ``wait=True`` so the processor can
    report per-item progress / errors synchronously.
    """

    model_config = ConfigDict(extra="allow")

    processed: Optional[int] = None
    error_count: Optional[int] = None
    errors: Optional[List[str]] = None


class TempUploadResult(BaseModel):
    """Trivial ``{"temp_file_id": str}`` payload of ``POST /resources/temp_upload``."""

    temp_file_id: str


class AddResourceResult(BaseModel):
    """Result of ``POST /api/v1/resources`` wrapping ``service.resources.add_resource``.

    ``extra='allow'`` preserves any processor field not yet modeled here;
    add_resource evolves as new resource types are added.
    """

    model_config = ConfigDict(extra="allow")

    status: Optional[str] = None
    errors: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
    source_path: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    root_uri: Optional[str] = None
    temp_uri: Optional[str] = None
    queue_status: Optional[QueueStatus] = None


class AddSkillResult(BaseModel):
    """Result of ``POST /api/v1/skills`` wrapping ``service.resources.add_skill``."""

    model_config = ConfigDict(extra="allow")

    status: Optional[str] = None
    uri: Optional[str] = None
    name: Optional[str] = None
    auxiliary_files: Optional[int] = None
    queue_status: Optional[QueueStatus] = None
