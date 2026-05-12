#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from tau2_common import domains, load_config, output_dir, run_id, split_file, strategy_ids, tau2_repo, write_json


def _tau2_command(config: dict[str, Any], *, domain: str, strategy: dict[str, Any], repeat_index: int, run_label: str) -> list[str]:
    benchmark = config["benchmark"]
    model = config["model"]
    command = [
        "tau2",
        "run",
        "--domain",
        domain,
        "--task-split-name",
        str(benchmark.get("eval_split_name", "test")),
        "--num-trials",
        "1",
        "--max-steps",
        str(benchmark.get("max_steps", 200)),
        "--max-concurrency",
        str(benchmark.get("task_max_concurrency", 10)),
        "--agent-llm",
        str(model["agent_llm"]),
        "--user-llm",
        str(model["user_llm"]),
        "--save-to",
        run_label,
    ]

    reasoning_effort = benchmark.get("reasoning_effort")
    if reasoning_effort:
        command.extend(["--agent-llm-args", f'{{"temperature":0.0,"reasoning_effort":"{reasoning_effort}"}}'])
        command.extend(["--user-llm-args", f'{{"temperature":0.0,"reasoning_effort":"{reasoning_effort}"}}'])

    if strategy.get("memory_backend") == "none":
        command.extend(["--memory-backend", "none"])
    else:
        command.extend(["--memory-backend", "openviking"])
        command.extend(["--memory-retrieval-mode", str(strategy.get("retrieval_mode", "first_user"))])
        command.extend(["--memory-replay-write-policy", str(config.get("openviking", {}).get("replay_write_policy", "read_only"))])

    if config.get("features", {}).get("prewrite_recall", {}).get("enabled"):
        command.append("--enable-prewrite-recall")

    return command


def _build_plan(config: dict[str, Any], configured_run_id: str) -> dict[str, Any]:
    repeat_count = int(config["benchmark"].get("repeat_count", 4))
    strategies = config.get("strategies") or []
    cells = []
    for domain in domains(config):
        split_path = split_file(config, domain)
        for strategy in strategies:
            for repeat_index in range(repeat_count):
                run_label = f"{configured_run_id}_{domain}_{strategy['id']}_r{repeat_index + 1}"
                cells.append(
                    {
                        "domain": domain,
                        "strategy_id": strategy["id"],
                        "strategy_label": strategy.get("label", strategy["id"]),
                        "repeat_index": repeat_index + 1,
                        "run_label": run_label,
                        "train_required": bool(strategy.get("train_required")),
                        "memory_backend": strategy.get("memory_backend"),
                        "split_file": str(split_path),
                        "command": _tau2_command(
                            config,
                            domain=domain,
                            strategy=strategy,
                            repeat_index=repeat_index,
                            run_label=run_label,
                        ),
                    }
                )
    return {
        "schema_version": "openviking.tau2.run_plan.v0",
        "run_id": configured_run_id,
        "status": "planned",
        "strategy_ids": strategy_ids(config),
        "domains": domains(config),
        "cell_count": len(cells),
        "cells": cells,
    }


def _execute_cells(plan: dict[str, Any], repo: Path, out: Path) -> list[dict[str, Any]]:
    rows = []
    for cell in plan["cells"]:
        print(f"[tau2] running {cell['run_label']}")
        completed = subprocess.run(
            cell["command"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        row = {
            "run_label": cell["run_label"],
            "domain": cell["domain"],
            "strategy_id": cell["strategy_id"],
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }
        rows.append(row)
        write_json(out / "cell_results" / f"{cell['run_label']}.json", row)
        if completed.returncode != 0:
            raise RuntimeError(f"cell failed: {cell['run_label']} returncode={completed.returncode}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or run TAU-2 benchmark cells.")
    parser.add_argument("--config", type=Path, default=Path(__file__).parents[1] / "config" / "baseline.yaml")
    parser.add_argument("--run-id", default=run_id())
    parser.add_argument("--plan-only", action="store_true", help="Only write run_plan.json.")
    parser.add_argument("--execute", action="store_true", help="Execute planned cells.")
    args = parser.parse_args()

    if args.plan_only and args.execute:
        raise SystemExit("--plan-only and --execute are mutually exclusive")

    config = load_config(args.config)
    out = output_dir(config, args.run_id)
    out.mkdir(parents=True, exist_ok=True)
    plan = _build_plan(config, args.run_id)
    write_json(out / "run_plan.json", plan)
    write_json(out / "resolved_config.json", config)
    print(f"[tau2] wrote {out / 'run_plan.json'}")

    if args.execute:
        try:
            rows = _execute_cells(plan, tau2_repo(config), out)
            plan["status"] = "succeeded"
            plan["executed_cell_count"] = len(rows)
            write_json(out / "run_plan.json", plan)
        except Exception as exc:
            plan["status"] = "failed"
            plan["error"] = str(exc)
            write_json(out / "run_plan.json", plan)
            print(f"[tau2][ERROR] {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
