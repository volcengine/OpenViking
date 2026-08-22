# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from typing import Literal

from pydantic import BaseModel, Field

# Grep engine mode type alias — import this instead of repeating Literal["auto", "fs", "local", "vikingdb"]
GrepEngine = Literal["auto", "fs", "local", "vikingdb"]


class GrepConfig(BaseModel):
    """Configuration for grep engine behavior."""

    engine: GrepEngine = Field(
        default="auto",
        description=(
            "Search engine mode: 'auto' uses remote VikingDB BM25 recall when "
            "available, falls back to the local FTS5 keyword sidecar when enabled, "
            "otherwise forces filesystem scan. 'fs' forces filesystem search; "
            "'local' forces the local FTS5 keyword sidecar; 'vikingdb' forces the "
            "remote VikingDB BM25 path."
        ),
    )

    switch_to_remote_threshold: int = Field(
        default=10000,
        ge=0,
        description=(
            "L2 record count threshold to switch to vikingdb; 0 means always use vikingdb."
        ),
    )

    model_config = {"extra": "forbid"}
