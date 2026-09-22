# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Three-state PATCH merge and copy-on-write helpers.

The same three-state semantics apply to every config layer (cluster and account):

- field absent: keep current value.
- field is a concrete value: set/replace.
- field is ``None``: delete this layer's override, inheriting the next layer.

Objects merge recursively, arrays replace wholesale.
"""

from __future__ import annotations

import copy
from typing import Any, Optional


def apply_three_state_patch(current: Optional[dict], patch: dict) -> dict:
    """Return a new sparse override with ``patch`` applied to ``current``.

    Never mutates the inputs (pure function; safe for conflict retries).
    """
    result: dict = copy.deepcopy(current) if current else {}
    _merge_into(result, patch)
    return result


def _merge_into(base: dict, patch: dict) -> None:
    for key, value in patch.items():
        if value is None:
            # Delete this layer's override, restoring inheritance.
            base.pop(key, None)
            continue
        if isinstance(value, dict):
            existing = base.get(key)
            if isinstance(existing, dict):
                _merge_into(existing, value)
            else:
                nested: dict = {}
                _merge_into(nested, value)
                # A delete-only child patch against a missing parent is a no-op.
                # An explicitly empty object still creates an explicit parent.
                if nested or not value:
                    base[key] = nested
            continue
        # Concrete scalar or list: replace wholesale.
        base[key] = copy.deepcopy(value)


def diff_sections(old: Any, new: Any) -> frozenset[str]:
    """Return the set of top-level sections whose value changed.

    Accepts either dicts or pydantic models; models are dumped first. Used to
    build :class:`~openviking.config.manager.ConfigChangeEvent`.
    """
    old_dict = _as_dict(old)
    new_dict = _as_dict(new)
    changed: set[str] = set()
    for key in set(old_dict) | set(new_dict):
        if old_dict.get(key) != new_dict.get(key):
            changed.add(key)
    return frozenset(changed)


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump()
    return {}
