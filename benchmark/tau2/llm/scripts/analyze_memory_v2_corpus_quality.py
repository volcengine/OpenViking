#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _memory_root_uri(search_uri: str) -> str:
    prefix, separator, path = search_uri.partition("://")
    if not separator:
        raise ValueError(f"not a Viking URI: {search_uri}")
    segments = path.split("/")
    try:
        memory_index = segments.index("memories")
    except ValueError as exc:
        raise ValueError(f"search_uri does not contain a memories path segment: {search_uri}") from exc
    return f"{prefix}{separator}{'/'.join(segments[: memory_index + 1])}"


def _run_plan_manifests(run_plan: Path) -> list[Path]:
    data = _read_json(run_plan)
    manifests: list[Path] = []
    for cell in data.get("cells") or []:
        artifacts = cell.get("artifacts") or {}
        raw = artifacts.get("corpus_manifest")
        if raw:
            manifests.append(Path(raw))
            continue
        corpus_dir = cell.get("corpus_dir")
        if corpus_dir:
            manifests.append(Path(corpus_dir) / "corpus_manifest.json")
    return manifests


def _existing_manifests(paths: list[Path], *, allow_missing: bool) -> list[Path]:
    existing = [path for path in dict.fromkeys(paths) if path.is_file()]
    missing = [path for path in dict.fromkeys(paths) if not path.is_file()]
    if missing and not allow_missing:
        rendered = "\n".join(f"- {path}" for path in missing[:20])
        extra = "" if len(missing) <= 20 else f"\n... and {len(missing) - 20} more"
        raise FileNotFoundError(
            "missing corpus_manifest.json files; pass --allow-missing-manifests "
            f"only for incomplete diagnostic reads:\n{rendered}{extra}"
        )
    return existing


def _summarize_health(manifest_path: Path, health: dict[str, Any]) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    counts = health.get("memory_type_counts") or {}
    quality = health.get("experience_quality") or {}
    source_links = quality.get("source_links_per_experience") or {}
    duplicate_examples = quality.get("duplicate_exact_source_set_examples") or []
    samples = health.get("samples") or health.get("violation_samples") or []
    issue_keys = (
        "parse_error_count",
        "malformed_link_count",
        "owner_mismatch_count",
        "duplicate_link_count",
        "broken_endpoint_count",
        "missing_backlink_count",
        "missing_forward_link_count",
    )
    issue_total = sum(int(health.get(key) or 0) for key in issue_keys)
    issue_breakdown = {key: int(health.get(key) or 0) for key in issue_keys}
    return {
        "manifest": str(manifest_path),
        "domain": manifest.get("domain"),
        "committed_sessions": manifest.get("committed_session_count"),
        "skipped_failed_sessions": manifest.get("skipped_failed_session_count"),
        "commit_concurrency": manifest.get("corpus_session_commit_worker_count"),
        "root_uri": health.get("root_uri"),
        "healthy": health.get("healthy", issue_total == 0),
        "issue_total": issue_total,
        "issue_breakdown": issue_breakdown,
        "memory_files": health.get("memory_file_count"),
        "experiences": counts.get("experiences", 0),
        "trajectories": counts.get("trajectories", 0),
        "tools": counts.get("tools", 0),
        "skills": counts.get("skills", 0),
        "exp_per_session": _ratio(
            counts.get("experiences", 0), manifest.get("committed_session_count")
        ),
        "traj_per_session": _ratio(
            counts.get("trajectories", 0), manifest.get("committed_session_count")
        ),
        "source_links_avg": source_links.get("avg"),
        "source_links_p50": source_links.get("p50"),
        "source_links_p90": source_links.get("p90"),
        "source_linkless": source_links.get("linkless"),
        "single_source_rate": source_links.get("single_source_rate"),
        "pair_scan_skipped": quality.get("pair_scan_skipped"),
        "duplicate_exact_source_set_count": quality.get("duplicate_exact_source_set_count"),
        "name_similar_pair_count": quality.get("name_similar_pair_count"),
        "content_similar_pair_count": quality.get("content_similar_pair_count"),
        "source_overlap_pair_count": quality.get("source_overlap_pair_count"),
        "duplicate_exact_source_set_examples": duplicate_examples[:3],
        "samples": samples[:3],
    }


def _ratio(numerator: Any, denominator: Any) -> float | None:
    try:
        den = float(denominator)
        if den == 0:
            return None
        return round(float(numerator or 0) / den, 4)
    except (TypeError, ValueError):
        return None


def _collect(manifest_paths: list[Path], *, node_limit: int, sample_limit: int) -> list[dict[str, Any]]:
    from openviking_cli.client.sync_http import SyncHTTPClient

    rows: list[dict[str, Any]] = []
    for manifest_path in manifest_paths:
        manifest = _read_json(manifest_path)
        openviking = manifest.get("openviking") or {}
        search_uri = str(openviking.get("search_uri") or "")
        if not search_uri:
            raise ValueError(f"manifest missing openviking.search_uri: {manifest_path}")
        client = SyncHTTPClient(
            url=openviking.get("url"),
            account=openviking.get("account"),
            user=openviking.get("user"),
            agent_id=openviking.get("agent_id"),
            timeout=120.0,
        )
        client.initialize()
        try:
            health = client.memory_graph_health(
                _memory_root_uri(search_uri),
                node_limit=node_limit,
                sample_limit=sample_limit,
            )
        finally:
            client.close()
        rows.append(_summarize_health(manifest_path, health))
    return rows


def _print_markdown(rows: list[dict[str, Any]]) -> None:
    headers = [
        "domain",
        "commit_concurrency",
        "committed_sessions",
        "experiences",
        "trajectories",
        "exp_per_session",
        "source_links_avg",
        "source_linkless",
        "single_source_rate",
        "issue_total",
        "duplicate_exact_source_set_count",
        "name_similar_pair_count",
        "content_similar_pair_count",
        "source_overlap_pair_count",
        "pair_scan_skipped",
        "healthy",
    ]
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        print("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")

    for row in rows:
        breakdown = row.get("issue_breakdown") or {}
        nonzero = {key: value for key, value in breakdown.items() if value}
        if nonzero:
            print(
                f"\n### Graph issue breakdown: {row.get('domain')} "
                f"c{row.get('commit_concurrency')}"
            )
            for key, value in nonzero.items():
                print(f"- {key}: {value}")

    for row in rows:
        examples = row.get("duplicate_exact_source_set_examples") or []
        if not examples:
            continue
        print(f"\n### Duplicate source-set examples: {row.get('domain')} c{row.get('commit_concurrency')}")
        for example in examples:
            uris = example.get("uris") or []
            print(f"- source_count={example.get('source_count')}: " + ", ".join(_basename(uri) for uri in uris))


def _basename(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read completed TAU-2 Memory V2 corpus manifests and summarize graph health."
    )
    parser.add_argument("--manifest", action="append", type=Path, default=[])
    parser.add_argument("--run-plan", action="append", type=Path, default=[])
    parser.add_argument("--node-limit", type=int, default=200000)
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument(
        "--allow-missing-manifests",
        action="store_true",
        help="Allow incomplete diagnostic reads when --run-plan expands unfinished cells.",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    manifests = list(args.manifest)
    for run_plan in args.run_plan:
        manifests.extend(_run_plan_manifests(run_plan))
    manifests = _existing_manifests(manifests, allow_missing=args.allow_missing_manifests)
    if not manifests:
        parser.error("no completed corpus_manifest.json files found")

    rows = _collect(manifests, node_limit=args.node_limit, sample_limit=args.sample_limit)
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_markdown(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
