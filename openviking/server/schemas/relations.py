# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/relations endpoints."""

from typing import List, Union

from pydantic import BaseModel, ConfigDict, Field


class RelationEntry(BaseModel):
    """Single relation tuple emitted by ``service.relations.relations()``."""

    model_config = ConfigDict(extra="allow")

    uri: str
    reason: str = ""


class LinkResult(BaseModel):
    """``{"from": str, "to": str|List[str]}`` payload of ``POST /relations/link``.

    ``to`` is polymorphic because the request accepts ``to_uris: Union[str,
    List[str]]`` and the response echoes it unchanged. For ``DELETE
    /relations/link`` (unlink) the reply uses :class:`FromTo` from
    ``schemas.filesystem`` because ``to`` is always a single string there.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(..., alias="from")
    to: Union[str, List[str]]
