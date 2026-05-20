"""Build a compact category catalog from category annotation JSONL files."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CATEGORY_PART_PATTERN = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")
CATEGORY_PART_MAX_LENGTH = 64


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _safe_category_part(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SystemExit(f"annotation category missing {field}")
    if not CATEGORY_PART_PATTERN.fullmatch(text):
        raise SystemExit(
            f"annotation {field} must be a reusable slug id using lowercase letters, numbers, "
            f"'_' or '-': {text!r}"
        )
    if len(text) > CATEGORY_PART_MAX_LENGTH:
        raise SystemExit(
            f"annotation {field} must be a compact reusable slug id with at most "
            f"{CATEGORY_PART_MAX_LENGTH} characters; put detailed boundaries in applicability: {text!r}"
        )
    return text


def _load_annotations(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"annotation file not found: {path}")
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise SystemExit(f"expected object at {path}:{line_number}")
            rows.append(payload)
    if not rows:
        raise SystemExit("no annotations found")
    return rows


def _dedupe(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _iter_texts(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _build_catalog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        category = row.get("category")
        if not isinstance(category, dict):
            raise SystemExit("annotation missing category object")
        category1 = _safe_category_part(category.get("category1"), field="category1")
        category2 = _safe_category_part(category.get("category2"), field="category2")
        grouped[f"{category1}:{category2}"].append(row)

    categories: list[dict[str, Any]] = []
    for category_id in sorted(grouped):
        members = grouped[category_id]
        category = members[0]["category"]
        category1, category2 = category_id.split(":", 1)
        category3_values = [
            str(item.get("category", {}).get("category3") or "").strip()
            for item in members
            if isinstance(item.get("category"), dict)
        ]
        positive_triggers: list[str] = []
        negative_triggers: list[str] = []
        applicability_summaries: list[str] = []
        source_annotation_ids: list[str] = []
        domains: list[str] = []
        subject_types: list[str] = []
        for item in members:
            source_annotation_ids.append(str(item.get("annotation_id") or item.get("request_id") or ""))
            subject = item.get("subject") if isinstance(item.get("subject"), dict) else {}
            domains.append(str(subject.get("domain") or ""))
            subject_types.append(str(subject.get("subject_type") or ""))
            applicability = item.get("applicability") if isinstance(item.get("applicability"), dict) else {}
            positive_triggers.extend(_iter_texts(applicability.get("positive_triggers")))
            negative_triggers.extend(_iter_texts(applicability.get("negative_triggers")))
            summary = str(applicability.get("applicability_summary") or "").strip()
            if summary:
                applicability_summaries.append(summary)
        category3 = _dedupe(category3_values, limit=1)
        categories.append(
            {
                "applicability_summaries": _dedupe(applicability_summaries, limit=2),
                "category1": category1,
                "category2": category2,
                "category3": category3[0] if category3 else None,
                "category_id": category_id,
                "domains": _dedupe(domains, limit=8),
                "negative_triggers": _dedupe(negative_triggers, limit=3),
                "positive_triggers": _dedupe(positive_triggers, limit=3),
                "source_annotation_count": len(members),
                "source_annotation_ids": _dedupe(source_annotation_ids, limit=12),
                "subject_types": _dedupe(subject_types, limit=8),
            }
        )

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "category_count": len(categories),
        "categories": categories,
        "schema_version": "memory_category_catalog.v0",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    annotation_paths = [path.resolve() for path in args.annotations]
    rows = _load_annotations(annotation_paths)
    catalog = _build_catalog(rows)
    catalog["run_id"] = args.run_id
    catalog["source_annotation_files"] = [_relative(path) for path in annotation_paths]
    catalog["source_annotation_count"] = len(rows)

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"category_count": catalog["category_count"], "output": _relative(output), "status": "passed"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
