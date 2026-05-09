# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/fs endpoints."""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class FileStat(BaseModel):
    """Shape of ``service.fs.stat()`` output.

    Also used as the element type of detailed ``ls``/``tree`` list outputs
    (``simple=False``). The field set is the union across ``output=original``
    and ``output=agent`` modes; unset fields are omitted via
    ``ExcludeNoneRoute``. ``extra='allow'`` carries through AGFS-specific
    metadata (e.g. resource ``tags``).
    """

    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    size: Optional[int] = None
    mode: Optional[int] = None
    modTime: Optional[str] = None
    isDir: Optional[bool] = None
    meta: Optional[Dict[str, Any]] = None
    uri: Optional[str] = None
    rel_path: Optional[str] = None
    abstract: Optional[str] = None
    tags: Optional[str] = None


# ``GET /ls`` and ``GET /tree`` are polymorphic:
# - ``simple=True``: ``List[str]`` (URI strings only)
# - otherwise: ``List[FileStat]`` (detailed entries)
# The union keeps the OpenAPI schema honest so SDK consumers can branch
# on the runtime type.
FSListResult = Union[List[str], List[FileStat]]


class FromTo(BaseModel):
    """``{"from": str, "to": str}`` payload used by ``mv`` and ``unlink``.

    Uses ``alias='from'`` because ``from`` is a Python keyword.
    ``populate_by_name`` lets callers construct via either ``from_`` or
    ``from`` without friction. FastAPI serializes with the alias by
    default, so the JSON key stays ``"from"``.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(..., alias="from")
    to: str
