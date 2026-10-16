# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-scoped GitHub access configuration."""

from pydantic import BaseModel

from .runtime_field import RuntimeField


class GitHubConfig(BaseModel):
    """Configuration for GitHub access."""

    token: str = RuntimeField(default="")
