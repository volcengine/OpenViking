# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared index-operation protocol used by plans and queue messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from openviking.utils.tags import merge_search_tags, normalize_search_tags

PATCHABLE_INDEX_FIELDS = frozenset(
    {"md5", "content", "abstract", "updated_at", "active_count", "tags", "search_tags"}
)
_FIELD_MODES = frozenset({"replace", "append"})


class IndexAction(str, Enum):
    """Action applied to one or more vector-index records."""

    NONE = "none"
    UPSERT = "upsert"
    MERGE = "merge"
    UPDATE_FIELDS = "update_fields"
    DELETE = "delete"


@dataclass(frozen=True)
class FieldPatch:
    """One execution-time scalar mutation and its missing-record seed."""

    values: Mapping[str, Any]
    modes: Mapping[str, str] = field(default_factory=dict)
    seed_fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        values = dict(self.values)
        if "search_tags" in values:
            values["search_tags"] = normalize_search_tags(
                values["search_tags"], discard_invalid=True
            )
        object.__setattr__(self, "values", values)
        object.__setattr__(
            self, "modes", {str(key): str(value) for key, value in self.modes.items()}
        )
        object.__setattr__(self, "seed_fields", dict(self.seed_fields))
        unknown = set(self.values) - PATCHABLE_INDEX_FIELDS
        if unknown:
            raise ValueError(f"field patch contains forbidden fields: {sorted(unknown)}")
        unknown_modes = set(self.modes) - set(self.values)
        if unknown_modes:
            raise ValueError(f"field modes without values: {sorted(unknown_modes)}")
        invalid_modes = {
            name: mode for name, mode in self.modes.items() if mode not in _FIELD_MODES
        }
        if invalid_modes:
            raise ValueError(f"invalid field modes: {invalid_modes}")
        unsupported_append = {
            name for name, mode in self.modes.items() if mode == "append" and name != "search_tags"
        }
        if unsupported_append:
            raise ValueError(f"append is unsupported for fields: {sorted(unsupported_append)}")

    def resolve(self, existing: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve only patched fields against the latest stored values."""
        resolved: dict[str, Any] = {}
        for name, value in self.values.items():
            if name == "search_tags":
                incoming = normalize_search_tags(value, discard_invalid=True)
                resolved[name] = (
                    merge_search_tags(existing.get(name), incoming)
                    if self.modes.get(name, "replace") == "append"
                    else incoming
                )
            else:
                resolved[name] = value
        return resolved

    def apply(self, existing: Mapping[str, Any]) -> dict[str, Any]:
        return {**dict(existing), **self.resolve(existing)}

    def with_seed(self, seed_fields: Mapping[str, Any]) -> "FieldPatch":
        return FieldPatch(self.values, self.modes, seed_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "modes": dict(self.modes),
            "seed_fields": dict(self.seed_fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FieldPatch":
        return cls(
            values=dict(data.get("values") or {}),
            modes=dict(data.get("modes") or {}),
            seed_fields=dict(data.get("seed_fields") or {}),
        )


__all__ = ["FieldPatch", "IndexAction", "PATCHABLE_INDEX_FIELDS"]
