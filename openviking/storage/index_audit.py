# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only auditing between VikingFS resource facts and vector records."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Iterable

from openviking.core.context import ContextType
from openviking.core.namespace import owner_fields_for_uri
from openviking.server.identity import RequestContext
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.index_digest import canonical_digest, canonical_json
from openviking.storage.index_source import (
    IndexSourceFact,
    UnresolvedIndexSource,
    build_index_sources,
)
from openviking_cli.exceptions import InvalidArgumentError

AUDIT_SCHEMA_VERSION = "index-audit/v1"
REPAIR_PLAN_VERSION = "index-repair/v1"
ISSUE_TYPES = frozenset(
    {
        "missing",
        "stale",
        "orphan",
        "metadata_mismatch",
        "duplicate_keys",
        "unverifiable",
    }
)
DEFAULT_FINDING_LIMIT = 100
MAX_FINDING_LIMIT = 1000
DEFAULT_MAX_SCAN_RECORDS = 10000
MAX_SCAN_RECORDS = 100000
AUDIT_OUTPUT_FIELDS = [
    "id",
    "uri",
    "level",
    "context_type",
    "account_id",
    "owner_user_id",
    "source_digest",
]


def _short_digest(value: str | None) -> str | None:
    return value[:22] if value else None


def index_records_fingerprint(records: Iterable[dict[str, Any]]) -> str:
    """Fingerprint index records using only audit-relevant fields."""
    values = [{field: record.get(field) for field in AUDIT_OUTPUT_FIELDS} for record in records]
    values.sort(key=canonical_json)
    return canonical_digest(values)


@dataclass(frozen=True)
class IndexFinding:
    """One categorized index inconsistency."""

    issue_type: str
    uri: str
    level: int
    reason_code: str
    record_count: int = 0
    expected_source_digest: str | None = None
    actual_source_digest: str | None = None
    expected_index_fingerprint: str | None = None
    auto_fixable: bool = False

    @property
    def finding_id(self) -> str:
        return canonical_digest(
            {
                "issue_type": self.issue_type,
                "uri": self.uri,
                "level": self.level,
                "reason_code": self.reason_code,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the public, redacted representation."""
        return {
            "finding_id": self.finding_id,
            "issue_type": self.issue_type,
            "uri": self.uri,
            "level": self.level,
            "reason_code": self.reason_code,
            "record_count": self.record_count,
            "expected_source_digest": _short_digest(self.expected_source_digest),
            "actual_source_digest": _short_digest(self.actual_source_digest),
            "auto_fixable": self.auto_fixable,
        }


def _schema_has_source_digest(meta: dict[str, Any] | None) -> bool:
    if meta is None:
        return False
    if "Fields" not in meta:
        return True
    return any(field.get("FieldName") == "source_digest" for field in meta.get("Fields", []))


async def _collection_meta(vector_store: Any, ctx: RequestContext) -> dict[str, Any] | None:
    if not hasattr(vector_store, "get_collection_meta"):
        return None
    try:
        return await vector_store.get_collection_meta(ctx=ctx)
    except TypeError:
        return await vector_store.get_collection_meta()


async def _filter_records(
    vector_store: Any, uri: str, ctx: RequestContext
) -> tuple[list[dict[str, Any]] | None, str | None]:
    kwargs = {
        "filter": Eq("uri", uri),
        "limit": 101,
        "output_fields": AUDIT_OUTPUT_FIELDS,
    }
    try:
        try:
            return await vector_store.filter(**kwargs, ctx=ctx), None
        except TypeError:
            return await vector_store.filter(**kwargs), None
    except Exception:
        return None, "index_read_failed"


async def _scroll_records(
    vector_store: Any,
    root_uri: str,
    ctx: RequestContext,
    max_records: int,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    records: list[dict[str, Any]] = []
    cursor: str | None = None
    filter_expr = And(
        [
            PathScope("uri", root_uri),
            Eq("context_type", ContextType.RESOURCE.value),
        ]
    )
    while True:
        remaining = max_records - len(records)
        if remaining <= 0:
            return records, False, "max_scan_records_reached"
        try:
            kwargs = {
                "filter": filter_expr,
                "limit": min(500, remaining),
                "cursor": cursor,
                "output_fields": AUDIT_OUTPUT_FIELDS,
            }
            try:
                page, cursor = await vector_store.scroll(**kwargs, ctx=ctx)
            except TypeError:
                page, cursor = await vector_store.scroll(**kwargs)
        except Exception:
            return records, False, "index_scroll_failed"
        records.extend(page)
        if not cursor:
            return records, True, None


def _metadata_matches(record: dict[str, Any], fact: IndexSourceFact, ctx: RequestContext) -> bool:
    expected_owner = owner_fields_for_uri(fact.uri, ctx=ctx).get("owner_user_id")
    return (
        record.get("uri") == fact.uri
        and record.get("level") == fact.level
        and record.get("context_type") == ContextType.RESOURCE.value
        and record.get("account_id") == ctx.account_id
        and record.get("owner_user_id") == expected_owner
    )


def _unresolved_finding(source: UnresolvedIndexSource) -> IndexFinding:
    return IndexFinding(
        issue_type="unverifiable",
        uri=source.uri,
        level=source.level,
        reason_code=source.reason_code,
    )


def _classify_fact(
    fact: IndexSourceFact,
    records: list[dict[str, Any]] | None,
    error: str | None,
    *,
    digest_supported: bool,
    ctx: RequestContext,
) -> IndexFinding | None:
    if records is None:
        return IndexFinding("unverifiable", fact.uri, fact.level, error or "index_read_failed")

    matching = [record for record in records if record.get("level") == fact.level]
    fingerprint = index_records_fingerprint(matching)
    if len(matching) > 1:
        return IndexFinding(
            "duplicate_keys",
            fact.uri,
            fact.level,
            "multiple_records_for_logical_key",
            len(matching),
            fact.digest,
            expected_index_fingerprint=fingerprint,
            auto_fixable=True,
        )
    if not matching:
        return IndexFinding(
            "missing",
            fact.uri,
            fact.level,
            "record_absent",
            expected_source_digest=fact.digest,
            expected_index_fingerprint=fingerprint,
            auto_fixable=True,
        )

    record = matching[0]
    if not _metadata_matches(record, fact, ctx):
        return IndexFinding(
            "metadata_mismatch",
            fact.uri,
            fact.level,
            "critical_metadata_mismatch",
            1,
            fact.digest,
            expected_index_fingerprint=fingerprint,
            auto_fixable=True,
        )
    actual_digest = record.get("source_digest")
    if not digest_supported:
        return IndexFinding(
            "unverifiable",
            fact.uri,
            fact.level,
            "source_digest_field_unavailable",
            1,
            fact.digest,
        )
    if not isinstance(actual_digest, str) or not actual_digest:
        return IndexFinding(
            "unverifiable",
            fact.uri,
            fact.level,
            "source_digest_missing",
            1,
            fact.digest,
        )
    if actual_digest != fact.digest:
        return IndexFinding(
            "stale",
            fact.uri,
            fact.level,
            "source_digest_mismatch",
            1,
            fact.digest,
            actual_digest,
            fingerprint,
            True,
        )
    return None


def _encode_cursor(offset: int, request_fingerprint: str) -> str:
    payload = {"version": 1, "offset": offset, "request_fingerprint": request_fingerprint}
    envelope = {"payload": payload, "digest": canonical_digest(payload)}
    return base64.urlsafe_b64encode(canonical_json(envelope).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str, request_fingerprint: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        envelope = json.loads(base64.urlsafe_b64decode(padded).decode())
        payload = envelope["payload"]
        if envelope["digest"] != canonical_digest(payload):
            raise ValueError
        if payload.get("version") != 1:
            raise ValueError
        if payload.get("request_fingerprint") != request_fingerprint:
            raise ValueError
        offset = payload["offset"]
        if not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except Exception as exc:
        raise InvalidArgumentError("Invalid or mismatched consistency cursor.") from exc


def _build_repair_plan(
    *,
    root_uri: str,
    account_id: str,
    collection_name: str,
    schema_fingerprint: str,
    facts: tuple[IndexSourceFact, ...],
    findings: list[IndexFinding],
) -> dict[str, Any]:
    fact_by_key = {fact.key: fact for fact in facts}
    actions = []
    for finding in findings:
        if not finding.auto_fixable:
            continue
        fact = fact_by_key.get((finding.uri, finding.level))
        action = "delete" if finding.issue_type == "orphan" or fact is None else "reindex"
        if finding.issue_type in {"metadata_mismatch", "duplicate_keys"} and fact:
            action = "delete_reindex"
        actions.append(
            {
                "action": action,
                "uri": finding.uri,
                "level": finding.level,
                "reason": finding.issue_type,
                "expected_source_digest": fact.digest if fact else None,
                "expected_index_fingerprint": finding.expected_index_fingerprint,
            }
        )
    root_fingerprint = canonical_digest(
        [{"uri": fact.uri, "level": fact.level, "source_digest": fact.digest} for fact in facts]
    )
    plan: dict[str, Any] = {
        "plan_version": REPAIR_PLAN_VERSION,
        "account_id": account_id,
        "root_uri": root_uri,
        "collection": {
            "name": collection_name,
            "schema_fingerprint": schema_fingerprint,
        },
        "root_fingerprint": root_fingerprint,
        "actions": actions,
    }
    plan["plan_digest"] = canonical_digest(plan)
    return plan


async def audit_index(
    viking_fs: Any,
    vector_store: Any,
    root_uri: str,
    entries: list[dict[str, Any]],
    ctx: RequestContext,
    *,
    issue_types: list[str] | None = None,
    limit: int = DEFAULT_FINDING_LIMIT,
    cursor: str | None = None,
    max_scan_records: int = DEFAULT_MAX_SCAN_RECORDS,
    generate_repair_plan: bool = False,
) -> dict[str, Any]:
    """Audit a resource subtree without performing storage writes."""
    if limit <= 0 or limit > MAX_FINDING_LIMIT:
        raise InvalidArgumentError(f"limit must be between 1 and {MAX_FINDING_LIMIT}.")
    if max_scan_records <= 0 or max_scan_records > MAX_SCAN_RECORDS:
        raise InvalidArgumentError(f"max_scan_records must be between 1 and {MAX_SCAN_RECORDS}.")
    selected = set(issue_types or ISSUE_TYPES)
    unknown = selected - ISSUE_TYPES
    if unknown:
        raise InvalidArgumentError(f"Unsupported consistency issue type: {sorted(unknown)[0]}")

    facts, unresolved = await build_index_sources(viking_fs, root_uri, entries, ctx)
    meta_error = None
    has_meta_api = hasattr(vector_store, "get_collection_meta")
    try:
        meta = await _collection_meta(vector_store, ctx)
        if has_meta_api and meta is None:
            meta_error = "collection_schema_unavailable"
    except Exception:
        meta = None
        meta_error = "collection_schema_unavailable"
    schema_fingerprint = canonical_digest(meta or {})
    digest_supported = _schema_has_source_digest(meta) and meta_error is None
    findings = [_unresolved_finding(source) for source in unresolved]

    seen_record_fingerprints: set[str] = set()
    scanned_count = len(facts)
    truncated = scanned_count > max_scan_records
    truncation_reason = "max_scan_records_reached" if truncated else None
    records_by_uri: dict[str, tuple[list[dict[str, Any]] | None, str | None]] = {}
    expected_levels_by_uri: dict[str, set[int]] = {}
    for fact in facts:
        expected_levels_by_uri.setdefault(fact.uri, set()).add(fact.level)
    for fact in facts[:max_scan_records]:
        if fact.uri not in records_by_uri:
            records_by_uri[fact.uri] = await _filter_records(vector_store, fact.uri, ctx)
        records, error = records_by_uri[fact.uri]
        finding = _classify_fact(
            fact,
            records,
            error,
            digest_supported=digest_supported,
            ctx=ctx,
        )
        if finding:
            findings.append(finding)
        for record in records or []:
            seen_record_fingerprints.add(index_records_fingerprint([record]))

    # A record for a known URI but an impossible level is a metadata defect, not an
    # orphan. Report it once per physical record so a repair plan can delete it.
    for uri, (records, error) in records_by_uri.items():
        if error or records is None:
            continue
        expected_levels = expected_levels_by_uri[uri]
        for record in records:
            level = record.get("level")
            if isinstance(level, int) and level not in expected_levels:
                findings.append(
                    IndexFinding(
                        "metadata_mismatch",
                        uri,
                        level,
                        "unexpected_level_for_source",
                        1,
                        expected_index_fingerprint=index_records_fingerprint([record]),
                        auto_fixable=True,
                    )
                )

    remaining = max(0, max_scan_records - min(scanned_count, max_scan_records))
    vector_records: list[dict[str, Any]] = []
    vector_complete = False
    vector_error: str | None = None
    if not truncated:
        vector_records, vector_complete, vector_error = await _scroll_records(
            vector_store, root_uri, ctx, remaining
        )
        scanned_count += len(vector_records)
        if not vector_complete:
            truncated = True
            truncation_reason = vector_error

    expected_keys = {fact.key for fact in facts}
    unresolved_keys = {(source.uri, source.level) for source in unresolved}
    for record in vector_records if vector_complete else []:
        fingerprint = index_records_fingerprint([record])
        if fingerprint in seen_record_fingerprints:
            continue
        record_uri = record.get("uri")
        level = record.get("level")
        if not isinstance(record_uri, str) or not isinstance(level, int):
            findings.append(IndexFinding("unverifiable", root_uri, -1, "invalid_index_metadata", 1))
            continue
        key = (record_uri, level)
        if key in expected_keys or key in unresolved_keys:
            continue
        findings.append(
            IndexFinding(
                "orphan",
                record_uri,
                level,
                "source_confirmed_absent",
                1,
                expected_index_fingerprint=fingerprint,
                auto_fixable=True,
            )
        )

    if meta_error:
        findings.append(IndexFinding("unverifiable", root_uri, -1, meta_error))
    if vector_error == "index_scroll_failed":
        findings.append(IndexFinding("unverifiable", root_uri, -1, vector_error))

    deduped = {finding.finding_id: finding for finding in findings}
    all_findings = sorted(
        deduped.values(), key=lambda item: (item.uri, item.level, item.issue_type, item.finding_id)
    )
    counts = dict.fromkeys(sorted(ISSUE_TYPES), 0)
    for finding in all_findings:
        counts[finding.issue_type] += 1
    filtered = [finding for finding in all_findings if finding.issue_type in selected]

    collection_name = str(getattr(vector_store, "collection_name", "context"))
    request_fingerprint = canonical_digest(
        {
            "root_uri": root_uri,
            "account_id": ctx.account_id,
            "collection": collection_name,
            "schema_fingerprint": schema_fingerprint,
            "issue_types": sorted(selected),
            "max_scan_records": max_scan_records,
        }
    )
    offset = _decode_cursor(cursor, request_fingerprint) if cursor else 0
    if offset > len(filtered):
        raise InvalidArgumentError("Invalid or mismatched consistency cursor.")
    page = filtered[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = (
        _encode_cursor(next_offset, request_fingerprint) if next_offset < len(filtered) else None
    )
    complete = not truncated and next_cursor is None

    missing = [finding for finding in all_findings if finding.issue_type == "missing"]
    result: dict[str, Any] = {
        "ok": not all_findings and not truncated,
        "expected_count": len(facts),
        "missing_record_count": len(missing),
        "missing_records": [
            {
                "uri": finding.uri,
                "path": next(
                    (fact.rel_path for fact in facts if fact.key == (finding.uri, finding.level)),
                    "",
                ),
                "level": finding.level,
                "key": (
                    f"{next((fact.rel_path for fact in facts if fact.key == (finding.uri, finding.level)), '')}"
                    f"#level={finding.level}"
                ),
            }
            for finding in missing[:20]
        ],
        "missing_records_truncated": len(missing) > 20,
        "schema_version": AUDIT_SCHEMA_VERSION,
        "scope": {
            "root_uri": root_uri,
            "account_id": ctx.account_id,
            "collection": collection_name,
            "context_type": ContextType.RESOURCE.value,
            "levels": [0, 1, 2],
        },
        "complete": complete,
        "next_cursor": next_cursor,
        "scanned_count": scanned_count,
        "counts": counts,
        "findings": [finding.to_dict() for finding in page],
        "truncated": truncated,
        "truncation_reason": truncation_reason,
    }
    if generate_repair_plan:
        if (
            truncated
            or next_cursor
            or vector_error
            or unresolved
            or meta_error
            or any(finding.issue_type == "unverifiable" for finding in all_findings)
        ):
            result["repair_plan_error"] = "complete_verifiable_scan_required"
        elif cursor:
            result["repair_plan_error"] = "repair_plan_requires_initial_complete_scan"
        else:
            result["repair_plan"] = _build_repair_plan(
                root_uri=root_uri,
                account_id=ctx.account_id,
                collection_name=collection_name,
                schema_fingerprint=schema_fingerprint,
                facts=facts,
                findings=all_findings,
            )
    return result
