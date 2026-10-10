# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""TTL query planning and response projection. No storage calls or locks.

The reserved tag uses the existing search_tags index, not a new schema field.
Legacy records without this tag need an explicit migration before full TTL
coverage can be claimed. Enabling TTL never starts that migration implicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openviking.storage.expr import And, Eq, Or, PathScope, Range, RawDSL
from openviking_cli.utils.config.ttl_config import TTLConfig, TTL_SCOPES

TAG_PREFIX = "__ov_ttl_scope="
MANAGED_TAGS = [TAG_PREFIX + scope for scope in TTL_SCOPES]
CONTAINER_TAG = TAG_PREFIX + "container"


def scope_and_root(uri: str):
    if not isinstance(uri, str) or not uri.startswith("viking://user/"):
        return None
    parts = uri.removeprefix("viking://").strip("/").split("/")
    if any(not p or p in {".", ".."} for p in parts):
        return None
    if len(parts) >= 3 and parts[2] == "sessions":
        return "sessions", "viking://" + "/".join(parts[:3])
    if len(parts) >= 4 and parts[2:4] == ["memories", "events"]:
        return "user_events", "viking://" + "/".join(parts[:4])
    if len(parts) >= 6 and parts[2] == "peers" and parts[4:6] == ["memories", "events"]:
        return "peer_events", "viking://" + "/".join(parts[:6])
    return None


def deletion_uri(uri):
    target = scope_and_root(uri)
    if target is None or uri.rstrip("/") == target[1]:
        return None
    if target[0] == "sessions":
        return target[1] + "/" + uri[len(target[1]) + 1 :].split("/")[0]
    return uri if not uri.rsplit("/", 1)[-1].startswith(".") else None


def _container_uri(uri):
    if not uri.startswith("viking://user/"):
        return False
    p = uri.removeprefix("viking://").strip("/").split("/")
    return (
        len(p) == 2
        or len(p) == 3
        and p[2] in {"memories", "peers"}
        or len(p) == 4
        and p[2] == "peers"
        or len(p) == 5
        and p[2] == "peers"
        and p[4] == "memories"
    )


def indexed_tags(uri: str, tags, level: int = 2):
    """Derive the reserved scope from URI; callers cannot spoof this tag."""
    result = [tag for tag in tags or [] if not str(tag).lower().startswith(TAG_PREFIX)]
    target = scope_and_root(uri)
    if target and uri.rstrip("/") == target[1]:
        # A policy root contains many expiring objects. Its summary has no
        # single deadline, including the parent of all Sessions.
        result.append(CONTAINER_TAG)
    elif target:
        result.append(TAG_PREFIX + target[0])
    elif level in (0, 1) and _container_uri(uri):
        # Cross-file summaries may contain expired descendants. They do not
        # have a valid independent deadline and must not leak expired content.
        result.append(CONTAINER_TAG)
    return result


def public_tags(tags):
    return [tag for tag in tags or [] if not str(tag).lower().startswith(TAG_PREFIX)]


def timestamp(value) -> datetime | None:
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        # Stored timestamps have a timezone. Guessing the local timezone can
        # silently change expiry across deployments.
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def expires_at(config: TTLConfig, uri: str, updated_at, level: int = 2):
    target = scope_and_root(uri)
    if not target or (level != 2 and target[0] != "sessions") or uri.rstrip("/") == target[1]:
        return None
    policy = config.resolve_uri_policy(uri, target[0])
    if policy.mode != "days":
        return None
    time = timestamp(updated_at)
    if time is None:
        return None
    try:
        return time + timedelta(days=policy.ttl_days)
    except OverflowError:
        return None


def _exclude_roots(roots):
    return RawDSL({"op": "must_not", "field": "uri", "conds": list(roots), "para": "-d=-1"})


def query_filter(config: TTLConfig, now: datetime):
    """One boolean filter before Top-K; exact root overrides win over defaults.

    Parent summaries are excluded for an active policy. Candidate discovery is
    deliberately not part of this query contract.
    """
    if not config.enabled:
        return None
    terms = [
        RawDSL({"op": "must_not", "field": "search_tags", "conds": [*MANAGED_TAGS, CONTAINER_TAG]})
    ]
    for scope in TTL_SCOPES:
        overrides = {
            root: policy
            for root, policy in config.directories.items()
            if scope_and_root(root)[0] == scope
        }
        groups = [(None, getattr(config, scope) or config.global_default), *overrides.items()]
        for root, policy in groups:
            conditions = [Eq("search_tags", TAG_PREFIX + scope)]
            if root is not None:
                conditions.append(PathScope("uri", root))
            elif overrides:
                conditions.append(_exclude_roots(overrides))
            if policy.mode == "days":
                cutoff = (now - timedelta(days=policy.ttl_days)).isoformat()
                if scope != "sessions":
                    conditions.append(Eq("level", 2))
                field = "created_at" if scope == "sessions" else "updated_at"
                conditions.append(Range(field, gt=cutoff))
            terms.append(And(conditions))
    return Or(terms)


def project_results(records, config: TTLConfig | None):
    if config is None or not config.enabled:
        return records
    for record in records:
        uri = record.get("uri", "")
        target = scope_and_root(uri)
        if (
            not target
            or uri.rstrip("/") == target[1]
            or (record.get("level", 2) != 2 and target[0] != "sessions")
        ):
            continue
        if config.resolve_uri_policy(uri, target[0]).mode != "days":
            continue
        deadline = expires_at(
            config,
            uri,
            record.get("created_at" if target[0] == "sessions" else "updated_at"),
            record.get("level", 2),
        )
        record["expires_at"] = deadline.isoformat().replace("+00:00", "Z") if deadline else None
        if deadline is None:
            record["ttl_status"] = "unknown_timestamp"
    return records
