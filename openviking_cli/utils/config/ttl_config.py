# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Directory TTL (time-to-live) recycling configuration.

TTL execution is provided by the optional, default-off Redis implementation in
``openviking.ttl``. The runtime configuration manager publishes policies at the
cluster (库/实例) and account (数据空间) scopes. No Redis-free backend is provided.

A policy value is either a positive integer number of days or the literal
``"disabled"``. An absent node means "inherit"; ``"disabled"`` stops the
fallback at that node. Effective-policy selection (User > Account > cluster for
each node, then type-default > ``global`` > default-off) lives in
:mod:`openviking.config.ttl` so this module stays a pure data shape.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .runtime_field import RuntimeField

#: A single TTL policy: a positive day count or an explicit ``disabled``.
TTLPolicy = Union[Annotated[int, Field(strict=True, ge=1, le=365000)], Literal["disabled"]]


class TTLConfig(BaseModel):
    """Declarative TTL policy shared by cluster, account and user configs.

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


__all__ = ["TTLConfig", "TTLPolicy"]
