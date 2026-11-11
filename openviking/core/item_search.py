# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public Item Search request and response models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class SearchAsItemOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregation: Literal["max", "sum"] = "max"


@dataclass
class ItemSearchResult:
    items: list[dict[str, Any]]

    @property
    def total(self) -> int:
        return len(self.items)

    def to_dict(self, **_: Any) -> dict[str, Any]:
        return {"items": self.items, "total": self.total}
