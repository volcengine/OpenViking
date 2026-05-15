from __future__ import annotations

import json
import re
import hashlib
from itertools import combinations
from pathlib import Path
from typing import Any


CATEGORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*:[A-Za-z0-9_][A-Za-z0-9_-]*$")


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


def _as_int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, int] = {}
    for key, raw_value in value.items():
        int_value = _as_int(raw_value, 0)
        if int_value:
            parsed[str(key)] = int_value
    return parsed


def _as_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, str] = {}
    for key, raw_value in value.items():
        text = str(raw_value or "").strip()
        if text:
            parsed[str(key)] = text
    return parsed


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _score_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _slug_identity(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _category_id(category: dict[str, Any]) -> str | None:
    catalog_match = category.get("catalog_match") if isinstance(category.get("catalog_match"), dict) else {}
    matched_id = str(catalog_match.get("matched_category_id") or "").strip()
    if matched_id:
        return matched_id
    category1 = str(category.get("category1") or "").strip()
    category2 = str(category.get("category2") or "").strip()
    if category1 and category2:
        return f"{category1}:{category2}"
    return category1 or None


def _malformed_matched_category_id(row: dict[str, Any]) -> str | None:
    category = row.get("category") if isinstance(row.get("category"), dict) else {}
    catalog_match = category.get("catalog_match") if isinstance(category.get("catalog_match"), dict) else {}
    matched_id = str(catalog_match.get("matched_category_id") or "").strip()
    if not matched_id:
        return None
    if not CATEGORY_ID_PATTERN.fullmatch(matched_id):
        return matched_id
    return None


def _compact_annotation(row: dict[str, Any]) -> dict[str, Any]:
    category = row.get("category") if isinstance(row.get("category"), dict) else {}
    ranking = row.get("ranking_features") if isinstance(row.get("ranking_features"), dict) else {}
    if not category and not ranking:
        return {}
    category_id = _category_id(category) or _category_id(ranking)
    payload = {
        "matched": True,
        "category_id": category_id,
        "category1": category.get("category1") or ranking.get("category1"),
        "category2": category.get("category2") or ranking.get("category2"),
        "category3": category.get("category3") or ranking.get("category3"),
        "category_source": category.get("category_source") or ranking.get("category_source") or "annotation_sidecar",
        "confidence": category.get("confidence") or ranking.get("confidence"),
        "annotation_id": row.get("annotation_id") or row.get("request_id"),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _identity_variants(value: Any) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    variants = {text, _slug_identity(text)}
    for marker in ("/memories/", "_memories_"):
        if marker not in text:
            continue
        suffix = text.split(marker, 1)[1].strip("/_")
        if not suffix:
            continue
        variants.update(
            {
                suffix,
                _slug_identity(suffix),
                f"memories/{suffix}",
                _slug_identity(f"memories/{suffix}"),
                Path(suffix).name,
                _slug_identity(Path(suffix).name),
            }
        )
    return {variant for variant in variants if variant}


def _annotation_paths(raw: Any, *, repo_root: Path) -> list[Path]:
    values: list[Any] = []
    if isinstance(raw, dict):
        for item in raw.values():
            if isinstance(item, list):
                values.extend(item)
            else:
                values.append(item)
    elif isinstance(raw, list):
        values.extend(raw)
    elif raw:
        values.append(raw)
    paths: list[Path] = []
    for value in values:
        if not value:
            continue
        path = Path(str(value)).expanduser()
        paths.append(path if path.is_absolute() else repo_root / path)
    return paths


def _load_annotation_index(paths: list[Path]) -> dict[str, Any]:
    index: dict[str, Any] = {
        "by_key": {},
        "loaded_files": [],
        "load_errors": [],
        "query_count": 0,
        "memory_count": 0,
    }
    for path in paths:
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
                    index["load_errors"].append({"path": str(path), "line": line_number, "error": str(exc)})
                    continue
                if not isinstance(row, dict):
                    continue
                malformed_category_id = _malformed_matched_category_id(row)
                if malformed_category_id:
                    index["load_errors"].append(
                        {
                            "path": str(path),
                            "line": line_number,
                            "error": (
                                "invalid matched_category_id "
                                f"{malformed_category_id!r}; expected '<category1>:<category2>'"
                            ),
                        }
                    )
                    continue
                subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
                subject_type = str(subject.get("subject_type") or "").strip()
                if subject_type == "query":
                    index["query_count"] += 1
                elif subject_type == "memory":
                    index["memory_count"] += 1
                for key in (
                    row.get("annotation_id"),
                    row.get("request_id"),
                    subject.get("subject_id"),
                    subject.get("subject_ref"),
                ):
                    for variant in _identity_variants(key):
                        index["by_key"][variant] = row
                loaded += 1
        index["loaded_files"].append({"path": str(path), "rows": loaded})
    return index


def _lookup_annotation(index: dict[str, Any], keys: list[Any]) -> dict[str, Any] | None:
    by_key = index.get("by_key") if isinstance(index.get("by_key"), dict) else {}
    for key in keys:
        for variant in _identity_variants(key):
            row = by_key.get(variant)
            if isinstance(row, dict):
                return row
    return None


def _query_lookup_keys(signature: str, *, include_hash: str | None = None) -> list[str]:
    signature_slug = _slug_identity(signature)
    keys = [
        signature,
        signature_slug,
        f"tau2_query_signature_{signature_slug}",
        f"query:tau2_query_signature_{signature_slug}",
    ]
    if include_hash:
        keys.extend([include_hash, f"query:{include_hash}"])
    return keys


def _write_tool_names_from_query(query: str) -> list[str]:
    first_line = query.splitlines()[0] if query else ""
    prefix = "Before executing write-like tool call(s):"
    tool_blob = first_line.split(prefix, 1)[1] if prefix in first_line else first_line
    return sorted(set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", tool_blob)))


def _write_tool_signature(domain: str, tools: list[str]) -> str:
    return "|".join(["tau2", domain, "pre_write_action", "tools=" + ",".join(sorted(tools))])


def _query_signature(domain: str, decision_node: str, query: str) -> str:
    if decision_node == "before_write_tool_call":
        return _write_tool_signature(domain, _write_tool_names_from_query(query))
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return "|".join(["tau2", domain, decision_node, f"query_sha256={query_hash}"])


def _query_signature_candidates(domain: str, decision_node: str, query: str) -> list[str]:
    primary = _query_signature(domain, decision_node, query)
    if decision_node != "before_write_tool_call":
        return [primary]
    tools = _write_tool_names_from_query(query)
    if len(tools) <= 1:
        return [primary]
    signatures = [primary]
    for size in range(len(tools) - 1, 0, -1):
        for subset in combinations(tools, size):
            signature = _write_tool_signature(domain, list(subset))
            if signature not in signatures:
                signatures.append(signature)
    return signatures


class CategoryReranker:
    def __init__(
        self,
        *,
        enabled: bool,
        apply_nodes: set[str],
        annotation_index: dict[str, Any],
        load_report: dict[str, Any],
        retrieve_limit: int | None,
        inject_limit: int | None,
        retrieve_limits: dict[str, int] | None,
        inject_limits: dict[str, int] | None,
        mismatch_policy: str,
        mismatch_policies: dict[str, str] | None,
        positive_match_required: bool,
        no_match_policy: str,
        missing_query_policy: str,
        search_score_weight: float,
    ) -> None:
        self.enabled = enabled
        self.apply_nodes = apply_nodes
        self.annotation_index = annotation_index
        self.load_report = load_report
        self.retrieve_limit = retrieve_limit
        self.inject_limit = inject_limit
        self.retrieve_limits = retrieve_limits or {}
        self.inject_limits = inject_limits or {}
        self.mismatch_policy = mismatch_policy
        self.mismatch_policies = mismatch_policies or {}
        self.positive_match_required = positive_match_required
        self.no_match_policy = no_match_policy
        self.missing_query_policy = missing_query_policy
        self.search_score_weight = search_score_weight

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None, *, repo_root: Path) -> "CategoryReranker":
        payload = payload if isinstance(payload, dict) else {}
        enabled = _as_bool(payload.get("enabled"), default=False)
        apply_nodes = set(_as_list(payload.get("apply_nodes")) or ["before_write_tool_call"])
        if enabled:
            annotation_files = _annotation_paths(
                payload.get("annotation_files") or payload.get("category_annotation_files"),
                repo_root=repo_root,
            )
            if not annotation_files:
                raise ValueError(
                    "category rerank requires LLM-generated annotation_files; "
                    "live query-to-category mapping is not supported"
                )
            annotation_index = _load_annotation_index(annotation_files)
            if annotation_index.get("load_errors"):
                raise ValueError(f"category rerank sidecar failed to load: {annotation_index['load_errors']}")
            if not annotation_index.get("query_count") or not annotation_index.get("memory_count"):
                raise ValueError(
                    "category rerank sidecar must contain both query and memory annotations: "
                    f"query_count={annotation_index.get('query_count')} "
                    f"memory_count={annotation_index.get('memory_count')}"
                )
            load_report = {
                "loaded": True,
                "source": "annotation_files",
                "loaded_files": annotation_index.get("loaded_files") or [],
                "query_count": annotation_index.get("query_count") or 0,
                "memory_count": annotation_index.get("memory_count") or 0,
                "errors": [],
            }
        else:
            annotation_index = {"by_key": {}, "loaded_files": [], "load_errors": [], "query_count": 0, "memory_count": 0}
            load_report = {
                "path": None,
                "loaded": False,
                "domain_count": 0,
                "category_count": 0,
                "errors": [],
            }
        mismatch_policy = str(payload.get("mismatch_policy") or "").strip()
        mismatch_policies = _as_str_map(payload.get("mismatch_policies"))
        positive_match_policies = {
            "keep_positive_match_drop_mismatch",
            "positive_match_only",
            "positive_priority_fill",
            "strict_pair_match_only",
        }
        positive_match_required = _as_bool(
            payload.get("positive_match_required"),
            default=(mismatch_policy in positive_match_policies)
            or any(policy in positive_match_policies for policy in mismatch_policies.values()),
        )
        if not mismatch_policy and positive_match_required:
            mismatch_policy = "keep_positive_match_drop_mismatch"
        missing_query_policy = str(payload.get("missing_query_policy") or "fail_fast")
        if missing_query_policy not in {"fail_fast", "base_rank", "skip_injection"}:
            raise ValueError(
                "category rerank missing_query_policy must be one of "
                "'fail_fast', 'base_rank', or 'skip_injection'"
            )
        return cls(
            enabled=enabled,
            apply_nodes=apply_nodes,
            annotation_index=annotation_index,
            load_report=load_report,
            retrieve_limit=_as_int(payload.get("retrieve_limit"), 0) or None,
            inject_limit=_as_int(payload.get("inject_limit"), 0) or None,
            retrieve_limits=_as_int_map(payload.get("retrieve_limits")),
            inject_limits=_as_int_map(payload.get("inject_limits")),
            mismatch_policy=mismatch_policy or "none",
            mismatch_policies=mismatch_policies,
            positive_match_required=positive_match_required,
            no_match_policy=str(payload.get("no_match_policy") or "skip_injection"),
            missing_query_policy=missing_query_policy,
            search_score_weight=float(payload.get("search_score_weight") or 0.0),
        )

    def _retrieve_limit(self, decision_node: str) -> int | None:
        return self.retrieve_limits.get(decision_node) or self.retrieve_limit

    def _inject_limit(self, decision_node: str, base_limit: int) -> int:
        return self.inject_limits.get(decision_node) or self.inject_limit or base_limit

    def _mismatch_policy(self, decision_node: str) -> str:
        return self.mismatch_policies.get(decision_node) or self.mismatch_policy

    def search_limit(self, base_limit: int, *, decision_node: str) -> int:
        if self.enabled and decision_node in self.apply_nodes:
            retrieve_limit = self._retrieve_limit(decision_node)
            if retrieve_limit:
                return max(base_limit, retrieve_limit)
        return base_limit

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "apply_nodes": sorted(self.apply_nodes),
            "retrieve_limit": self.retrieve_limit,
            "inject_limit": self.inject_limit,
            "retrieve_limits": dict(self.retrieve_limits),
            "inject_limits": dict(self.inject_limits),
            "mismatch_policy": self.mismatch_policy,
            "mismatch_policies": dict(self.mismatch_policies),
            "positive_match_required": self.positive_match_required,
            "no_match_policy": self.no_match_policy,
            "missing_query_policy": self.missing_query_policy,
            "search_score_weight": self.search_score_weight,
            "sidecar": self.load_report,
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
        mismatch_policy = self._mismatch_policy(decision_node)
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
                "retrieve_limit": self._retrieve_limit(decision_node),
                "inject_limit": self._inject_limit(decision_node, base_limit),
                "mismatch_policy": mismatch_policy,
                "mismatch_policies": dict(self.mismatch_policies),
                "no_match_policy": self.no_match_policy,
                "missing_query_policy": self.missing_query_policy,
                "positive_match_required": self.positive_match_required,
                "selection_policy": "score_sort",
                "sidecar": self.load_report,
                "loaded_files": _loaded_files(self.load_report),
                "load_errors": self.load_report.get("errors") or [],
            }
            return selected, trace_rows, diagnostics

        domain_key = str(domain).lower()
        query_signatures = _query_signature_candidates(domain_key, decision_node, query)
        query_signature = query_signatures[0]
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        query_annotations: list[dict[str, Any]] = []
        matched_query_signatures: list[str] = []
        missing_query_signatures: list[str] = []
        for index, signature in enumerate(query_signatures):
            query_row = _lookup_annotation(
                self.annotation_index,
                _query_lookup_keys(signature, include_hash=query_hash if index == 0 else None),
            )
            if not isinstance(query_row, dict):
                missing_query_signatures.append(signature)
                continue
            query_annotation = _compact_annotation(query_row)
            if not query_annotation:
                raise ValueError(
                    "empty category query sidecar annotation: "
                    f"domain={domain_key} decision_node={decision_node} "
                    f"query_signature={signature}"
                )
            query_annotation = dict(query_annotation)
            query_annotation["query_signature"] = signature
            query_annotations.append(query_annotation)
            matched_query_signatures.append(signature)
        if not query_annotations:
            if self.missing_query_policy in {"base_rank", "skip_injection"}:
                selected = rows[:base_limit] if self.missing_query_policy == "base_rank" else []
                decision = (
                    "missing_query_sidecar_base_rank"
                    if self.missing_query_policy == "base_rank"
                    else "missing_query_sidecar_skip_injection"
                )
                trace_rows = _mark_selected(rows, selected, decision=decision)
                diagnostics = {
                    "enabled": True,
                    "applied": False,
                    "decision_node": decision_node,
                    "decision": decision,
                    "apply_nodes": sorted(self.apply_nodes),
                    "raw_candidate_count": len(rows),
                    "selected_count": len(selected),
                    "retrieve_limit": self._retrieve_limit(decision_node),
                    "inject_limit": self._inject_limit(decision_node, base_limit),
                    "mismatch_policy": mismatch_policy,
                    "mismatch_policies": dict(self.mismatch_policies),
                    "positive_match_required": self.positive_match_required,
                    "no_match_policy": self.no_match_policy,
                    "missing_query_policy": self.missing_query_policy,
                    "query_sidecar_coverage": "missing",
                    "query_signature": query_signature,
                    "query_signature_candidates": query_signatures,
                    "matched_query_signatures": matched_query_signatures,
                    "missing_query_signatures": missing_query_signatures,
                    "selection_policy": self.missing_query_policy,
                    "sidecar": self.load_report,
                    "loaded_files": _loaded_files(self.load_report),
                    "load_errors": self.load_report.get("errors") or [],
                }
                return selected, trace_rows, diagnostics
            raise ValueError(
                "missing category query sidecar annotation: "
                f"domain={domain_key} decision_node={decision_node} "
                f"query_signature={query_signature}"
            )
        query_annotation = _merge_query_annotations(query_annotations)
        query_sidecar_coverage = "covered" if matched_query_signatures and matched_query_signatures[0] == query_signature else "partial"
        scored = []
        candidates = []
        for index, row in enumerate(rows):
            uri = str(row.get("uri") or "")
            base_uri = uri.split("#", 1)[0]
            memory_row = _lookup_annotation(
                self.annotation_index,
                [
                    uri,
                    base_uri,
                    Path(base_uri).name,
                ],
            )
            if not isinstance(memory_row, dict):
                raise ValueError(
                    "missing category memory sidecar annotation: "
                    f"domain={domain_key} decision_node={decision_node} uri={uri}"
                )
            memory_annotation = _compact_annotation(memory_row)
            if not memory_annotation:
                raise ValueError(
                    "empty category memory sidecar annotation: "
                    f"domain={domain_key} decision_node={decision_node} uri={uri}"
                )
            best_query_annotation: dict[str, Any] | None = None
            best_score = float("-inf")
            best_reasons: list[str] = []
            best_match_flags: dict[str, bool] = {}
            for current_query in query_annotations:
                score, reasons, match_flags = _candidate_score(
                    current_query,
                    memory_annotation,
                    original_rank=index + 1,
                    original_score=_score_value(row.get("score")) * self.search_score_weight,
                )
                score_key = (
                    score,
                    1 if match_flags.get("category2_match") else 0,
                    1 if match_flags.get("category1_match") else 0,
                )
                best_key = (
                    best_score,
                    1 if best_match_flags.get("category2_match") else 0,
                    1 if best_match_flags.get("category1_match") else 0,
                )
                if score_key > best_key:
                    best_score = score
                    best_reasons = reasons
                    best_match_flags = match_flags
                    best_query_annotation = current_query
            candidate = {
                "uri": row.get("uri"),
                "raw_rank": index + 1,
                "raw_score": row.get("score"),
                "category_score": best_score,
                "category_rerank_reasons": best_reasons,
                "query_category": best_query_annotation,
                "memory_category": memory_annotation,
                **best_match_flags,
            }
            candidates.append(candidate)
            scored.append((best_score, -index, row, candidate))

        sorted_scored = sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)
        positive_level = "none"
        if any(item[3].get("category2_match") for item in sorted_scored):
            positive_level = "category2"
        elif any(item[3].get("category1_match") for item in sorted_scored):
            positive_level = "category1"

        decision = "soft_reranked"
        filtered = sorted_scored
        dropped_mismatch_count = 0
        inject_limit = self._inject_limit(decision_node, base_limit)
        priority_fill: dict[str, Any] = {"applied": False}
        if mismatch_policy == "strict_pair_match_only":
            before_count = len(sorted_scored)
            filtered = [
                item
                for item in sorted_scored
                if item[3].get("category1_match") and item[3].get("category2_match")
            ]
            if filtered:
                decision = "soft_reranked_keep_strict_pair_matches"
            elif self.no_match_policy == "skip_injection":
                filtered = []
                decision = "no_strict_pair_category_match_skip_injection"
            dropped_mismatch_count = before_count - len(filtered)
        elif mismatch_policy in {"keep_positive_match_drop_mismatch", "positive_match_only"}:
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
        elif mismatch_policy == "positive_priority_fill":
            before_count = len(sorted_scored)
            if positive_level in {"category1", "category2"}:
                filtered, priority_fill = _priority_fill(
                    query_annotation,
                    sorted_scored,
                    inject_limit=inject_limit,
                )
                decision = "soft_reranked_positive_priority_fill"
            elif self.no_match_policy == "skip_injection":
                filtered = []
                decision = "no_positive_category_match_skip_injection"
            dropped_mismatch_count = before_count - len(filtered)
        elif mismatch_policy == "drop_when_match_available":
            has_positive_match = positive_level in {"category1", "category2"}
            if has_positive_match:
                before_count = len(sorted_scored)
                guarded = [
                    item for item in sorted_scored if not item[3].get("category_explicit_mismatch")
                ]
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
            "retrieve_limit": self._retrieve_limit(decision_node),
            "inject_limit": inject_limit,
            "mismatch_policy": mismatch_policy,
            "mismatch_policies": dict(self.mismatch_policies),
            "positive_match_required": self.positive_match_required,
            "positive_match_level": positive_level,
            "no_match_policy": self.no_match_policy,
            "missing_query_policy": self.missing_query_policy,
            "selection_policy": "score_sort",
            "dropped_mismatch_count": dropped_mismatch_count,
            "priority_fill": priority_fill,
            "kept_before_cap_ids": [str(item[2].get("uri") or "") for item in filtered],
            "query_category": query_annotation,
            "query_signature": query_signature,
            "query_signature_candidates": query_signatures,
            "matched_query_signatures": matched_query_signatures,
            "missing_query_signatures": missing_query_signatures,
            "query_sidecar_coverage": query_sidecar_coverage,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "sidecar": self.load_report,
            "loaded_files": _loaded_files(self.load_report),
            "load_errors": self.load_report.get("errors") or [],
        }
        return selected, trace_rows, diagnostics


def _loaded_files(load_report: dict[str, Any]) -> list[str]:
    loaded_files = load_report.get("loaded_files")
    if isinstance(loaded_files, list):
        return [str(row.get("path") if isinstance(row, dict) else row) for row in loaded_files]
    if load_report.get("loaded") and load_report.get("path"):
        return [str(load_report["path"])]
    return []


def _ordered_values(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return []


def _merge_query_annotations(query_annotations: list[dict[str, Any]]) -> dict[str, Any]:
    if not query_annotations:
        return {}
    merged: dict[str, Any] = {
        "matched": True,
        "category_source": "multi_query_sidecar" if len(query_annotations) > 1 else query_annotations[0].get("category_source"),
        "annotation_id": ",".join(str(row.get("annotation_id") or "") for row in query_annotations if row.get("annotation_id")),
        "query_signatures": [row.get("query_signature") for row in query_annotations if row.get("query_signature")],
    }
    for key in ("category_id", "category1", "category2", "category3"):
        values: list[str] = []
        for row in query_annotations:
            for value in _ordered_values(row, key):
                if value not in values:
                    values.append(value)
        if values:
            merged[key] = values[0] if len(values) == 1 else values
    confidences = [row.get("confidence") for row in query_annotations if isinstance(row.get("confidence"), (int, float))]
    if confidences:
        merged["confidence"] = max(confidences)
    return merged


def _values(payload: dict[str, Any], key: str) -> set[str]:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return {value.strip()}
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _priority_fill(
    query: dict[str, Any],
    sorted_scored: list[tuple[float, int, dict[str, Any], dict[str, Any]]],
    *,
    inject_limit: int,
) -> tuple[list[tuple[float, int, dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    if inject_limit <= 0 or not sorted_scored:
        return sorted_scored, {"applied": False, "reason": "empty_or_zero_limit"}

    selected: list[tuple[float, int, dict[str, Any], dict[str, Any]]] = []
    selected_indexes: set[int] = set()
    fill_steps: list[dict[str, Any]] = []

    def pick(level: str, category: str) -> None:
        if len(selected) >= inject_limit:
            return
        for index, row in enumerate(sorted_scored):
            if index in selected_indexes:
                continue
            memory_category = row[3].get("memory_category")
            memory_category = memory_category if isinstance(memory_category, dict) else {}
            if category not in _values(memory_category, level):
                continue
            selected.append(row)
            selected_indexes.add(index)
            fill_steps.append(
                {
                    "level": level,
                    "category": category,
                    "uri": str(row[2].get("uri") or ""),
                    "category_score": row[0],
                }
            )
            return

    for category_id in _ordered_values(query, "category_id"):
        pick("category_id", category_id)
    for category1 in _ordered_values(query, "category1"):
        pick("category1", category1)

    positive_remaining = [
        row
        for index, row in enumerate(sorted_scored)
        if index not in selected_indexes
        and (row[3].get("category1_match") or row[3].get("category2_match"))
    ]
    other_remaining = [
        row
        for index, row in enumerate(sorted_scored)
        if index not in selected_indexes
        and not (row[3].get("category1_match") or row[3].get("category2_match"))
    ]
    return selected + positive_remaining + other_remaining, {
        "applied": bool(selected),
        "inject_limit": inject_limit,
        "selected_count": len(selected),
        "fill_steps": fill_steps,
    }


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
    query_ids = _values(query, "category_id")
    memory_ids = _values(memory, "category_id")
    query_c1 = _values(query, "category1")
    query_c2 = _values(query, "category2")
    memory_c1 = _values(memory, "category1")
    memory_c2 = _values(memory, "category2")
    category1_match = bool(query_c1 and memory_c1 and query_c1 & memory_c1)
    category_pair_match = bool(query_ids and memory_ids and query_ids & memory_ids)
    category2_label_match = bool(query_c2 and memory_c2 and query_c2 & memory_c2)
    category2_match = category_pair_match or bool(
        not query_ids and not memory_ids and category1_match and category2_label_match
    )
    if category2_match:
        score += 100.0
        reasons.append("category_pair_match")
    if category1_match:
        score += 40.0
        reasons.append("category1_match")
    if category2_label_match and not category2_match:
        reasons.append("category2_label_match_without_pair")
    if (query_ids and memory_ids and not category_pair_match) or (
        not query_ids and not memory_ids and query_c2 and memory_c2 and not category2_match
    ):
        score -= 5.0
        reasons.append("category_pair_mismatch_downrank")
    if query_c1 and memory_c1 and not category1_match:
        score -= 20.0
        reasons.append("category1_mismatch_downrank")
    if (query_c1 or query_c2) and not (memory_c1 or memory_c2):
        score -= 2.0
        reasons.append("missing_memory_category")
    return (
        score,
        reasons,
        {
            "category1_match": category1_match,
            "category2_match": category2_match,
            "category_pair_match": category_pair_match,
            "category2_label_match": category2_label_match,
            "category_explicit_mismatch": bool(
                (query_c1 and memory_c1 and not category1_match)
                or (query_ids and memory_ids and not category_pair_match)
                or (
                    not query_ids
                    and not memory_ids
                    and query_c2
                    and memory_c2
                    and not category2_match
                )
            ),
        },
    )


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
        traced["injected"] = bool(
            traced["selected_for_injection"] and int(row.get("text_chars") or 0) > 0
        )
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
            candidate_query_category = candidate.get("query_category")
            candidate_query_category = candidate_query_category if isinstance(candidate_query_category, dict) else query_category
            if candidate_query_category:
                traced["query_category1_prompt"] = candidate_query_category.get("category1")
                traced["query_category2_prompt"] = candidate_query_category.get("category2")
                traced["query_category_source_prompt"] = candidate_query_category.get("category_source")
                traced["query_category_confidence_prompt"] = candidate_query_category.get("confidence")
                traced["query_category_signature"] = candidate_query_category.get("query_signature")
            traced["category1_match"] = candidate.get("category1_match")
            traced["category2_match"] = candidate.get("category2_match")
            traced["category_pair_match"] = candidate.get("category_pair_match")
            traced["category2_label_match"] = candidate.get("category2_label_match")
            traced["category_explicit_mismatch"] = candidate.get("category_explicit_mismatch")
        trace_rows.append(traced)
    return trace_rows
