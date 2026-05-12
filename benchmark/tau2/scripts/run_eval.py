#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

from tau2_common import (
    domains,
    load_config,
    output_dir,
    run_id,
    simulator_policy_report,
    split_file,
    strategy_ids,
    tau2_cli,
    tau2_context,
    tau2_repo,
    user_simulator_policy,
    write_json,
)


def _tau2_command(
    config: dict[str, Any],
    *,
    domain: str,
    strategy: dict[str, Any],
    run_label: str,
    task_ids: list[str] | None,
    num_tasks: int | None,
) -> list[str] | None:
    benchmark = config["benchmark"]
    model = config["model"]

    if strategy.get("memory_backend") != "none":
        return None

    command = [
        tau2_cli(config),
        "run",
        "--domain",
        domain,
        "--agent",
        str(benchmark.get("agent", "llm_agent")),
        "--user",
        str(benchmark.get("user", "user_simulator")),
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

    if task_ids:
        command.append("--task-ids")
        command.extend(task_ids)
    elif num_tasks is not None:
        command.extend(["--num-tasks", str(num_tasks)])

    return command


def _build_plan(
    config: dict[str, Any],
    configured_run_id: str,
    *,
    selected_domains: set[str] | None,
    selected_strategy_ids: set[str] | None,
    task_ids: list[str] | None,
    num_tasks: int | None,
    repeat_count_override: int | None,
) -> dict[str, Any]:
    repeat_count = repeat_count_override or int(config["benchmark"].get("repeat_count", 4))
    policy_report = simulator_policy_report(config)
    strategies = config.get("strategies") or []
    if selected_strategy_ids:
        unknown = selected_strategy_ids - set(strategy_ids(config))
        if unknown:
            raise ValueError(f"unknown strategy ids: {sorted(unknown)}")
        strategies = [strategy for strategy in strategies if strategy["id"] in selected_strategy_ids]
    cells = []
    plan_domains = domains(config)
    if selected_domains:
        unknown_domains = selected_domains - set(plan_domains)
        if unknown_domains:
            raise ValueError(f"unknown domains: {sorted(unknown_domains)}")
        plan_domains = [domain for domain in plan_domains if domain in selected_domains]
    for domain in plan_domains:
        split_path = split_file(config, domain)
        for strategy in strategies:
            for repeat_index in range(repeat_count):
                run_label = f"{configured_run_id}_{domain}_{strategy['id']}_r{repeat_index + 1}"
                command = _tau2_command(
                    config,
                    domain=domain,
                    strategy=strategy,
                    run_label=run_label,
                    task_ids=task_ids,
                    num_tasks=num_tasks,
                )
                non_executable_reason = None
                if command is None:
                    non_executable_reason = (
                        "OpenViking memory strategy requires a TAU-2 agent adapter; "
                        "this benchmark scaffold only executes upstream TAU-2 no-memory cells."
                    )
                cells.append(
                    {
                        "domain": domain,
                        "strategy_id": strategy["id"],
                        "strategy_label": strategy.get("label", strategy["id"]),
                        "repeat_index": repeat_index + 1,
                        "run_label": run_label,
                        "train_required": bool(strategy.get("train_required")),
                        "memory_backend": strategy.get("memory_backend"),
                        "adapter_status": strategy.get("adapter_status", "ready"),
                        "executable": command is not None,
                        "user_simulator_policy": user_simulator_policy(config),
                        "user_simulator_policy_supported": policy_report["supported"],
                        "split_file": str(split_path),
                        "command": command,
                        "non_executable_reason": non_executable_reason,
                    }
                )
    executable_cell_count = sum(1 for cell in cells if cell["executable"])
    return {
        "schema_version": "openviking.tau2.run_plan.v0",
        "run_id": configured_run_id,
        "status": "planned",
        "strategy_ids": strategy_ids(config),
        "domains": plan_domains,
        "tau2": tau2_context(config),
        "simulator_policy": policy_report,
        "cell_count": len(cells),
        "executable_cell_count": executable_cell_count,
        "pending_cell_count": len(cells) - executable_cell_count,
        "cells": cells,
    }


def _execute_cells(plan: dict[str, Any], repo: Path, out: Path) -> list[dict[str, Any]]:
    policy_report = plan.get("simulator_policy") or {}
    if not policy_report.get("supported", False):
        raise RuntimeError(
            "configured user simulator policy is not supported by this TAU-2 checkout: "
            f"{policy_report}"
        )
    rows = []
    for cell in plan["cells"]:
        if not cell.get("executable"):
            raise RuntimeError(
                f"cell is not executable yet: {cell['run_label']} "
                f"(strategy_id={cell['strategy_id']}, adapter_status={cell.get('adapter_status')})"
            )
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


def _preflight(config: dict[str, Any], out: Path, *, strict: bool) -> int:
    errors: list[str] = []
    tau2_info = tau2_context(config)
    policy_report = simulator_policy_report(config)
    if strict and not tau2_info["tau2_repo_exists"]:
        errors.append(f"missing TAU-2 repo: {tau2_info['tau2_repo']}")
    if strict and not tau2_info["tau2_cli_resolved"]:
        errors.append(f"missing TAU-2 CLI: {tau2_info['tau2_cli']}")
    if strict and not policy_report["supported"]:
        errors.append(
            "configured confirmation-aware user simulator policy requires a TAU-2 "
            f"checkout with the prompt fix: {policy_report['prompt_files']}"
        )
    split_rows = []
    for domain in domains(config):
        path = split_file(config, domain)
        exists = path.is_file()
        split_rows.append({"domain": domain, "path": str(path), "exists": exists})
        if strict and not exists:
            errors.append(f"missing split file for {domain}: {path}")

    import_rows = []
    for module in ("openviking", "openviking_cli", "tau2"):
        ok = importlib.util.find_spec(module) is not None
        import_rows.append({"module": module, "ok": ok})
        if strict and not ok:
            errors.append(f"missing Python module: {module}")

    report = {
        "status": "failed" if errors else "ok",
        "strict": strict,
        "tau2": tau2_info,
        "simulator_policy": policy_report,
        "domains": domains(config),
        "strategies": strategy_ids(config),
        "imports": import_rows,
        "split_files": split_rows,
        "errors": errors,
    }
    write_json(out / "preflight.json", report)
    if errors:
        for error in errors:
            print(f"[preflight][ERROR] {error}", file=sys.stderr)
        return 1
    print(f"[preflight][OK] wrote {out / 'preflight.json'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or run TAU-2 benchmark cells.")
    parser.add_argument("--config", type=Path, default=Path(__file__).parents[1] / "config" / "baseline.yaml")
    parser.add_argument("--run-id", default=run_id())
    parser.add_argument("--domain", action="append", help="Run only this configured domain; may be repeated.")
    parser.add_argument("--repeat-count", type=int, help="Override benchmark.repeat_count for smoke runs.")
    parser.add_argument("--strategy-id", action="append", help="Run only this strategy id; may be repeated.")
    parser.add_argument("--task-id", action="append", help="Run only this TAU-2 task id; may be repeated.")
    parser.add_argument("--num-tasks", type=int, help="Run the first N tasks from the selected split.")
    parser.add_argument("--preflight", action="store_true", help="Write a lightweight environment/config preflight report.")
    parser.add_argument("--strict-preflight", action="store_true", help="Fail if optional runtime imports or split files are missing.")
    parser.add_argument("--plan-only", action="store_true", help="Only write run_plan.json.")
    parser.add_argument("--execute", action="store_true", help="Execute planned cells.")
    args = parser.parse_args()

    if args.plan_only and args.execute:
        raise SystemExit("--plan-only and --execute are mutually exclusive")

    config = load_config(args.config)
    out = output_dir(config, args.run_id)
    out.mkdir(parents=True, exist_ok=True)
    if args.preflight or args.strict_preflight:
        preflight_status = _preflight(config, out, strict=args.strict_preflight)
        if args.strict_preflight and preflight_status != 0:
            return preflight_status

    plan = _build_plan(
        config,
        args.run_id,
        selected_domains=set(args.domain) if args.domain else None,
        selected_strategy_ids=set(args.strategy_id) if args.strategy_id else None,
        task_ids=args.task_id,
        num_tasks=args.num_tasks,
        repeat_count_override=args.repeat_count,
    )
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
