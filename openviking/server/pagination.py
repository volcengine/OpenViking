# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared pagination request contracts."""

from collections.abc import Mapping

from fastapi import Query, Request
from pydantic import BaseModel, Field

from openviking_cli.exceptions import InvalidArgumentError

DEFAULT_PAGE_LIMIT = 50
DEFAULT_MAX_PAGE_LIMIT = 200
LARGE_MAX_PAGE_LIMIT = 1000


class OffsetPagination(BaseModel):
    """Default offset pagination."""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=DEFAULT_MAX_PAGE_LIMIT)


class LargeOffsetPagination(BaseModel):
    """Offset pagination for endpoints that allow larger pages."""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=LARGE_MAX_PAGE_LIMIT)


class FilesystemOffsetPagination(BaseModel):
    """Offset pagination without an endpoint-level upper bound."""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1)


class AuditOffsetPagination(BaseModel):
    """Audit pagination with its existing 100-item upper bound."""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=100)


def offset_pagination(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=DEFAULT_MAX_PAGE_LIMIT),
) -> OffsetPagination:
    return OffsetPagination(offset=offset, limit=limit)


def large_offset_pagination(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=LARGE_MAX_PAGE_LIMIT),
) -> LargeOffsetPagination:
    return LargeOffsetPagination(offset=offset, limit=limit)


def filesystem_offset_pagination(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1),
) -> FilesystemOffsetPagination:
    return FilesystemOffsetPagination(offset=offset, limit=limit)


def audit_offset_pagination(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=100),
) -> AuditOffsetPagination:
    return AuditOffsetPagination(offset=offset, limit=limit)


def reject_legacy_pagination_params(
    request: Request,
    forbidden_names: Mapping[str, str],
) -> None:
    """Reject removed pagination parameters instead of silently ignoring them."""
    rejected = sorted(name for name in forbidden_names if name in request.query_params)
    if not rejected:
        return

    replacements = {name: forbidden_names[name] for name in rejected}
    raise InvalidArgumentError(
        f"Unsupported pagination parameter(s): {', '.join(rejected)}",
        details={
            "unsupported_parameters": rejected,
            "replacements": replacements,
        },
    )
