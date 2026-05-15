"""Build category annotation requests from an OpenViking TAU-2 corpus.

The script is intentionally self-contained for the OpenViking benchmark PR:
it reads a corpus manifest, renders visible memory files into LLM prompts, and
does not import Agent Harness helpers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = ROOT / "benchmark" / "tau2" / "result" / "category_requests"
DEFAULT_SCHEMA_REF = "memory_category_annotation.v0"

PROMPT_TEMPLATE = """# Memory / Query Category Extraction v0

You are annotating a memory-related subject for retrieval and reranking.

Return strict JSON matching `memory_category_annotation.v0`. Do not include Markdown.

## Goal

Assign reusable categories for both query-side and memory-side ranking:

- `category1`: coarse workflow / outcome / skill / artifact family.
- `category2`: finer applicability class or state boundary.
- `category3`: optional narrow subcategory when clearly useful.
- `category1`, `category2`, and non-null `category3` must be compact reusable ids:
  lowercase `snake_case`, using only letters, numbers, `_`, or `-`; no spaces,
  commas, prose sentences, raw order/user/payment/reservation ids, or dates.
- Each category id part must be at most 64 characters. Put detailed state,
  eligibility, confirmation, and boundary text in `applicability`, not in the id.

The same schema must work for benchmark tasks, worker tasks, tool actions, skills, artifacts, trajectory segments, and memory documents.

## Safety Boundary

- Use only the visible input below.
- Do not infer from hidden gold answers, evaluator criteria, official expected actions, or private IDs.
- Do not preserve order IDs, emails, phone numbers, addresses, account numbers, ticket IDs, or other instance-specific identifiers as category names.
- Prefer stable reusable categories over one-off labels.
- `category1` should be broad enough to group many related subjects, but not so
  broad that it mixes different workflow outcomes. Do not collapse subjects into
  a generic container only because they all mention a user, account, order,
  reservation, document, task, tool, or service request.
- Avoid putting product type, tool detail, exact state, or multi-step procedure
  detail into `category1`; also avoid catch-all labels that only say the subject
  is about management, handling, support, or processing.
- `category2` should be the compact action/state facet under that broad family.
- `category2` should still be reusable. Do not merely restate the filename or
  visible title; omit product / domain object detail unless it changes
  applicability.
- Category names should describe the business action, skill, artifact type, or applicability boundary.
- Category ids must be stable short slug labels, not natural-language summaries.
- Do not use recall timing or evaluation mechanics as categories unless the subject is literally about the recall/evaluation mechanism itself.
- For query-side subjects, do not encode decision-node mechanics such as
  `first_user`, `pre_write`, `before_write`, `classification`, `query`, or
  `retrieval` into category ids. Categorize the intended workflow or action
  itself.
- For query-side subjects, ignore pipeline framing phrases such as "before
  executing write-like tool calls" when naming categories. For example, a
  subject with tool `cancel_pending_order` should be categorized as a pending
  order cancellation workflow, not as a pre-write classification workflow.
- It is fine to mention decision-node mechanics in `evidence` or
  `applicability` when they explain where the subject came from, but
  `category1`, `category2`, `category3`, and `category_id` must stay about the
  reusable business / skill / artifact semantics.
- `category2` must be more specific than `category1`; prefer state, precondition, or applicability boundary.
- If evidence is weak, keep the category broad and lower `confidence`.
- When no catalog is provided, still choose `category1` as if future similar
  subjects will reuse it; do not create a narrow first-level category just to
  fit the current item.

## Subject Metadata

```json
{{SUBJECT_METADATA_JSON}}
```

## Visible Subject Text

```text
{{SUBJECT_TEXT}}
```

## Optional Category Hints

```json
{{CATEGORY_HINTS_JSON}}
```

If `CATEGORY_HINTS_JSON` contains `known_category_catalog`, treat it as an existing reusable taxonomy:

- First try to reuse an existing `category1/category2` pair from the catalog.
- Reuse a category when the visible subject clearly matches its description, examples, positive triggers, and negative boundaries.
- Reuse an existing `category1` only when the primary workflow outcome matches;
  shared nouns such as user, account, order, reservation, document, task, or
  tool are not sufficient evidence.
- If no existing category fits, create a new stable category and explain why in `category.new_category_reason`.
- If a close category exists but is too broad or missing a boundary, reuse `category1`, propose a more precise `category2`, and mention the relationship in `category.catalog_relation`.
- If you set `catalog_match.matched_category_id`, it must be the exact canonical `category1:category2` id from the catalog. If no exact catalog pair is reused, set it to `null`.
- Do not create or reuse catalog ids whose only difference is the runtime
  decision node, retrieval stage, annotation task, or evaluation pipeline. Those
  details belong in metadata/evidence, not in taxonomy.

## Output JSON Shape

```json
{
  "schema_version": "memory_category_annotation.v0",
  "annotation_id": "...",
  "producer": "llm_prompt",
  "subject": {
    "subject_type": "query|memory|trajectory_segment|tool_action|artifact|skill|worker_task",
    "subject_id": "...",
    "subject_ref": "...",
    "benchmark_family": "...",
    "domain": "..."
  },
  "category": {
    "category1": "...",
    "category2": "...",
    "category3": null,
    "category_source": "llm_prompt",
    "catalog_match": {
      "matched": true,
      "matched_category_id": "optional existing category id",
      "decision": "reuse|refine|new",
      "reason": "short reason"
    },
    "confidence": 0.0,
    "reason": "short reason using visible evidence only"
  },
  "applicability": {
    "applicability_summary": "...",
    "positive_triggers": ["..."],
    "negative_triggers": ["..."],
    "preconditions": ["..."],
    "anti_patterns": ["..."]
  },
  "evidence": {
    "source_fields": ["visible_subject_text"],
    "evidence_spans": [
      {"text": "short visible evidence span", "role": "category_or_boundary_evidence"}
    ]
  },
  "ranking_features": {
    "category1": "...",
    "category2": "...",
    "category_source": "llm_prompt",
    "confidence": 0.0
  },
  "safety": {
    "uses_hidden_gold": false,
    "pii_or_instance_policy": "avoid_instance_ids",
    "runtime_safe_inputs_only": true
  }
}
```

`safety.pii_or_instance_policy` must be exactly one of
`avoid_instance_ids`, `redacted`, or `not_applicable`.
"""


@dataclass(frozen=True)
class Subject:
    subject_type: str
    subject_id: str
    subject_ref: str
    benchmark_family: str
    domain: str
    subject_text: str

    @property
    def stable_hash(self) -> str:
        payload = json.dumps(
            {
                "benchmark_family": self.benchmark_family,
                "domain": self.domain,
                "subject_id": self.subject_id,
                "subject_ref": self.subject_ref,
                "subject_text": self.subject_text,
                "subject_type": self.subject_type,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def metadata(self) -> dict[str, Any]:
        return {
            "benchmark_family": self.benchmark_family,
            "domain": self.domain,
            "subject_id": self.subject_id,
            "subject_ref": self.subject_ref,
            "subject_type": self.subject_type,
        }


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)[:180] or "category_requests"


def _slug_identity(value: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower())
    return "_".join(part for part in safe.split("_") if part)


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return payload


def _compact_text(text: str, *, limit: int) -> str:
    compact = "\n".join(line.rstrip() for line in text.strip().splitlines() if line.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 40].rstrip() + "\n...[truncated for category extraction]"


def _compact_catalog(path: Path | None, *, max_categories: int) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = _load_json(path)
    categories = payload.get("categories")
    if not isinstance(categories, list):
        raise SystemExit(f"category catalog missing categories: {path}")
    compact: list[dict[str, Any]] = []
    for category in categories[:max_categories]:
        if not isinstance(category, dict):
            continue
        compact.append(
            {
                "category_id": category.get("category_id"),
                "category1": category.get("category1"),
                "category2": category.get("category2"),
                "category3": category.get("category3"),
                "positive_triggers": (category.get("positive_triggers") or [])[:1],
                "negative_triggers": (category.get("negative_triggers") or [])[:1],
            }
        )
    return {
        "catalog_ref": _relative(path.resolve()),
        "category_count": len(compact),
        "categories": compact,
        "reuse_policy": "prefer_existing_category1_category2_pairs_before_creating_new_categories",
        "schema_version": payload.get("schema_version"),
    }


def _render_prompt(*, subject: Subject, category_hints: dict[str, Any]) -> str:
    return (
        PROMPT_TEMPLATE.replace("{{SUBJECT_METADATA_JSON}}", json.dumps(subject.metadata(), ensure_ascii=False, indent=2, sort_keys=True))
        .replace("{{SUBJECT_TEXT}}", subject.subject_text.strip())
        .replace("{{CATEGORY_HINTS_JSON}}", json.dumps(category_hints, ensure_ascii=False, indent=2, sort_keys=True))
    )


def _build_request(*, subject: Subject, category_hints: dict[str, Any]) -> dict[str, Any]:
    request_id = f"{subject.subject_type}:{subject.subject_id}:{subject.stable_hash}"
    return {
        "category_hints": category_hints,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "dry_run_render_only",
        "expected_output_schema_version": DEFAULT_SCHEMA_REF,
        "prompt": _render_prompt(subject=subject, category_hints=category_hints),
        "prompt_ref": "benchmark/tau2/scripts/build_category_requests.py::PROMPT_TEMPLATE",
        "request_id": request_id,
        "safety": {
            "pii_or_instance_policy": "avoid_instance_ids",
            "runtime_safe_inputs_only": True,
            "uses_hidden_gold": False,
        },
        "schema_ref": DEFAULT_SCHEMA_REF,
        "schema_version": "memory_category_extraction_request.v0",
        "subject": subject.metadata(),
    }


def _memory_root(*, workspace: Path, manifest: dict[str, Any], memory_type: str) -> Path:
    openviking = manifest.get("openviking")
    if not isinstance(openviking, dict):
        raise SystemExit("corpus manifest missing openviking block")
    account = str(openviking.get("account") or "").strip()
    agent_id = str(openviking.get("agent_id") or "").strip()
    if not account or not agent_id:
        raise SystemExit("corpus manifest missing openviking.account or openviking.agent_id")
    return workspace / "viking" / account / "agent" / agent_id / "memories" / memory_type


def _iter_memory_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise SystemExit(f"memory root not found: {root}")
    files = []
    for path in sorted(root.glob("*.md")):
        if path.name.startswith(".") or path.name.endswith(".abstract.md") or path.name.endswith(".overview.md"):
            continue
        files.append(path)
    return files


def _subject_id(logical_uri: str) -> str:
    safe = _safe_key(logical_uri)
    return f"openviking_memory_{safe}"


def _query_subject_id(query_signature: str) -> str:
    return f"tau2_query_signature_{_slug_identity(query_signature)}"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise SystemExit(f"expected JSON object at {path}:{line_number}")
        rows.append(row)
    if not rows:
        raise SystemExit(f"no query subjects found: {path}")
    return rows


def _query_subject_from_row(row: dict[str, Any], *, default_domain: str | None, text_limit: int) -> Subject:
    domain = str(row.get("domain") or default_domain or "").strip()
    decision_node = str(row.get("decision_node") or "").strip()
    query_signature = str(row.get("query_signature") or row.get("signature") or "").strip()
    query_text = str(row.get("query_text") or row.get("query") or row.get("visible_query") or "").strip()
    if not domain:
        raise SystemExit(f"query subject missing domain: {row}")
    if not decision_node:
        raise SystemExit(f"query subject missing decision_node: {row}")
    if not query_signature:
        raise SystemExit(f"query subject missing query_signature: {row}")
    if not query_text:
        raise SystemExit(f"query subject missing query text: {row}")
    tools = row.get("tools") or []
    subject_text = "\n".join(
        line
        for line in [
            f"Decision node: {decision_node}",
            f"Query signature: {query_signature}",
            f"Tools: {', '.join(str(item) for item in tools) if isinstance(tools, list) else tools}",
            "",
            "Visible query:",
            query_text,
        ]
        if line != ""
    )
    return Subject(
        benchmark_family=str(row.get("benchmark_family") or "tau2"),
        domain=domain,
        subject_id=str(row.get("subject_id") or _query_subject_id(query_signature)),
        subject_ref=str(row.get("subject_ref") or query_signature),
        subject_text=_compact_text(subject_text, limit=text_limit),
        subject_type="query",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--memory-type", default="trajectories")
    parser.add_argument("--query-subjects-jsonl", type=Path, default=None)
    parser.add_argument("--category-catalog", type=Path, default=None)
    parser.add_argument("--max-catalog-categories", type=int, default=80)
    parser.add_argument("--subject-text-limit", type=int, default=3500)
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N sorted memory files.")
    parser.add_argument("--limit", type=int, default=0, help="0 means all remaining memory files.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--hint", action="append", default=[])
    args = parser.parse_args()
    if args.offset < 0:
        raise SystemExit("--offset must be >= 0")
    if args.limit < 0:
        raise SystemExit("--limit must be >= 0")

    catalog = _compact_catalog(args.category_catalog.resolve() if args.category_catalog else None, max_categories=args.max_catalog_categories)
    extra_hints: dict[str, str] = {}
    for pair in args.hint:
        if "=" not in pair:
            raise SystemExit(f"invalid --hint {pair!r}; expected key=value")
        key, value = pair.split("=", 1)
        extra_hints[key.strip()] = value.strip()

    requests = []
    subjects = []
    manifest: dict[str, Any] | None = None
    root: Path | None = None
    all_files: list[Path] = []
    files: list[Path] = []
    if args.query_subjects_jsonl:
        query_rows = _load_jsonl(args.query_subjects_jsonl.resolve())
        for row in query_rows:
            subject = _query_subject_from_row(row, default_domain=args.domain, text_limit=args.subject_text_limit)
            hints: dict[str, Any] = {
                "benchmark": subject.benchmark_family,
                "decision_node": row.get("decision_node"),
                "query_signature": row.get("query_signature") or row.get("signature"),
                "query_source": row.get("source") or row.get("query_source") or "query_subjects_jsonl",
                "tools": row.get("tools") or [],
                **extra_hints,
            }
            if catalog:
                hints["known_category_catalog"] = catalog
            requests.append(_build_request(subject=subject, category_hints=hints))
            subjects.append(
                {
                    "query_signature": row.get("query_signature") or row.get("signature"),
                    "source": row.get("source") or row.get("query_source") or "query_subjects_jsonl",
                    "subject": subject.metadata(),
                    "text_sha256": hashlib.sha256(subject.subject_text.encode("utf-8")).hexdigest()[:16],
                }
            )
    else:
        if args.manifest is None or args.workspace is None or not args.domain:
            raise SystemExit("--manifest, --workspace, and --domain are required for memory request generation")
        manifest = _load_json(args.manifest.resolve())
        openviking = manifest.get("openviking") if isinstance(manifest.get("openviking"), dict) else {}
        agent_id = str(openviking.get("agent_id") or "").strip()
        if not agent_id:
            raise SystemExit("manifest openviking.agent_id is required")
        root = _memory_root(workspace=args.workspace.resolve(), manifest=manifest, memory_type=args.memory_type)
        all_files = _iter_memory_files(root)
        files = all_files[args.offset :]
        if args.limit:
            files = files[: args.limit]
        for path in files:
            logical_uri = f"viking://agent/{agent_id}/memories/{args.memory_type}/{path.name}"
            subject = Subject(
                benchmark_family="tau2",
                domain=args.domain,
                subject_id=_subject_id(logical_uri),
                subject_ref=logical_uri,
                subject_text=_compact_text(path.read_text(encoding="utf-8"), limit=args.subject_text_limit),
                subject_type="memory",
            )
            hints: dict[str, Any] = {
                "benchmark": "tau2",
                "file_name": path.name,
                "logical_uri": logical_uri,
                "ov_uri_bucket": args.memory_type.rstrip("s"),
                **extra_hints,
            }
            if catalog:
                hints["known_category_catalog"] = catalog
            requests.append(_build_request(subject=subject, category_hints=hints))
            subjects.append(
                {
                    "bucket": args.memory_type,
                    "memory_path": _relative(path),
                    "subject": subject.metadata(),
                    "text_sha256": hashlib.sha256(subject.subject_text.encode("utf-8")).hexdigest()[:16],
                }
            )

    if not requests:
        raise SystemExit("no category requests produced")

    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    run_root = output_root / _safe_key(args.run_id)
    run_root.mkdir(parents=True, exist_ok=True)
    requests_path = run_root / "category_extraction_requests.jsonl"
    subjects_path = run_root / "subjects.jsonl"
    summary_path = run_root / "run_summary.json"
    requests_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in requests), encoding="utf-8")
    subjects_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in subjects), encoding="utf-8")
    summary = {
        "category_catalog_path": _relative(args.category_catalog.resolve()) if args.category_catalog else None,
        "claim_boundary": "prompt_request_plan_from_openviking_manifest_visible_memory_files_no_hidden_gold",
        "concrete_memory_file_count": len(files),
        "domain": args.domain,
        "known_category_count": catalog.get("category_count") if catalog else 0,
        "limit": args.limit,
        "manifest_committed_session_count": manifest.get("committed_session_count") if manifest else None,
        "manifest_path": _relative(args.manifest.resolve()) if args.manifest else None,
        "memory_root": _relative(root) if root else None,
        "memory_type": args.memory_type,
        "offset": args.offset,
        "query_subjects_jsonl": _relative(args.query_subjects_jsonl.resolve()) if args.query_subjects_jsonl else None,
        "request_count": len(requests),
        "requests_path": _relative(requests_path),
        "run_id": args.run_id,
        "schema_version": "openviking_tau2_category_request_plan.v0",
        "status": "passed",
        "subjects_path": _relative(subjects_path),
        "subject_type": "query" if args.query_subjects_jsonl else "memory",
        "total_concrete_memory_file_count": len(all_files),
        "workspace": _relative(args.workspace.resolve()) if args.workspace else None,
    }
    if manifest and manifest.get("committed_session_count") != len(files):
        summary["inventory_warning"] = "committed_session_count differs from concrete memory file count"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
