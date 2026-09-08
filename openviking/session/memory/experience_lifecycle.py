# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deterministic lifecycle rules for Experience memories."""

from __future__ import annotations

from typing import Any, Iterable

EXPERIENCE_STATUS_VALUES = frozenset({"draft", "promoted", "degraded", "archived"})

_LEGACY_STATUS_MAP = {
    "staging": "draft",
    "production": "promoted",
    "deprecated": "degraded",
}


def normalize_experience_status(value: Any, *, default: str = "promoted") -> str:
    """Normalize lifecycle state while keeping legacy Experience files usable."""

    status = str(value or "").strip().lower()
    status = _LEGACY_STATUS_MAP.get(status, status)
    if status in EXPERIENCE_STATUS_VALUES:
        return status
    return default


def experience_is_agent_visible(value: Any) -> bool:
    """Only promoted Experiences are safe for Agent-facing recall."""

    return normalize_experience_status(value) == "promoted"


def experience_is_case_linkable(value: Any) -> bool:
    """Require explicit promotion for Case links; fail closed on missing status.

    This does not change the legacy search/read visibility policy.
    """

    return normalize_experience_status(value, default="draft") == "promoted"


def is_experience_memory_uri(uri: Any) -> bool:
    """Return whether ``uri`` identifies a concrete Experience memory file."""

    source_uri = str(uri or "").split("#", 1)[0].rstrip("/")
    return (
        "/memories/experiences/" in source_uri
        and source_uri.endswith(".md")
        and not source_uri.endswith(("/.abstract.md", "/.overview.md"))
    )


def experience_file_is_archived(memory_file: Any, *, uri: Any = None) -> bool:
    """Return whether one concrete Experience file is archived."""

    memory_type = str(getattr(memory_file, "memory_type", None) or "")
    if memory_type != "experiences" and not is_experience_memory_uri(
        uri or getattr(memory_file, "uri", None)
    ):
        return False
    fields = dict(getattr(memory_file, "extra_fields", {}) or {})
    return normalize_experience_status(fields.get("status")) == "archived"


def experience_case_link_uris(links: Iterable[Any], *, experience_uri: str) -> set[str]:
    """Return Case URIs connected to one Experience by persisted StoredLinks."""

    result: set[str] = set()
    for link in links or []:
        from_uri = _link_value(link, "from_uri")
        to_uri = _link_value(link, "to_uri")
        if to_uri != experience_uri or "/memories/cases/" not in from_uri:
            continue
        if from_uri.endswith(".md"):
            result.add(from_uri)
    return result


def experience_lifecycle_fields(
    *,
    existing_policy: Any | None,
    links: Iterable[Any],
    gradients: Iterable[Any],
) -> dict[str, Any]:
    """Compute lifecycle fields from persisted provenance and current evidence.

    Content remains LLM-authored.  Lifecycle state is deliberately code-owned:
    one fully successful observed recovery can be promoted immediately, while
    other lessons require two independent trajectories supporting the same
    merged Experience.
    """

    existing_metadata = (
        dict(getattr(existing_policy, "metadata", {}) or {}) if existing_policy is not None else {}
    )
    existing_status = normalize_experience_status(
        existing_metadata.get("status") or getattr(existing_policy, "status", None),
        default="draft" if existing_policy is None else "promoted",
    )
    source_count = max(
        _non_negative_int(existing_metadata.get("source_count")),
        len(experience_source_trajectory_uris(links)),
    )
    complete_recovery = any(_is_complete_observed_recovery(item) for item in gradients)

    if existing_status == "archived":
        status = "archived"
        reason = "archived"
    elif existing_status == "degraded":
        # A degraded rule needs an explicit governance decision before it can
        # return to Agent recall; ordinary training updates must not revive it.
        status = "degraded"
        reason = "awaiting_reconfirmation"
    elif existing_status == "promoted":
        status = "promoted"
        reason = str(existing_metadata.get("promotion_reason") or "legacy_or_previously_promoted")
    elif complete_recovery:
        status = "promoted"
        reason = "complete_observed_recovery"
    elif source_count >= 2:
        status = "promoted"
        reason = "multi_trajectory_confirmation"
    else:
        status = "draft"
        reason = "single_trajectory_only"

    return {
        "status": status,
        "source_count": source_count,
        "promotion_reason": reason,
    }


def experience_source_trajectory_uris(links: Iterable[Any]) -> set[str]:
    """Return independent trajectory URIs referenced by Experience provenance."""

    result: set[str] = set()
    for link in links or []:
        link_type = _link_value(link, "link_type")
        uri = _link_value(link, "to_uri")
        if link_type != "derived_from" or "/memories/trajectories/" not in uri:
            continue
        result.add(uri)
    return result


def _is_complete_observed_recovery(gradient: Any) -> bool:
    metadata = dict(getattr(gradient, "metadata", {}) or {})
    if str(metadata.get("trajectory_outcome") or "").strip().lower() != "success":
        return False
    if str(metadata.get("recovery_status") or "").strip().lower() != "observed_recovered":
        return False
    if metadata.get("rubric_passed") is not True:
        return False
    try:
        return float(metadata.get("rubric_score")) >= 1.0
    except (TypeError, ValueError):
        return False


def _link_value(link: Any, name: str) -> str:
    if isinstance(link, dict):
        return str(link.get(name) or "")
    return str(getattr(link, name, "") or "")


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
