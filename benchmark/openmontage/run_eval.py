#!/usr/bin/env python3
"""Run the OpenMontage retrieval benchmark against an OpenViking instance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scorer import score_report


ROOT = Path(__file__).resolve().parent
FIXTURE_PATH = ROOT / "data" / "fixture.json"
DEFAULT_OUTPUT = ROOT / "result" / "openmontage_eval.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def build_client(mode: str, workspace: str | None, url: str | None):
    import openviking as ov

    if mode == "http":
        client = ov.SyncHTTPClient(url=url or "http://localhost:1933")
    else:
        client = ov.OpenViking(path=workspace or "./data/openmontage-workspace")
    client.initialize()
    return client


def serialize_find_results(results) -> list[dict]:
    resources = getattr(results, "resources", []) or []
    serialized = []
    for item in resources:
        serialized.append(
            {
                "uri": getattr(item, "uri", ""),
                "score": getattr(item, "score", None),
                "abstract": getattr(item, "abstract", "") or "",
                "overview": getattr(item, "overview", "") or "",
            }
        )
    return serialized


def run_cases(client, fixture: dict, limit: int) -> dict:
    report = {
        "project_id": fixture["project_id"],
        "project_uri": fixture["project_uri"],
        "cases": [],
    }
    for case in fixture["evaluations"]:
        results = client.find(query=case["query"], target_uri=case["target_uri"], limit=limit)
        report["cases"].append(
            {
                **case,
                "hits": serialize_find_results(results),
            }
        )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["embedded", "http"], default="embedded")
    parser.add_argument("--workspace", help="Embedded-mode workspace path")
    parser.add_argument("--url", help="HTTP server base URL")
    parser.add_argument("--limit", type=int, default=3, help="Top-k retrieval hits per query")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = load_fixture()
    try:
        client = build_client(args.mode, args.workspace, args.url)
        report = run_cases(client, fixture, args.limit)
        scored = score_report(report)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps({"report": report, "score": scored}, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(scored, indent=2))
        return 0
    except Exception as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
