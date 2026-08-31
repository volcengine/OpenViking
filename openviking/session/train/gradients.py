# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic gradient implementations for policy optimization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openviking.session.memory.dataclass import (
    MemoryFile,
    StoredLink,
    name_field_for_memory_type,
)


@dataclass(slots=True)
class PatchSemanticGradient:
    """Patch-based semantic gradient for one target policy.

    A semantic gradient is represented as a typed before/after memory file pair.
    The concrete patch text is a rendering concern owned by merge context
    providers; the gradient itself carries structured memory-file state.
    """

    before_file: MemoryFile | None
    after_file: MemoryFile
    base_version: int | None
    rationale: str
    links: list[StoredLink]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def target_name(self) -> str:
        fields = self.after_file.extra_fields or {}
        memory_type = self.after_file.memory_type or fields.get("memory_type") or "experiences"
        name = fields.get(name_field_for_memory_type(str(memory_type))) or fields.get("name")
        if name:
            return str(name)
        uri = self.target_uri
        return uri.rstrip("/").split("/")[-1].removesuffix(".md") if uri else "unknown_policy"

    @property
    def target_uri(self) -> str | None:
        return self.after_file.uri or (self.before_file.uri if self.before_file is not None else None)
