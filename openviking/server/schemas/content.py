# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/content endpoints."""

from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, ConfigDict

# GET /read returns the deserialized content which can be either:
# - a plain text string (the file contents)
# - a dict (parsed memory JSON from ``deserialize_content``)
# Keep the union explicit so SDK consumers know to branch.
ContentReadResult = Union[str, Dict[str, Any]]


class ContentWriteResult(BaseModel):
    """Result of ``service.fs.write()`` applied to the content endpoint.

    ``extra='allow'`` protects against silent drop of future fields added
    to the write service.
    """

    model_config = ConfigDict(extra="allow")

    uri: str
    root_uri: Optional[str] = None
    context_type: Optional[str] = None
    mode: Optional[str] = None
    written_bytes: Optional[int] = None
    semantic_updated: Optional[bool] = None
    vector_updated: Optional[bool] = None
    queue_status: Optional[Dict[str, Any]] = None


class ReindexResult(BaseModel):
    """Result of ``POST /api/v1/content/reindex``.

    The shape depends on the ``wait`` request parameter:

    - ``wait=True``: dict from ``service.resources.summarize`` or
      ``build_index`` — dynamic per processor output, captured as extras.
    - ``wait=False``: ``{uri, status, task_id, message}`` acknowledgement.

    Fields are Optional to cover both paths; ``extra='allow'`` forwards
    processor-specific fields from the sync path.
    """

    model_config = ConfigDict(extra="allow")

    uri: Optional[str] = None
    status: Optional[str] = None
    task_id: Optional[str] = None
    message: Optional[str] = None
