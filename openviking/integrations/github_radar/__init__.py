# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""GitHub PR radar.

A watch sidecar that polls a repository's pull requests through the ``gh`` CLI
and writes periodic markdown reports under ``viking://.../radar/<repo>``.

The radar deliberately never touches the git content ingestion path: it reads
platform objects (PRs) directly from the GitHub REST API and keeps its own
watermark in a state file under the report directory. A radar failure must
never fail the watch run that hosts it.
"""

from openviking.integrations.github_radar.models import (
    PR_RADAR_CONFIG_KEY,
    PrSignal,
    RadarConfig,
    RadarState,
    escape_repo_slug,
)

__all__ = [
    "PR_RADAR_CONFIG_KEY",
    "RadarConfig",
    "RadarState",
    "PrSignal",
    "escape_repo_slug",
]
