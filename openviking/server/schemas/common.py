# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Common response primitives reused across routers."""

from typing import Any, Generic, List, Optional, TypeVar

from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

T = TypeVar("T")


class Pagination(BaseModel):
    """Pagination metadata."""

    total: int = Field(..., description="Total number of items matching the query")
    offset: int = Field(0, description="Offset used for the current page")
    limit: int = Field(..., description="Page size used for the current page")
    has_more: bool = Field(False, description="Whether more items are available")


class PaginatedResult(BaseModel, Generic[T]):
    """Paginated payload wrapped as the ``result`` field of ``Response``.

    Intended usage: ``Response[PaginatedResult[SessionInfo]]``.
    """

    items: List[T] = Field(default_factory=list, description="Items in the current page")
    pagination: Optional[Pagination] = Field(
        default=None,
        description="Pagination metadata; omitted for non-paginated list endpoints",
    )


class ExcludeNoneRoute(APIRoute):
    """APIRoute variant that forces ``response_model_exclude_none=True``.

    Business routers opt in via ``APIRouter(route_class=ExcludeNoneRoute)`` to
    get a unified null-handling policy: ``None`` fields are omitted from JSON
    output, matching the historical behavior of endpoints that previously used
    ``.model_dump(exclude_none=True)``.

    FastAPI's route decorators always pass ``response_model_exclude_none``
    explicitly (default ``False``), so this class overrides the value rather
    than relying on ``setdefault``. If a single endpoint needs to emit
    ``null`` fields, move it to a router without this route class.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["response_model_exclude_none"] = True
        super().__init__(*args, **kwargs)
