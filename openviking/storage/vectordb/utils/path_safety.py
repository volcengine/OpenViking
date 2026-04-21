# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for safe vectordb path handling."""

from __future__ import annotations

from pathlib import Path
from typing import Union

from openviking.storage.vectordb.utils.validation import validate_name_str

PathLike = Union[str, Path]


def resolve_storage_path(path: PathLike) -> Path:
    """Return a normalized absolute path for storage operations."""
    return Path(path).expanduser().resolve()


def safe_join(base: PathLike, *parts: str) -> Path:
    """Join child parts under base and ensure the result stays within base."""
    base_path = resolve_storage_path(base)
    candidate = base_path.joinpath(*parts).resolve()
    try:
        candidate.relative_to(base_path)
    except ValueError as exc:
        escaped = "/".join(parts) if parts else str(candidate)
        raise ValueError(f"path escapes base directory: {escaped!r}") from exc
    return candidate


def safe_join_name(base: PathLike, name: str) -> Path:
    """Join a validated vectordb name under base."""
    validate_name_str(name)
    return safe_join(base, name)
