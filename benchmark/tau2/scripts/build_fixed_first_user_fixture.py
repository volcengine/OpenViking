#!/usr/bin/env python3
"""Build a TAU-2 fixed-first-user fixture from a TAU-2 results.json file."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _add_tau2_to_path(repo: Path) -> None:
    repo = repo.expanduser().resolve()
    for candidate in (repo, repo / "src"):
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def _load_json(path: Path) -> Any:
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


def _scenario_sha256(instructions: str) -> str:
    return hashlib.sha256(instructions.encode("utf-8")).hexdigest()


def _first_user(simulation: dict[str, Any]) -> str:
    for message in simulation.get("messages") or []:
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _get_tasks(repo: Path, domain: str, task_split_name: str) -> list[Any]:
    _add_tau2_to_path(repo)
    try:
        from tau2.runner.helpers import get_tasks

        return list(get_tasks(domain, task_split_name=task_split_name))
    except ModuleNotFoundError:
        from tau2.run import get_tasks as legacy_get_tasks

        return list(legacy_get_tasks(task_set_name=domain, task_split_name=task_split_name))


def build_fixture(
    *,
    repo: Path,
    results_json: Path,
    domain: str,
    task_split_name: str,
) -> dict[str, Any]:
    results = _load_json(results_json)
    first_user_by_task_id = {
        str(simulation.get("task_id")): _first_user(simulation)
        for simulation in results.get("simulations") or []
    }
    tasks = _get_tasks(repo, domain, task_split_name)

    records: list[dict[str, str]] = []
    by_scenario_sha256: dict[str, str] = {}
    missing_task_ids: list[str] = []
    for task in tasks:
        task_id = str(task.id)
        first_user = first_user_by_task_id.get(task_id, "")
        if not first_user:
            missing_task_ids.append(task_id)
            continue
        key = _scenario_sha256(str(task.user_scenario))
        by_scenario_sha256[key] = first_user
        records.append(
            {
                "task_id": task_id,
                "scenario_sha256": key,
                "first_user": first_user,
                "first_user_preview": first_user[:220],
            }
        )

    return {
        "fixture_type": "tau2_fixed_first_user.v0",
        "domain": domain,
        "task_split_name": task_split_name,
        "source_results_json": str(results_json.expanduser().resolve()),
        "expected_task_count": len(tasks),
        "record_count": len(records),
        "missing_task_ids": missing_task_ids,
        "by_scenario_sha256": by_scenario_sha256,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("benchmark/tau2/.external/tau2-bench"))
    parser.add_argument("--results-json", type=Path, required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--task-split-name", default="test")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--require-full-split",
        action="store_true",
        help="Fail if the results file does not cover every task in the requested split.",
    )
    args = parser.parse_args()

    fixture = build_fixture(
        repo=args.repo,
        results_json=args.results_json,
        domain=args.domain,
        task_split_name=args.task_split_name,
    )
    if args.require_full_split and fixture["record_count"] != fixture["expected_task_count"]:
        raise SystemExit(
            "fixed-first-user fixture coverage incomplete: "
            f"{fixture['record_count']}/{fixture['expected_task_count']} records, "
            f"missing task ids: {fixture['missing_task_ids'][:20]}"
        )

    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "expected_task_count": fixture["expected_task_count"],
                "record_count": fixture["record_count"],
                "missing_task_count": len(fixture["missing_task_ids"]),
                "path": str(output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
