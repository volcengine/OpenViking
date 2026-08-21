# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Output models for context assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from openviking.retrieve.context_assembler.params import Tier


@dataclass
class AssembledEntry:
    """One rendered context entry. The URI is always present, at every tier."""

    uri: str
    category: str
    score: float
    detail: Tier
    text: str = ""
    origin: str = ""
    tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "uri": self.uri,
            "category": self.category,
            "score": self.score,
            "detail": self.detail,
            "text": self.text,
        }
        if self.origin:
            data["origin"] = self.origin
        return data


@dataclass
class AssembleResult:
    entries: List[AssembledEntry] = field(default_factory=list)
    rendered: str = ""
    digest: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "rendered": self.rendered,
            "digest": self.digest,
            "stats": self.stats,
        }
