# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration for the conversation-log ingest subsystem (``openviking-server ingest``).

Ingest parses local agent-harness conversation logs (Claude Code, Codex, OpenCode,
Hermes, OpenClaw, Cursor) and "replays" them through OpenViking's session pipeline.
See ``openviking/ingest/`` for the runtime.
"""

import os
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .consts import (
    OPENVIKING_INGEST_API_KEY_ENV,
    OPENVIKING_INGEST_ENABLED_ENV,
    OPENVIKING_INGEST_SERVER_URL_ENV,
)

IngestMode = Literal["off", "backfill", "watch", "both"]


def _env_truthy(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


class CommitPolicy(BaseModel):
    """When a replayed session is committed (which triggers OV memory extraction)."""

    commit_token_threshold: int = Field(
        default=6000,
        gt=0,
        description="Commit a session once its pending (un-archived) token count reaches this.",
    )
    commit_idle_seconds: float = Field(
        default=5.0,
        gt=0,
        description="In watch mode, commit a session after it has been idle this long.",
    )
    keep_recent_count: int = Field(
        default=0,
        ge=0,
        description="WM v2 sliding window: messages to retain live after a commit (0 = archive all).",
    )

    model_config = {"extra": "forbid"}


class IngestHarnessConfig(BaseModel):
    """Per-harness ingest settings, keyed by registry name (e.g. ``claude_code``)."""

    enabled: bool = Field(default=False, description="Enable ingesting this harness.")
    mode: IngestMode = Field(
        default="backfill",
        description="off | backfill (one-shot historical) | watch (incremental) | both.",
    )
    paths: List[str] = Field(
        default_factory=list,
        description="Override the harness's default log discovery roots (file/dir/db paths).",
    )
    poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Incremental poll cadence for this harness (watch mode).",
    )
    user_field: str = Field(
        default="",
        description=(
            "For group-chat harnesses (hermes/openclaw): the log key holding the original "
            "username, used as the user-side peer_id. Empty = use the adapter default."
        ),
    )
    experimental: bool = Field(
        default=False,
        description="Opt into experimental adapters (e.g. cursor) that may be fragile.",
    )
    commit: CommitPolicy = Field(default_factory=CommitPolicy)

    model_config = {"extra": "forbid"}


class IngestConfig(BaseModel):
    """Top-level ingest configuration (``ingest`` section of ``ov.conf``)."""

    enabled: bool = Field(default=False, description="Master switch for log ingestion.")
    server_url: str = Field(
        default="",
        description="OV server URL the ingest client targets. Empty => OPENVIKING_URL / localhost.",
    )
    api_key: str = Field(default="", description="API key for the target OV server.")
    account: str = Field(default="default", description="OV account that owns ingested sessions.")
    user: str = Field(default="default", description="OV user that owns ingested sessions.")
    state_dir: str = Field(
        default="",
        description="Where the read-cursor state DB lives. Empty => ~/.openviking/ingest.",
    )
    session_id_prefix: str = Field(
        default="import",
        description="OV session ids are '{prefix}__{harness}__{native_session_id}'.",
    )
    memory_policy: Dict = Field(
        default_factory=dict,
        description="Passthrough memory_policy for replayed sessions (empty => server default).",
    )
    harnesses: Dict[str, IngestHarnessConfig] = Field(
        default_factory=dict,
        description="Per-harness configuration, keyed by registry name.",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _apply_env_overrides(self) -> "IngestConfig":
        # Operational toggles may come from the environment (deploy-time > config).
        enabled = _env_truthy(os.environ.get(OPENVIKING_INGEST_ENABLED_ENV))
        if enabled is not None:
            self.enabled = enabled
        server_url = os.environ.get(OPENVIKING_INGEST_SERVER_URL_ENV)
        if server_url:
            self.server_url = server_url
        api_key = os.environ.get(OPENVIKING_INGEST_API_KEY_ENV)
        if api_key:
            self.api_key = api_key
        return self

    def enabled_harnesses(self) -> Dict[str, IngestHarnessConfig]:
        """Harnesses that are turned on and not in 'off' mode.

        The master switch ``enabled`` gates everything: when it is false, no harness
        is active regardless of its own ``enabled`` flag.
        """
        if not self.enabled:
            return {}
        return {
            name: cfg for name, cfg in self.harnesses.items() if cfg.enabled and cfg.mode != "off"
        }
