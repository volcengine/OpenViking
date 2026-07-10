# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Conversation-log ingest: replay local agent-harness logs into OpenViking sessions.

Parses each harness's local conversation logs (Claude Code, Codex, OpenCode, Hermes,
OpenClaw, Cursor) into normalized messages and replays them through OV's existing
``create_session -> batch_add_messages -> commit`` pipeline (commit triggers OV's
async memory extraction). Supports one-shot backfill of existing logs and
incremental, cursor-driven polling of new logs.

Inspired by / supersedes volcengine/OpenViking#2674 by @huang-yi-dae.
"""

from openviking.ingest.models import Cursor, NormalizedMessage, SessionRef
from openviking.ingest.registry import SOURCE_REGISTRY, register_source

__all__ = [
    "Cursor",
    "NormalizedMessage",
    "SessionRef",
    "SOURCE_REGISTRY",
    "register_source",
]
