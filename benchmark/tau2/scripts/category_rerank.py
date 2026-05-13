from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _normalize(text: Any) -> str:
    lowered = str(text or "").lower()
    return re.sub(r"[^a-z0-9_]+", " ", lowered)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _score_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class CategoryEntry:
    category_id: str
    category1: str
    category2: str
    query_triggers: tuple[str, ...]
    memory_triggers: tuple[str, ...]
    negative_triggers: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CategoryEntry | None":
        category1 = str(payload.get("category1") or "").strip()
        category2 = str(payload.get("category2") or "").strip()
        category_id = str(payload.get("category_id") or "").strip()
        if not category_id and category1:
            category_id = category1 if not category2 else f"{category1}:{category2}"
        if not category_id or not category1:
            return None
        query_triggers = tuple(
            dict.fromkeys(
                _as_list(payload.get("query_triggers"))
                + _as_list(payload.get("triggers"))
                + [category_id, category1, category2]
            )
        )
        memory_triggers = tuple(
            dict.fromkeys(
                _as_list(payload.get("memory_triggers"))
                + _as_list(payload.get("triggers"))
                + [category_id, category1, category2]
            )
        )
        return cls(
            category_id=category_id,
            category1=category1,
            category2=category2,
            query_triggers=query_triggers,
            memory_triggers=memory_triggers,
            negative_triggers=tuple(_as_list(payload.get("negative_triggers"))),
        )


class CategoryReranker:
    def __init__(
        self,
        *,
        enabled: bool,
        apply_nodes: set[str],
        catalog: dict[str, list[CategoryEntry]],
        load_report: dict[str, Any],
        annotation_index: dict[str, Any],
        retrieve_limit: int | None,
        inject_limit: int | None,
        mismatch_policy: str,
        positive_match_required: bool,
        no_match_policy: str,
        search_score_weight: float,
    ) -> None:
        self.enabled = enabled
        self.apply_nodes = apply_nodes
        self.catalog = catalog
        self.load_report = load_report
        self.annotation_index = annotation_index
        self.retrieve_limit = retrieve_limit
        self.inject_limit = inject_limit
        self.mismatch_policy = mismatch_policy
        self.positive_match_required = positive_match_required
        self.no_match_policy = no_match_policy
        self.search_score_weight = search_score_weight

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None, *, repo_root: Path) -> "CategoryReranker":
        payload = payload if isinstance(payload, dict) else {}
        enabled = _as_bool(payload.get("enabled"), default=False)
        apply_nodes = set(_as_list(payload.get("apply_nodes")) or ["before_write_tool_call"])
        if enabled:
            catalog, load_report = _load_catalog(payload.get("catalog_path"), repo_root=repo_root)
            if not load_report.get("loaded"):
                raise ValueError(f"category rerank catalog failed to load: {load_report}")
            annotation_index = _load_annotation_index(payload, repo_root=repo_root)
        else:
            catalog = {}
            load_report = {
                "path": None,
                "loaded": False,
                "domain_count": 0,
                "category_count": 0,
                "errors": [],
            }
            annotation_index = _empty_annotation_index()
        mismatch_policy = str(payload.get("mismatch_policy") or "").strip()
        positive_match_required = _as_bool(
            payload.get("positive_match_required"),
            default=mismatch_policy in {"keep_positive_match_drop_mismatch", "positive_match_only"},
        )
        if not mismatch_policy and positive_match_required:
            mismatch_policy = "keep_positive_match_drop_mismatch"
        return cls(
            enabled=enabled,
            apply_nodes=apply_nodes,
            catalog=catalog,
            load_report=load_report,
            annotation_index=annotation_index,
            retrieve_limit=_as_int(payload.get("retrieve_limit"), 0) or None,
            inject_limit=_as_int(payload.get("inject_limit"), 0) or None,
            mismatch_policy=mismatch_policy or "none",
            positive_match_required=positive_match_required,
            no_match_policy=str(payload.get("no_match_policy") or "skip_injection"),
            search_score_weight=float(payload.get("search_score_weight") or 0.0),
        )

    def search_limit(self, base_limit: int, *, decision_node: str) -> int:
        if self.enabled and decision_node in self.apply_nodes and self.retrieve_limit:
            return max(base_limit, self.retrieve_limit)
        return base_limit

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "apply_nodes": sorted(self.apply_nodes),
            "retrieve_limit": self.retrieve_limit,
            "inject_limit": self.inject_limit,
            "mismatch_policy": self.mismatch_policy,
            "positive_match_required": self.positive_match_required,
            "no_match_policy": self.no_match_policy,
            "search_score_weight": self.search_score_weight,
            "catalog": self.load_report,
            "annotation_sidecar": _annotation_summary(self.annotation_index),
        }

    def select(
        self,
        *,
        domain: str,
        query: str,
        rows: list[dict[str, Any]],
        decision_node: str,
        base_limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        if not self.enabled or decision_node not in self.apply_nodes:
            selected = rows[:base_limit]
            trace_rows = _mark_selected(rows, selected, decision="base_rank")
            diagnostics = {
                "enabled": self.enabled,
                "applied": False,
                "decision_node": decision_node,
                "decision": "node_not_enabled" if self.enabled else "disabled",
                "apply_nodes": sorted(self.apply_nodes),
                "raw_candidate_count": len(rows),
                "selected_count": len(selected),
                "retrieve_limit": self.retrieve_limit,
                "inject_limit": self.inject_limit or base_limit,
                "mismatch_policy": self.mismatch_policy,
                "no_match_policy": self.no_match_policy,
                "positive_match_required": self.positive_match_required,
                "selection_policy": "score_sort",
                "catalog": self.load_report,
                "annotation_sidecar": _annotation_summary(self.annotation_index),
                "loaded_files": _loaded_files(self.load_report, self.annotation_index),
                "load_errors": _load_errors(self.load_report, self.annotation_index),
            }
            return selected, trace_rows, diagnostics

        domain_entries = self.catalog.get(str(domain).lower(), [])
        query_annotation = _query_annotation(
            self.annotation_index,
            domain_entries,
            domain=domain,
            query=query,
        )
        scored = []
        candidates = []
        for index, row in enumerate(rows):
            memory_text = "\n".join(
                [
                    str(row.get("uri") or ""),
                    str(row.get("_text") or ""),
                    str(row.get("level") or ""),
                ]
            )
            memory_annotation = _memory_annotation(
                self.annotation_index,
                domain_entries,
                row=row,
                text=memory_text,
            )
            score, reasons, match_flags = _candidate_score(
                query_annotation,
                memory_annotation,
                original_rank=index + 1,
                original_score=_score_value(row.get("score")) * self.search_score_weight,
            )
            candidate = {
                "uri": row.get("uri"),
                "raw_rank": index + 1,
                "raw_score": row.get("score"),
                "category_score": score,
                "category_rerank_reasons": reasons,
                "memory_category": memory_annotation,
                **match_flags,
            }
            candidates.append(candidate)
            scored.append((score, -index, row, candidate))

        sorted_scored = sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)
        positive_level = "none"
        if any(item[3].get("category2_match") for item in sorted_scored):
            positive_level = "category2"
        elif any(item[3].get("category1_match") for item in sorted_scored):
            positive_level = "category1"

        decision = "soft_reranked"
        filtered = sorted_scored
        dropped_mismatch_count = 0
        if self.mismatch_policy in {"keep_positive_match_drop_mismatch", "positive_match_only"}:
            before_count = len(sorted_scored)
            if positive_level == "category2":
                filtered = [item for item in sorted_scored if item[3].get("category2_match")]
                decision = "soft_reranked_keep_category2_matches"
            elif positive_level == "category1":
                filtered = [item for item in sorted_scored if item[3].get("category1_match")]
                decision = "soft_reranked_keep_category1_matches"
            elif self.no_match_policy == "skip_injection":
                filtered = []
                decision = "no_positive_category_match_skip_injection"
            dropped_mismatch_count = before_count - len(filtered)
        elif self.mismatch_policy == "drop_when_match_available":
            has_positive_match = positive_level in {"category1", "category2"}
            if has_positive_match:
                before_count = len(sorted_scored)
                guarded = [item for item in sorted_scored if not item[3].get("category_explicit_mismatch")]
                if guarded:
                    filtered = guarded
                    decision = "soft_reranked_with_mismatch_guard"
                    dropped_mismatch_count = before_count - len(filtered)
            elif self.no_match_policy == "skip_injection":
                dropped_mismatch_count = len(sorted_scored)
                filtered = []
                decision = "no_positive_category_match_skip_injection"
        elif self.no_match_policy == "skip_injection" and positive_level == "none":
            dropped_mismatch_count = len(sorted_scored)
            filtered = []
            decision = "no_positive_category_match_skip_injection"

        inject_limit = self.inject_limit or base_limit
        selected = [item[2] for item in filtered[:inject_limit]]
        trace_rows = _mark_selected(
            rows,
            selected,
            decision=decision,
            kept_before_cap=[item[2] for item in filtered],
            candidate_by_uri={
                str(candidate.get("uri") or ""): candidate for candidate in candidates
            },
            query_category=query_annotation,
        )
        diagnostics = {
            "enabled": True,
            "applied": True,
            "decision_node": decision_node,
            "decision": decision,
            "raw_candidate_count": len(rows),
            "selected_count": len(selected),
            "retrieve_limit": self.retrieve_limit,
            "inject_limit": inject_limit,
            "mismatch_policy": self.mismatch_policy,
            "positive_match_required": self.positive_match_required,
            "positive_match_level": positive_level,
            "no_match_policy": self.no_match_policy,
            "selection_policy": "score_sort",
            "dropped_mismatch_count": dropped_mismatch_count,
            "kept_before_cap_ids": [str(item[2].get("uri") or "") for item in filtered],
            "query_category": query_annotation,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "catalog": self.load_report,
            "annotation_sidecar": _annotation_summary(self.annotation_index),
            "loaded_files": _loaded_files(self.load_report, self.annotation_index),
            "load_errors": _load_errors(self.load_report, self.annotation_index),
        }
        return selected, trace_rows, diagnostics


def _loaded_files(
    load_report: dict[str, Any],
    annotation_index: dict[str, Any] | None = None,
) -> list[str]:
    files: list[str] = []
    if load_report.get("loaded") and load_report.get("path"):
        files.append(str(load_report["path"]))
    if isinstance(annotation_index, dict):
        files.extend(str(row.get("path")) for row in annotation_index.get("loaded_files") or [])
    return files


def _load_errors(
    load_report: dict[str, Any],
    annotation_index: dict[str, Any] | None = None,
) -> list[Any]:
    errors = list(load_report.get("errors") or [])
    if isinstance(annotation_index, dict):
        errors.extend(annotation_index.get("load_errors") or [])
    return errors


def _empty_annotation_index() -> dict[str, Any]:
    return {
        "by_key": {},
        "loaded_files": [],
        "load_errors": [],
        "row_count": 0,
        "enabled": False,
    }


def _annotation_summary(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(index.get("enabled")),
        "loaded": bool(index.get("loaded_files")),
        "loaded_files": index.get("loaded_files") or [],
        "row_count": index.get("row_count") or 0,
        "key_count": len(index.get("by_key") or {}),
        "load_errors": index.get("load_errors") or [],
    }


def _resolve_path(raw_path: Any, *, repo_root: Path) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path


def _split_path_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [item for raw in value.values() for item in _split_path_text(raw)]
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"[:\n,]", text) if part.strip()]


def _annotation_file_values(payload: dict[str, Any]) -> list[str]:
    values = _split_path_text(payload.get("annotation_files"))
    env_names = _as_list(payload.get("annotation_files_env")) or [
        "OPENVIKING_TAU2_CATEGORY_ANNOTATION_FILES",
        "AGENT_HARNESS_TAU2_CATEGORY_ANNOTATION_FILES",
    ]
    for name in env_names:
        values.extend(_split_path_text(os.environ.get(name)))
    return list(dict.fromkeys(values))


def _category_payload(annotation: dict[str, Any]) -> dict[str, Any]:
    category = annotation.get("category") if isinstance(annotation.get("category"), dict) else {}
    ranking = (
        annotation.get("ranking_features")
        if isinstance(annotation.get("ranking_features"), dict)
        else {}
    )
    catalog_match = (
        category.get("catalog_match") if isinstance(category.get("catalog_match"), dict) else {}
    )
    payload = {
        "subject_type": (
            annotation.get("subject", {}).get("subject_type")
            if isinstance(annotation.get("subject"), dict)
            else None
        ),
        "category_source": category.get("category_source")
        or ranking.get("category_source")
        or "annotation_sidecar",
        "matched": True,
        "primary_category_id": catalog_match.get("matched_category_id"),
        "category1": category.get("category1") or ranking.get("category1"),
        "category2": category.get("category2") or ranking.get("category2"),
        "confidence": category.get("confidence") or ranking.get("confidence"),
        "catalog_match_decision": catalog_match.get("decision"),
        "annotation_id": annotation.get("annotation_id") or annotation.get("request_id"),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _slug_identity(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_.-")
    return cleaned


def _annotation_lookup_keys(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    slug = _slug_identity(text)
    keys = [text, slug]
    if text.startswith("viking://"):
        keys.extend([f"openviking_memory_{slug}", f"openviking_memory_{text}"])
    return list(dict.fromkeys(key for key in keys if key))


def _annotation_index_put(index: dict[str, Any], key: Any, annotation: dict[str, Any]) -> None:
    for candidate in _annotation_lookup_keys(key):
        index["by_key"][candidate] = annotation


def _load_annotation_index(payload: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    index = _empty_annotation_index()
    index["enabled"] = True
    for raw_path in _annotation_file_values(payload):
        path = _resolve_path(raw_path, repo_root=repo_root)
        if not path.is_file():
            index["load_errors"].append({"path": str(path), "error": "file_not_found"})
            continue
        loaded = 0
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    index["load_errors"].append(
                        {"path": str(path), "line": line_number, "error": str(exc)}
                    )
                    continue
                if not isinstance(row, dict):
                    continue
                subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
                for key in (
                    row.get("annotation_id"),
                    row.get("request_id"),
                    subject.get("subject_id"),
                    subject.get("subject_ref"),
                ):
                    _annotation_index_put(index, key, row)
                loaded += 1
        index["row_count"] += loaded
        index["loaded_files"].append({"path": str(path), "rows": loaded})
    return index


def _load_catalog(raw_path: Any, *, repo_root: Path) -> tuple[dict[str, list[CategoryEntry]], dict[str, Any]]:
    report = {
        "path": None,
        "loaded": False,
        "domain_count": 0,
        "category_count": 0,
        "errors": [],
    }
    if not raw_path:
        report["errors"].append("missing_catalog_path")
        return {}, report
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    report["path"] = str(path)
    if not path.is_file():
        report["errors"].append("catalog_file_not_found")
        return {}, report
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        return {}, report

    domains = payload.get("domains") if isinstance(payload, dict) else {}
    catalog: dict[str, list[CategoryEntry]] = {}
    if not isinstance(domains, dict):
        report["errors"].append("catalog_domains_must_be_mapping")
        return {}, report
    for domain, domain_payload in domains.items():
        raw_categories = domain_payload
        if isinstance(domain_payload, dict):
            raw_categories = domain_payload.get("categories") or []
        if not isinstance(raw_categories, list):
            continue
        entries = []
        for row in raw_categories:
            if not isinstance(row, dict):
                continue
            entry = CategoryEntry.from_payload(row)
            if entry:
                entries.append(entry)
        if entries:
            catalog[str(domain).lower()] = entries
    report["loaded"] = bool(catalog)
    report["domain_count"] = len(catalog)
    report["category_count"] = sum(len(entries) for entries in catalog.values())
    return catalog, report


def _annotate_text(
    entries: list[CategoryEntry],
    text: str,
    *,
    trigger_field: str,
    subject_type: str,
) -> dict[str, Any]:
    normalized = _normalize(text)
    matches = []
    for entry in entries:
        triggers = getattr(entry, trigger_field)
        matched = [trigger for trigger in triggers if _normalize(trigger) in normalized]
        negative = [
            trigger for trigger in entry.negative_triggers if _normalize(trigger) in normalized
        ]
        if not matched:
            continue
        score = len(matched) - (0.25 * len(negative))
        matches.append(
            {
                "category_id": entry.category_id,
                "category1": entry.category1,
                "category2": entry.category2,
                "matched_triggers": matched[:8],
                "negative_triggers": negative[:8],
                "score": score,
            }
        )
    matches.sort(key=lambda item: item["score"], reverse=True)
    category1 = list(dict.fromkeys(row["category1"] for row in matches if row["category1"]))
    category2 = list(dict.fromkeys(row["category2"] for row in matches if row["category2"]))
    primary = matches[0] if matches else None
    return {
        "subject_type": subject_type,
        "category_source": "tau2_category_catalog_keyword_match",
        "matched": bool(matches),
        "primary_category_id": primary.get("category_id") if primary else None,
        "category1": category1,
        "category2": category2,
        "confidence": min(1.0, 0.45 + 0.1 * len(matches)) if matches else 0.0,
        "matches": matches[:5],
    }


_WRITE_TOOL_PREFIXES = (
    "toggle_",
    "enable_",
    "disable_",
    "set_",
    "reset_",
    "update_",
    "modify_",
    "cancel_",
    "book_",
    "exchange_",
    "return_",
    "grant_",
    "reboot_",
)


def _lookup_annotation(index: dict[str, Any], keys: list[str], *, subject_type: str) -> dict[str, Any] | None:
    by_key = index.get("by_key") if isinstance(index.get("by_key"), dict) else {}
    for key in keys:
        for candidate in _annotation_lookup_keys(key):
            row = by_key.get(candidate)
            if not isinstance(row, dict):
                continue
            subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
            if subject.get("subject_type") == subject_type:
                return row
    return None


def _query_signature_from_text(domain: str, query: str) -> str | None:
    names = []
    for name in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", query):
        if name.startswith(_WRITE_TOOL_PREFIXES):
            names.append(name)
    if not names:
        return None
    return "|".join(
        [
            "tau2",
            str(domain).strip().lower() or "unknown",
            "pre_write_action",
            "tools=" + ",".join(sorted(set(names))),
        ]
    )


def _query_annotation(
    index: dict[str, Any],
    entries: list[CategoryEntry],
    *,
    domain: str,
    query: str,
) -> dict[str, Any]:
    signature = _query_signature_from_text(domain, query)
    keys = [query]
    if signature:
        signature_slug = _slug_identity(signature)
        keys.extend([signature, signature_slug, f"tau2_query_signature_{signature_slug}"])
    annotation = _lookup_annotation(index, keys, subject_type="query")
    if annotation:
        payload = _category_payload(annotation)
        payload["subject_type"] = "query"
        payload["category_source"] = payload.get("category_source") or "annotation_sidecar"
        if signature:
            payload["query_signature"] = signature
        return payload
    return _annotate_text(
        entries,
        query,
        trigger_field="query_triggers",
        subject_type="query",
    )


def _memory_annotation(
    index: dict[str, Any],
    entries: list[CategoryEntry],
    *,
    row: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    keys = [str(row.get("uri") or ""), str(row.get("memory_id") or "")]
    annotation = _lookup_annotation(index, keys, subject_type="memory")
    if annotation:
        payload = _category_payload(annotation)
        payload["subject_type"] = "memory"
        payload["category_source"] = payload.get("category_source") or "annotation_sidecar"
        return payload
    return _annotate_text(
        entries,
        text,
        trigger_field="memory_triggers",
        subject_type="memory",
    )


def _values(payload: dict[str, Any], key: str) -> set[str]:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return {value.strip()}
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _candidate_score(
    query: dict[str, Any],
    memory: dict[str, Any],
    *,
    original_rank: int,
    original_score: float,
) -> tuple[float, list[str], dict[str, bool]]:
    score = original_score - (original_rank * 0.001)
    reasons = ["original_rank_tiebreak"]
    if original_score:
        reasons.insert(0, "openviking_score")
    query_c1 = _values(query, "category1")
    query_c2 = _values(query, "category2")
    memory_c1 = _values(memory, "category1")
    memory_c2 = _values(memory, "category2")
    category2_match = bool(query_c2 and memory_c2 and query_c2 & memory_c2)
    category1_match = bool(query_c1 and memory_c1 and query_c1 & memory_c1)
    if category2_match:
        score += 100.0
        reasons.append("category2_match")
    if category1_match:
        score += 40.0
        reasons.append("category1_match")
    if query_c2 and memory_c2 and not category2_match:
        score -= 5.0
        reasons.append("category2_mismatch_downrank")
    if query_c1 and memory_c1 and not category1_match:
        score -= 20.0
        reasons.append("category1_mismatch_downrank")
    if (query_c1 or query_c2) and not (memory_c1 or memory_c2):
        score -= 2.0
        reasons.append("missing_memory_category")
    return score, reasons, {
        "category1_match": category1_match,
        "category2_match": category2_match,
        "category_explicit_mismatch": bool(
            (query_c1 and memory_c1 and not category1_match)
            or (query_c2 and memory_c2 and not category2_match)
        ),
    }


def _row_key(row: dict[str, Any]) -> str:
    return str(row.get("uri") or row.get("memory_id") or id(row))


def _mark_selected(
    rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    *,
    decision: str,
    kept_before_cap: list[dict[str, Any]] | None = None,
    candidate_by_uri: dict[str, dict[str, Any]] | None = None,
    query_category: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    selected_keys = {_row_key(row) for row in selected_rows}
    kept_keys = {_row_key(row) for row in (kept_before_cap or selected_rows)}
    trace_rows = []
    for index, row in enumerate(rows, start=1):
        key = _row_key(row)
        traced = _public_row(row)
        traced["raw_rank"] = index
        traced["selected_for_injection"] = key in selected_keys
        traced["injected"] = bool(traced["selected_for_injection"] and int(row.get("text_chars") or 0) > 0)
        if not traced["selected_for_injection"]:
            traced["skipped_reason"] = (
                "category_rerank_inject_limit" if key in kept_keys else "category_rerank"
            )
            if decision == "no_positive_category_match_skip_injection":
                traced["skipped_reason"] = "category_rerank_no_positive_match"
        candidate = (candidate_by_uri or {}).get(str(row.get("uri") or ""))
        if candidate:
            memory_category = candidate.get("memory_category")
            memory_category = memory_category if isinstance(memory_category, dict) else {}
            traced["category_rerank_score"] = candidate.get("category_score")
            traced["category_rerank_reasons"] = candidate.get("category_rerank_reasons")
            traced["memory_category"] = memory_category
            traced["memory_category1_prompt"] = memory_category.get("category1")
            traced["memory_category2_prompt"] = memory_category.get("category2")
            traced["memory_category_source_prompt"] = memory_category.get("category_source")
            traced["memory_category_confidence_prompt"] = memory_category.get("confidence")
            if query_category:
                traced["query_category1_prompt"] = query_category.get("category1")
                traced["query_category2_prompt"] = query_category.get("category2")
                traced["query_category_source_prompt"] = query_category.get("category_source")
                traced["query_category_confidence_prompt"] = query_category.get("confidence")
            traced["category1_match"] = candidate.get("category1_match")
            traced["category2_match"] = candidate.get("category2_match")
            traced["category_explicit_mismatch"] = candidate.get("category_explicit_mismatch")
        trace_rows.append(traced)
    return trace_rows
