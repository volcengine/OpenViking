#!/usr/bin/env python3
"""Validate an ARA-style paper artifact before OpenViking ingestion."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_FILES = [
    "PAPER.md",
    "logic/problem.md",
    "logic/claims.md",
    "logic/concepts.md",
    "logic/experiments.md",
    "logic/related_work.md",
    "logic/solution/constraints.md",
    "src/environment.md",
    "trace/exploration_tree.yaml",
    "evidence/README.md",
]

PAPER_FRONTMATTER_FIELDS = ["title", "authors", "year"]
CLAIM_FIELDS = [
    "Statement",
    "Status",
    "Falsification criteria",
    "Proof",
    "Evidence basis",
]
EXPERIMENT_FIELDS = ["Verifies", "Setup", "Procedure", "Metrics", "Expected outcome"]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def has_field(block: str, field: str) -> bool:
    pattern = rf"(?im)^\s*[-*]\s+(?:\*\*)?{re.escape(field)}(?:\*\*)?\s*:"
    return bool(re.search(pattern, block))


def split_heading_blocks(text: str, prefix: str) -> dict[str, str]:
    heading = re.compile(rf"(?m)^##\s+({prefix}\d{{2,}})\b.*$")
    matches = list(heading.finditer(text))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[match.group(1)] = text[start:end]
    return blocks


def extract_refs(text: str, prefix: str) -> set[str]:
    return set(re.findall(rf"\b{prefix}\d{{2,}}\b", text))


def validate(root: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not root.exists():
        return {
            "ok": False,
            "errors": [f"artifact directory does not exist: {root}"],
            "warnings": [],
            "summary": {},
        }
    if not root.is_dir():
        return {
            "ok": False,
            "errors": [f"artifact path is not a directory: {root}"],
            "warnings": [],
            "summary": {},
        }

    for rel in REQUIRED_FILES:
        path = root / rel
        if not path.exists():
            errors.append(f"missing required file: {rel}")
        elif path.stat().st_size == 0:
            errors.append(f"required file is empty: {rel}")

    paper_path = root / "PAPER.md"
    if paper_path.exists():
        paper = read_text(paper_path)
        frontmatter = re.match(r"(?s)^---\n(.*?)\n---\n", paper)
        if not frontmatter:
            errors.append("PAPER.md missing YAML frontmatter")
        else:
            fm = frontmatter.group(1)
            for field in PAPER_FRONTMATTER_FIELDS:
                if not re.search(rf"(?m)^{re.escape(field)}\s*:", fm):
                    errors.append(f"PAPER.md frontmatter missing field: {field}")
        if "Layer Index" not in paper:
            errors.append("PAPER.md missing Layer Index")

    claim_blocks: dict[str, str] = {}
    claims_path = root / "logic/claims.md"
    if claims_path.exists():
        claim_blocks = split_heading_blocks(read_text(claims_path), "C")
        if not claim_blocks:
            errors.append("logic/claims.md has no C## claim blocks")
        for claim_id, block in claim_blocks.items():
            for field in CLAIM_FIELDS:
                if not has_field(block, field):
                    errors.append(f"{claim_id} missing field: {field}")

    experiment_blocks: dict[str, str] = {}
    experiments_path = root / "logic/experiments.md"
    if experiments_path.exists():
        experiment_blocks = split_heading_blocks(read_text(experiments_path), "E")
        if not experiment_blocks:
            errors.append("logic/experiments.md has no E## experiment blocks")
        for exp_id, block in experiment_blocks.items():
            for field in EXPERIMENT_FIELDS:
                if not has_field(block, field):
                    errors.append(f"{exp_id} missing field: {field}")

    claim_ids = set(claim_blocks)
    experiment_ids = set(experiment_blocks)

    for claim_id, block in claim_blocks.items():
        proof_match = re.search(
            r"(?im)^\s*[-*]\s+(?:\*\*)?Proof(?:\*\*)?\s*:\s*(.+)$", block
        )
        if proof_match:
            refs = extract_refs(proof_match.group(1), "E")
            if not refs:
                errors.append(f"{claim_id} Proof does not reference any E## experiment")
            for ref in refs:
                if ref not in experiment_ids:
                    errors.append(f"{claim_id} Proof references missing experiment: {ref}")

    for exp_id, block in experiment_blocks.items():
        verifies_match = re.search(
            r"(?im)^\s*[-*]\s+(?:\*\*)?Verifies(?:\*\*)?\s*:\s*(.+)$", block
        )
        if verifies_match:
            refs = extract_refs(verifies_match.group(1), "C")
            if not refs:
                errors.append(f"{exp_id} Verifies does not reference any C## claim")
            for ref in refs:
                if ref not in claim_ids:
                    errors.append(f"{exp_id} Verifies references missing claim: {ref}")

    evidence_counts = {"figure_md": 0, "figure_png": 0, "table_md": 0, "table_png": 0}
    for kind, dirname in [("figure", "evidence/figures"), ("table", "evidence/tables")]:
        directory = root / dirname
        if not directory.exists():
            warnings.append(f"optional evidence directory missing: {dirname}")
            continue
        md_files = sorted(directory.glob("*.md"))
        png_files = sorted(directory.glob("*.png"))
        evidence_counts[f"{kind}_md"] = len(md_files)
        evidence_counts[f"{kind}_png"] = len(png_files)
        for md in md_files:
            content = read_text(md)
            rel = md.relative_to(root)
            if not re.search(
                r"(?im)^\s*[-*]?\s*(?:\*\*)?Source(?:\*\*)?\s*:", content
            ):
                errors.append(f"{rel} missing Source field")
            if kind == "figure":
                for field in ["Figure type", "Extraction method", "Reading confidence"]:
                    if not re.search(rf"(?im){re.escape(field)}\s*:", content):
                        warnings.append(f"{rel} missing recommended field: {field}")
            sibling_png = md.with_suffix(".png")
            if not sibling_png.exists():
                errors.append(f"{rel} missing sibling PNG: {sibling_png.name}")

    trace_path = root / "trace/exploration_tree.yaml"
    if trace_path.exists():
        trace = read_text(trace_path)
        if "support_level" not in trace:
            errors.append("trace/exploration_tree.yaml missing support_level entries")
        if not re.search(r"(?m)^\s*-\s*id\s*:", trace) and not re.search(
            r"(?m)^\s*id\s*:", trace
        ):
            warnings.append("trace/exploration_tree.yaml has no obvious node id entries")

    summary = {
        "required_files": len(REQUIRED_FILES),
        "claims": len(claim_blocks),
        "experiments": len(experiment_blocks),
        **evidence_counts,
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    result = validate(args.artifact_dir)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status = "PASS" if result["ok"] else "FAIL"
        print(f"ARA validation: {status}")
        print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
        for warning in result["warnings"]:
            print(f"WARN: {warning}")
        for error in result["errors"]:
            print(f"ERROR: {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
