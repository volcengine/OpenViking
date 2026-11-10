# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Commit completeness, independent from scanner/parser strictness."""

from typing import Literal, cast

from openviking_cli.exceptions import InvalidArgumentError

ImportPolicy = Literal["require_complete", "best_effort"]
DEFAULT_IMPORT_POLICY: ImportPolicy = "require_complete"


def validate_import_policy(value: str, *, strict: bool = False) -> ImportPolicy:
    if value not in ("require_complete", "best_effort"):
        raise InvalidArgumentError("import_policy must be 'require_complete' or 'best_effort'")
    if strict and value == "best_effort":
        raise InvalidArgumentError(
            "strict=True cannot be combined with import_policy='best_effort'"
        )
    return cast(ImportPolicy, value)
