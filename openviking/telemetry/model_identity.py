# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Model identity for provider spans, without exporting local filesystem paths."""

import re


def model_name_for_trace(model: str) -> str:
    """Keep provider model IDs; replace path-shaped configuration values."""
    if (
        model.startswith(("/", "./", "../", "~/"))
        or "//" in model
        or "\\" in model
        or re.match(r"^[A-Za-z]:", model)
    ):
        return "local-model"
    return model
