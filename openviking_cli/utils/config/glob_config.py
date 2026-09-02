# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from typing import Literal

from pydantic import BaseModel, Field

# Glob engine mode type alias — import this instead of repeating Literal["auto", "fs"]
GlobEngine = Literal["auto", "fs"]


class GlobConfig(BaseModel):
    """Configuration for glob engine behavior."""

    engine: GlobEngine = Field(
        default="auto",
        description=(
            "Glob engine mode: 'auto' uses remote VikingDB path_glob when available, "
            "'fs' forces local filesystem glob."
        ),
    )

    switch_to_remote_threshold: int = Field(
        default=1000,
        ge=0,
        description=(
            "Vector record count threshold to switch to VikingDB path_glob; "
            "0 means always use VikingDB when available."
        ),
    )

    model_config = {"extra": "forbid"}
