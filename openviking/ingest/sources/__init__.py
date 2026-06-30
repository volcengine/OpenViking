# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Harness log-source adapters. Importing this package registers all built-ins."""

# Import for side effects: each module's @register_source populates SOURCE_REGISTRY.
from openviking.ingest.sources import (  # noqa: F401
    claude_code,
    codex,
    cursor,
    hermes,
    openclaw,
    opencode,
)

__all__ = ["claude_code", "codex", "cursor", "hermes", "opencode", "openclaw"]
