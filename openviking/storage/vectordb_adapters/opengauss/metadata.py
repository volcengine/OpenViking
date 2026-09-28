# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Normalize persisted pre-release metadata at the database read boundary only."""

from typing import Any

from openviking_cli.utils.config.vectordb_config import (
    _OPENGAUSS_BUILD_PARAMS,
    _OPENGAUSS_SEARCH_ALIASES,
    _OPENGAUSS_SEARCH_PARAMS,
)

BUILD_KEYS = frozenset().union(*_OPENGAUSS_BUILD_PARAMS.values()) | {"enable_pq", "enable_rabitq"}
SEARCH_KEYS = frozenset().union(*_OPENGAUSS_SEARCH_PARAMS.values()) | frozenset(
    key for aliases in _OPENGAUSS_SEARCH_ALIASES.values() for key in aliases
)


def migrate_persisted_index_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Consume old duplicate fields once; never emit them in new metadata."""
    result = dict(meta)
    for group, keys in (("build_params", BUILD_KEYS), ("search_params", SEARCH_KEYS)):
        values = result.get(group, {})
        if not isinstance(values, dict):
            raise ValueError(f"openGauss {group} must be an object")
        values = dict(values)
        for key in keys:
            if key in result:
                values.setdefault(key, result.pop(key))
        result[group] = values
    build = result["build_params"]
    if "parallel_workers" in build:
        result.setdefault("parallel_workers", build.pop("parallel_workers"))
    return result
