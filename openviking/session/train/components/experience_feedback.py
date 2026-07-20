# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""System-owned feedback statistics for experience memories."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train.domain import Trajectory
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_FEEDBACK_STATS_SCHEMA_VERSION = 1
_EFFECT_KEYS = ("positive", "negative", "weak", "neutral")
_EFFECT_RANK = {"neutral": 0, "weak": 1, "positive": 2, "negative": 3}


@dataclass(slots=True)
class ExperienceFeedbackUpdateResult:
    """Summary of hidden feedback-stats metadata writes."""

    updated_uris: list[str] = field(default_factory=list)
    skipped_uris: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def record_experience_feedback_stats(
    *,
    trajectories: list[Trajectory],
    injected_reminders: list[dict[str, str]],
    viking_fs: Any,
    ctx: Any,
    observed_at: str | None = None,
) -> ExperienceFeedbackUpdateResult:
    """Update hidden ``feedback_stats`` metadata on injected experience files.

    The LLM only writes trajectory-level aliases (E1/E2/...). The alias-to-URI
    mapping is extracted deterministically from runtime reminder tags, so the
    stats remain system-owned and do not need an LLM-visible schema field on
    experiences.
    """

    result = ExperienceFeedbackUpdateResult()
    if not trajectories or not injected_reminders or viking_fs is None:
        return result

    del observed_at  # Kept for API compatibility; stats store aggregate counts only.
    observations = _collect_observations(
        trajectories=trajectories,
        injected_reminders=injected_reminders,
    )
    if not observations:
        return result

    for uri, uri_observations in observations.items():
        if not uri:
            continue
        try:
            raw = await viking_fs.read_file(uri, ctx=ctx)
            mf = MemoryFileUtils.read(raw or "", uri=uri)
            before = dict(mf.extra_fields or {})
            stats, changed = _merge_feedback_stats(
                before.get("feedback_stats"),
                uri_observations,
            )
            if not changed:
                result.skipped_uris.append(uri)
                continue
            mf.extra_fields["feedback_stats"] = stats
            await viking_fs.write_file(uri, MemoryFileUtils.write(mf), ctx=ctx)
            result.updated_uris.append(uri)
        except Exception as exc:  # pragma: no cover - defensive; caller must not fail training
            logger.warning("Failed to update experience feedback stats for %s: %s", uri, exc)
            result.errors.append(f"{uri}: {exc}")

    if result.updated_uris or result.errors:
        tracer.info(
            "[experience_feedback] updated hidden stats: "
            f"updated={len(result.updated_uris)} skipped={len(result.skipped_uris)} "
            f"errors={len(result.errors)}"
        )
    return result


def _collect_observations(
    *,
    trajectories: list[Trajectory],
    injected_reminders: list[dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    alias_to_reminder = {
        str(item.get("id") or "").strip(): item
        for item in injected_reminders or []
        if str(item.get("id") or "").strip()
    }
    if not alias_to_reminder:
        return {}

    # Count at most once per (experience URI, trajectory URI). If the same
    # experience appears under multiple aliases in one trajectory, keep the
    # most safety-critical effect for that trajectory, but do not persist any
    # per-trajectory details in feedback_stats.
    by_uri_and_trajectory: dict[tuple[str, str], str] = {}
    for trajectory in trajectories or []:
        effects = parse_experience_effects(
            (getattr(trajectory, "metadata", {}) or {}).get("experience_effects")
        )
        if effects is None:
            continue
        trajectory_uri = str(getattr(trajectory, "uri", "") or "")
        for alias, reminder in alias_to_reminder.items():
            uri = str(reminder.get("experience_uri") or "").strip()
            if not uri:
                continue
            effect = _effect_for_alias(alias, effects)
            key = (uri, trajectory_uri)
            existing = by_uri_and_trajectory.get(key)
            if existing is None or _EFFECT_RANK[effect] > _EFFECT_RANK[existing]:
                by_uri_and_trajectory[key] = effect

    observations: dict[str, list[dict[str, Any]]] = {}
    for (uri, _trajectory_uri), effect in by_uri_and_trajectory.items():
        observations.setdefault(uri, []).append({"effect": effect})
    return observations


def parse_experience_effects(value: Any) -> dict[str, set[str]] | None:
    """Parse the trajectory ``experience_effects`` field into alias-id sets."""

    if isinstance(value, dict):
        data = value
    elif isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
    else:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "positive_ids": _id_set(data.get("positive_ids")),
        "negative_ids": _id_set(data.get("negative_ids")),
        "weak_ids": _id_set(data.get("weak_ids")),
    }


def _id_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _effect_for_alias(alias: str, effects: dict[str, set[str]]) -> str:
    # Prefer the more safety-critical label if an invalid output puts one alias
    # in multiple lists.
    if alias in effects.get("negative_ids", set()):
        return "negative"
    if alias in effects.get("positive_ids", set()):
        return "positive"
    if alias in effects.get("weak_ids", set()):
        return "weak"
    return "neutral"


def _merge_feedback_stats(
    existing: Any,
    observations: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    stats = _normalize_feedback_stats(existing)
    changed = False
    for observation in observations:
        effect = str(observation.get("effect") or "neutral")
        if effect not in _EFFECT_KEYS:
            effect = "neutral"
        stats["injected_count"] += 1
        stats[f"{effect}_count"] += 1
        changed = True

    return stats, changed


def _normalize_feedback_stats(existing: Any) -> dict[str, Any]:
    source = existing if isinstance(existing, dict) else {}
    stats = {"schema_version": _FEEDBACK_STATS_SCHEMA_VERSION}
    for key in ("injected_count", *[f"{effect}_count" for effect in _EFFECT_KEYS]):
        stats[key] = _safe_nonnegative_int(source.get(key))
    return stats


def _safe_nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)
