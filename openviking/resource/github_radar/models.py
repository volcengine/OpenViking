# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Radar configuration, immutable PR facts and durable progress."""

import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from openviking_cli.exceptions import InvalidArgumentError


def utc(value: datetime) -> datetime:
    """Watch's naive timestamps use the server's local timezone."""
    return value.astimezone(timezone.utc).replace(microsecond=0)


def github_repository(path: str) -> str:
    if path.startswith("git@github.com:"):
        name = path[len("git@github.com:") :]
    else:
        parsed = urlsplit(path)
        if (
            parsed.scheme not in {"https", "ssh"}
            or parsed.hostname != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise InvalidArgumentError("pr_radar requires a GitHub repository URL.")
        name = parsed.path.lstrip("/")
    name = name.rstrip("/")
    if name.endswith(".git"):
        name = name[:-4]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise InvalidArgumentError("pr_radar requires a GitHub owner/repository URL.")
    if any(part in {".", ".."} for part in name.split("/")):
        raise InvalidArgumentError("Invalid GitHub repository.")
    return name.lower()


class RadarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool = False
    backfill_days: StrictInt = Field(default=7, ge=1, le=90)
    # No unapproved scheduling default. Required only when Radar is enabled.
    digest_interval_minutes: StrictInt | None = Field(default=None, ge=1, le=10080)
    max_prs_per_run: StrictInt = Field(default=50, ge=1, le=1000)
    summary_enabled: StrictBool = True
    summary_batch_bytes: StrictInt = Field(default=60000, ge=2000, le=200000)
    query_overlap_minutes: StrictInt = Field(default=1440, ge=1, le=10080)


def validate_radar(
    path: str, kwargs: dict[str, Any], *, watch_interval: float, add_type: str | None = None
) -> RadarConfig | None:
    if "pr_radar" not in kwargs:
        return None
    try:
        config = RadarConfig.model_validate(kwargs["pr_radar"])
    except Exception as exc:
        # Do not echo arbitrary configuration values (possibly credentials).
        raise InvalidArgumentError(
            "Invalid pr_radar configuration; check supported fields, types and ranges."
        ) from exc
    if config.enabled:
        github_repository(path)
        if add_type or watch_interval <= 0:
            raise InvalidArgumentError("pr_radar requires a native GitHub Watch.")
        if config.digest_interval_minutes is None:
            raise InvalidArgumentError("pr_radar.digest_interval_minutes is required when enabled.")
    return config


class PullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    number: int = Field(gt=0)
    title: str
    url: str
    author: str
    state: Literal["MERGED"] = "MERGED"
    draft: bool = False
    labels: list[str] = Field(default_factory=list)
    body: str = ""
    created_at: datetime
    updated_at: datetime
    closed_at: datetime
    merged_at: datetime


class QueryRange(BaseModel):
    start: datetime
    end: datetime


class PendingPublish(BaseModel):
    report_id: str
    content: str
    queue_after: list[PullRequest]
    state_after: dict[str, Any]


class RadarState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    repository: str
    anchor: datetime
    backfill_start: datetime
    reported_until: datetime
    backfill_done: bool = False
    collected_until: datetime | None = None
    scan_end: datetime | None = None
    ranges: list[QueryRange] = Field(default_factory=list)
    seen_numbers: list[int] = Field(default_factory=list)
    pending_publish: PendingPublish | None = None
    last_report_id: str | None = None
    last_error: str | None = None
