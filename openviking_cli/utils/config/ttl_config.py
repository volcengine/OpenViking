# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Directory TTL (time-to-live) recycling configuration.

TTL execution is provided by the optional, default-off Redis implementation in
``openviking.ttl``. The runtime configuration manager publishes policies at the
cluster (库/实例) and account (数据空间) scopes. No Redis-free backend is provided.

A policy value is either a positive integer number of days or the literal
``"disabled"``. An absent node means "inherit"; ``"disabled"`` stops the
fallback at that node. Effective-policy selection (per-node account-over-cluster
merge, then specific-root > type-default > ``global`` > default-off) lives in
:mod:`openviking.config.ttl` so this module stays a pure data shape.
"""

from __future__ import annotations

import re
from typing import Annotated, Dict, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .runtime_field import RuntimeField

#: A single TTL policy: a positive day count or an explicit ``disabled``.
TTLPolicy = Union[Annotated[int, Field(strict=True, ge=1, le=365000)], Literal["disabled"]]


class TTLConfig(BaseModel):
    """Declarative TTL policy surface shared by cluster and account configs.

    Every field is a :func:`RuntimeField` so the config API can PATCH it, and
    every field defaults to ``None`` (unset / inherit) so an untouched document
    keeps TTL fully disabled. ``global_`` is exposed to the API and persisted
    under its ``global`` alias because ``global`` is a reserved word in Python.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    global_: Optional[TTLPolicy] = RuntimeField(default=None, alias="global")
    user_events: Optional[TTLPolicy] = RuntimeField(default=None)
    peer_events: Optional[TTLPolicy] = RuntimeField(default=None)
    sessions: Optional[TTLPolicy] = RuntimeField(default=None)
    roots: Optional[Dict[str, TTLPolicy]] = RuntimeField(default=None)

    @model_validator(mode="after")
    def _validate_roots(self) -> "TTLConfig":
        for uri in self.roots or {}:
            if (
                not re.fullmatch(
                    r"viking://user/[^/]+/(?:memories/events|peers/[^/]+/memories/events|sessions)",
                    uri,
                )
                or any(part in {".", ".."} for part in uri.split("/"))
                or any(char in uri for char in "*?[]")
            ):
                raise ValueError("TTL roots must be canonical Events or Sessions roots")
        return self


__all__ = ["TTLConfig", "TTLPolicy"]
