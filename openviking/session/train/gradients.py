# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic gradient implementations for policy optimization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ExperienceContentPatch:
    """Before/after content patch for one Experience.

    ``before_content`` is ``None`` when the patch proposes a new Experience.
    """

    before_content: str | None
    after_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PatchSemanticGradient:
    """Patch-based semantic gradient for one target Experience."""

    target_experience_name: str
    target_experience_uri: str | None
    base_version: int | None
    patch: ExperienceContentPatch
    rationale: str
    evidence_trajectory_uris: list[str]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
