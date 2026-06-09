#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tau2_common import (
    assert_tau2_results_complete,
    domains,
    load_config,
    normalize_litellm_env,
    output_dir,
    resolve_path,
    run_id,
    simulator_policy_report,
    split_file,
    strategy_ids,
    tau2_context,
    tau2_repo,
    user_simulator_policy,
    write_json,
)

TRAIN_TRANSCRIPT_OPENVIKING_TEXT = "openviking_text"
DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS = 5000


def _reward(sim: dict[str, Any]) -> float:
    info = sim.get("reward_info") or {}
    value = info.get("reward", sim.get("reward", 0.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _db_match(sim: dict[str, Any]) -> bool | None:
    info = sim.get("reward_info") or {}
    db = info.get("db_check") or {}
    if isinstance(db, dict):
        if "score" in db:
            return bool(db["score"])
        if "db_match" in db:
            return bool(db["db_match"])
    return sim.get("db_match")


def _strategy_int(
    config: dict[str, Any],
    strategy: dict[str, Any],
    key: str,
    *,
    fallback_key: str | None = None,
    default: int = 4,
) -> int:
    openviking = config.get("openviking", {})
    value = strategy.get(key)
    if value is None:
        value = openviking.get(key)
    if value is None and fallback_key:
        value = strategy.get(fallback_key)
    if value is None and fallback_key:
        value = openviking.get(fallback_key)
    if value is None:
        value = default
    return int(value)


def _strategy_optional_int(
    config: dict[str, Any],
    strategy: dict[str, Any],
    key: str,
    *,
    fallback_key: str | None = None,
) -> int | None:
    openviking = config.get("openviking", {})
    value = strategy.get(key)
    if value is None:
        value = openviking.get(key)
    if value is None and fallback_key:
        value = strategy.get(fallback_key)
    if value is None and fallback_key:
        value = openviking.get(fallback_key)
    if value is None:
        return None
    return int(value)


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _require_fixed_first_user(config: dict[str, Any]) -> bool:
    return _enabled(config.get("eval", {}).get("require_fixed_first_user"))


def _retrieval_budget(config: dict[str, Any], strategy: dict[str, Any]) -> dict[str, int | None]:
    retrieval_top_k = _strategy_int(config, strategy, "retrieval_top_k", default=4)
    first_user_retrieval_top_k = _strategy_int(
        config,
        strategy,
        "first_user_retrieval_top_k",
        fallback_key="retrieval_top_k",
        default=retrieval_top_k,
    )
    first_user_inject_top_k = _strategy_int(
        config,
        strategy,
        "first_user_inject_top_k",
        fallback_key="first_user_retrieval_top_k",
        default=first_user_retrieval_top_k,
    )
    prewrite_retrieval_top_k = _strategy_int(
        config,
        strategy,
        "prewrite_retrieval_top_k",
        fallback_key="retrieval_top_k",
        default=retrieval_top_k,
    )
    prewrite_inject_top_k = _strategy_int(
        config,
        strategy,
        "prewrite_inject_top_k",
        fallback_key="prewrite_retrieval_top_k",
        default=prewrite_retrieval_top_k,
    )
    memory_inject_max_chars = _strategy_optional_int(
        config,
        strategy,
        "memory_inject_max_chars",
    )
    first_user_memory_inject_max_chars = _strategy_optional_int(
        config,
        strategy,
        "first_user_memory_inject_max_chars",
        fallback_key="memory_inject_max_chars",
    )
    prewrite_memory_inject_max_chars = _strategy_optional_int(
        config,
        strategy,
        "prewrite_memory_inject_max_chars",
        fallback_key="memory_inject_max_chars",
    )
    for key, value in {
        "memory_inject_max_chars": memory_inject_max_chars,
        "first_user_memory_inject_max_chars": first_user_memory_inject_max_chars,
        "prewrite_memory_inject_max_chars": prewrite_memory_inject_max_chars,
    }.items():
        if value is not None and value < 0:
            raise ValueError(f"{strategy['id']} has negative {key}: {value}")
    return {
        "retrieval_top_k": retrieval_top_k,
        "first_user_retrieval_top_k": first_user_retrieval_top_k,
        "first_user_inject_top_k": first_user_inject_top_k,
        "prewrite_retrieval_top_k": prewrite_retrieval_top_k,
        "prewrite_inject_top_k": prewrite_inject_top_k,
        "memory_inject_max_chars": memory_inject_max_chars,
        "first_user_memory_inject_max_chars": first_user_memory_inject_max_chars,
        "prewrite_memory_inject_max_chars": prewrite_memory_inject_max_chars,
    }


def _memory_corpus_key_for(
    *,
    domain: str,
    strategy: dict[str, Any],
    train_num_tasks: int | None,
) -> str:
    corpus_id = str(strategy.get("corpus_id") or strategy["id"])
    raw_key = strategy.get("corpus_cache_key")
    if raw_key:
        key = str(raw_key).format(
            domain=domain,
            strategy_id=strategy["id"],
            corpus_id=corpus_id,
        )
    else:
        key = f"{domain}_{corpus_id}"
    if train_num_tasks is not None:
        key = f"{key}_train{train_num_tasks}"
    return key


def _memory_corpus_dir(config: dict[str, Any], configured_run_id: str, corpus_key: str) -> Path:
    raw = config.get("paths", {}).get("corpus_cache_dir")
    if raw:
        return resolve_path(str(raw)) / corpus_key
    return output_dir(config, configured_run_id) / "memory_corpora" / corpus_key


def _search_uri(search_memory_type: str) -> str:
    return f"viking://user/memories/{search_memory_type}"


def _train_transcript_format(strategy: dict[str, Any]) -> str:
    return str(strategy.get("train_transcript_format") or TRAIN_TRANSCRIPT_OPENVIKING_TEXT)


def _train_tool_output_max_chars(strategy: dict[str, Any]) -> int:
    raw = strategy.get("train_tool_output_max_chars")
    if raw is None:
        return DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS
    return int(raw)


def _train_skip_failed_sessions(strategy: dict[str, Any]) -> bool:
    return _enabled(strategy.get("train_skip_failed_sessions"))


def _manifest_openviking_identity(corpus_dir: Path) -> dict[str, str] | None:
    manifest_path = corpus_dir / "corpus_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    openviking = manifest.get("openviking") or {}
    required = ("account", "user", "search_uri")
    if not all(openviking.get(key) for key in required):
        return None
    return {key: str(openviking[key]) for key in required}


def _metrics_from_tau2_results(results_path: Path) -> dict[str, Any]:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    assert_tau2_results_complete(data, context=str(results_path))
    sims = data.get("simulations") or []
    rewards = [_reward(sim) for sim in sims]
    db_values = [_db_match(sim) for sim in sims]
    db_known = [value for value in db_values if value is not None]
    return {
        "simulation_count": len(sims),
        "avg_reward": sum(rewards) / len(rewards) if rewards else 0.0,
        "db_match_rate": (sum(1 for value in db_known if value) / len(db_known))
        if db_known
        else None,
    }


def _tau2_command(
    config: dict[str, Any],
    *,
    domain: str,
    strategy: dict[str, Any],
    configured_run_id: str,
    run_label: str,
    task_ids: list[str] | None,
    num_tasks: int | None,
    train_num_tasks: int | None,
    seed: int,
) -> list[str] | None:
    benchmark = config["benchmark"]
    model = config["model"]

    reasoning_effort = benchmark.get("reasoning_effort")
    agent_llm_args = '{"temperature":0.0}'
    user_llm_args = '{"temperature":0.0}'
    if reasoning_effort:
        agent_llm_args = f'{{"temperature":0.0,"reasoning_effort":"{reasoning_effort}"}}'
        user_llm_args = f'{{"temperature":0.0,"reasoning_effort":"{reasoning_effort}"}}'
    fixed_first_user_file = _fixed_first_user_file(config, domain)
    scope_prompt_file = _scope_prompt_file(config, strategy, domain)

    if (
        strategy.get("memory_backend") == "openviking"
        and strategy.get("train_memory_mode") == "experience_only"
    ):
        openviking = config["openviking"]
        corpus_id = str(strategy.get("corpus_id") or strategy["id"])
        resolved_train_num_tasks = (
            train_num_tasks if train_num_tasks is not None else strategy.get("train_num_tasks")
        )
        corpus_key = _memory_corpus_key_for(
            domain=domain,
            strategy=strategy,
            train_num_tasks=resolved_train_num_tasks,
        )
        corpus_dir = _memory_corpus_dir(config, configured_run_id, corpus_key)
        reuse_identity = _manifest_openviking_identity(corpus_dir)
        if reuse_identity is not None:
            account = reuse_identity["account"]
            user = reuse_identity["user"]
            search_uri = ""
        elif openviking.get("reuse_corpus_across_runs", False):
            account = f"{openviking['account']}-{corpus_key}"
            user = f"tau2-{domain}-{corpus_id}"
            search_uri = ""
        else:
            account = f"{openviking['account']}-{configured_run_id}-{domain}-{corpus_id}"
            user = f"tau2-{domain}-{corpus_id}"
            search_uri = ""
        search_memory_type = str(strategy.get("search_memory_type", "experiences"))
        if search_memory_type not in {"experiences", "trajectories"}:
            raise ValueError(
                f"Unsupported search_memory_type for {strategy['id']}: {search_memory_type}"
            )
        if not search_uri:
            search_uri = _search_uri(search_memory_type)
        budget = _retrieval_budget(config, strategy)
        command = [
            sys.executable,
            str(Path(__file__).with_name("run_memory_v2_eval.py")),
            "--tau2-repo",
            str(tau2_repo(config)),
            "--run-dir",
            str(output_dir(config, configured_run_id) / "memory_cells" / run_label),
            "--corpus-dir",
            str(corpus_dir),
            "--run-label",
            run_label,
            "--strategy-id",
            strategy["id"],
            "--domain",
            domain,
            "--train-split-name",
            str(benchmark.get("train_split_name", "train")),
            "--eval-split-name",
            str(benchmark.get("eval_split_name", "test")),
            "--max-steps",
            str(benchmark.get("max_steps", 200)),
            "--max-concurrency",
            str(benchmark.get("task_max_concurrency", 10)),
            "--agent-llm",
            str(model["agent_llm"]),
            "--user-llm",
            str(model["user_llm"]),
            "--agent-llm-args",
            agent_llm_args,
            "--user-llm-args",
            user_llm_args,
            "--openviking-url",
            str(openviking["url"]),
            "--openviking-account",
            account,
            "--openviking-user",
            user,
            "--search-uri",
            search_uri,
            "--retrieval-top-k",
            str(budget["retrieval_top_k"]),
            "--first-user-retrieval-top-k",
            str(budget["first_user_retrieval_top_k"]),
            "--first-user-inject-top-k",
            str(budget["first_user_inject_top_k"]),
            "--prewrite-retrieval-top-k",
            str(budget["prewrite_retrieval_top_k"]),
            "--prewrite-inject-top-k",
            str(budget["prewrite_inject_top_k"]),
            "--retrieval-mode",
            str(strategy.get("retrieval_mode", "first_user")),
            "--train-transcript-format",
            _train_transcript_format(strategy),
            "--train-tool-output-max-chars",
            str(_train_tool_output_max_chars(strategy)),
            "--seed",
            str(seed),
        ]
        if budget["memory_inject_max_chars"] is not None:
            command.extend(["--memory-inject-max-chars", str(budget["memory_inject_max_chars"])])
        if budget["first_user_memory_inject_max_chars"] is not None:
            command.extend(
                [
                    "--first-user-memory-inject-max-chars",
                    str(budget["first_user_memory_inject_max_chars"]),
                ]
            )
        if budget["prewrite_memory_inject_max_chars"] is not None:
            command.extend(
                [
                    "--prewrite-memory-inject-max-chars",
                    str(budget["prewrite_memory_inject_max_chars"]),
                ]
            )
        if _enabled(strategy.get("train_include_system_prompt")):
            command.append("--train-include-system-prompt")
        if _train_skip_failed_sessions(strategy):
            command.append("--train-skip-failed-sessions")
        if fixed_first_user_file is not None:
            command.extend(["--fixed-first-user-file", str(fixed_first_user_file)])
        if scope_prompt_file is not None:
            command.extend(["--scope-prompt-file", str(scope_prompt_file)])
        if task_ids:
            for task_id in task_ids:
                command.extend(["--task-id", task_id])
        elif num_tasks is not None:
            command.extend(["--num-tasks", str(num_tasks)])
        if resolved_train_num_tasks is not None:
            command.extend(["--train-num-tasks", str(resolved_train_num_tasks)])
        return command

    if strategy.get("memory_backend") != "none":
        return None

    command = [
        sys.executable,
        str(Path(__file__).with_name("run_memory_v2_eval.py")),
        "--tau2-repo",
        str(tau2_repo(config)),
        "--run-dir",
        str(output_dir(config, configured_run_id) / "memory_cells" / run_label),
        "--run-label",
        run_label,
        "--strategy-id",
        strategy["id"],
        "--domain",
        domain,
        "--eval-split-name",
        str(benchmark.get("eval_split_name", "test")),
        "--max-steps",
        str(benchmark.get("max_steps", 200)),
        "--max-concurrency",
        str(benchmark.get("task_max_concurrency", 10)),
        "--base-agent",
        str(benchmark.get("agent", "llm_agent")),
        "--user",
        str(benchmark.get("user", "user_simulator")),
        "--agent-llm",
        str(model["agent_llm"]),
        "--user-llm",
        str(model["user_llm"]),
        "--agent-llm-args",
        agent_llm_args,
        "--user-llm-args",
        user_llm_args,
        "--seed",
        str(seed),
        "--no-memory",
    ]
    if fixed_first_user_file is not None:
        command.extend(["--fixed-first-user-file", str(fixed_first_user_file)])
    if scope_prompt_file is not None:
        command.extend(["--scope-prompt-file", str(scope_prompt_file)])

    if task_ids:
        for task_id in task_ids:
            command.extend(["--task-id", task_id])
    elif num_tasks is not None:
        command.extend(["--num-tasks", str(num_tasks)])

    return command


def _fixed_first_user_file(config: dict[str, Any], domain: str) -> Path | None:
    raw = config.get("eval", {}).get("fixed_first_user_fixture")
    if raw is None:
        raw = config.get("eval", {}).get("fixed_first_user_fixtures")
    if isinstance(raw, dict):
        raw = raw.get(domain) or raw.get("default")
    if raw is None or str(raw).strip() == "":
        return None
    return resolve_path(str(raw))


def _scope_prompt_file(
    config: dict[str, Any], strategy: dict[str, Any], domain: str
) -> Path | None:
    raw = strategy.get("scope_prompt_file")
    if raw is None:
        raw = strategy.get("scope_prompt_files")
    if raw is None:
        raw = config.get("openviking", {}).get("scope_prompt_file")
    if raw is None:
        raw = config.get("openviking", {}).get("scope_prompt_files")
    if isinstance(raw, dict):
        raw = raw.get(domain) or raw.get("default")
    if raw is None or str(raw).strip() == "":
        return None
    return resolve_path(str(raw))


def _build_plan(
    config: dict[str, Any],
    configured_run_id: str,
    *,
    selected_domains: set[str] | None,
    selected_strategy_ids: set[str] | None,
    task_ids: list[str] | None,
    num_tasks: int | None,
    train_num_tasks: int | None,
    repeat_count_override: int | None,
    cell_concurrency_override: int | None,
    strategy_concurrency_override: int | None,
) -> dict[str, Any]:
    repeat_count = repeat_count_override or int(config["benchmark"].get("repeat_count", 8))
    base_seed = int(config["benchmark"].get("seed", 300))
    cell_timeout_seconds = int(config["benchmark"].get("cell_timeout_seconds", 0) or 0)
    strategy_concurrency = strategy_concurrency_override
    if strategy_concurrency is None:
        strategy_concurrency = cell_concurrency_override
    if strategy_concurrency is None:
        strategy_concurrency = config["benchmark"].get("strategy_concurrency")
    if strategy_concurrency is None:
        strategy_concurrency = config["benchmark"].get("cell_concurrency", 1)
    strategy_concurrency = max(1, int(strategy_concurrency or 1))
    policy_report = simulator_policy_report(config)
    require_fixed_first_user = _require_fixed_first_user(config)
    strategies = config.get("strategies") or []
    if selected_strategy_ids:
        unknown = selected_strategy_ids - set(strategy_ids(config))
        if unknown:
            raise ValueError(f"unknown strategy ids: {sorted(unknown)}")
        strategies = [
            strategy for strategy in strategies if strategy["id"] in selected_strategy_ids
        ]
    cells = []
    plan_domains = domains(config)
    if selected_domains:
        unknown_domains = selected_domains - set(plan_domains)
        if unknown_domains:
            raise ValueError(f"unknown domains: {sorted(unknown_domains)}")
        plan_domains = [domain for domain in plan_domains if domain in selected_domains]
    if require_fixed_first_user:
        missing_domains = [
            domain for domain in plan_domains if _fixed_first_user_file(config, domain) is None
        ]
        if missing_domains:
            protocol = config.get("eval", {}).get("protocol", "fixed_first_user_full8")
            raise ValueError(
                f"eval protocol {protocol!r} requires fixed-first-user fixtures for "
                f"{missing_domains}; set TAU2_RETAIL_FIXED_FIRST_USER_FILE and "
                "TAU2_AIRLINE_FIXED_FIRST_USER_FILE, or use a config that explicitly "
                "sets eval.require_fixed_first_user=false"
            )
    for domain in plan_domains:
        split_path = split_file(config, domain)
        for strategy in strategies:
            for repeat_index in range(repeat_count):
                seed = base_seed + repeat_index
                run_label = f"{configured_run_id}_{domain}_{strategy['id']}_r{repeat_index + 1}"
                command = _tau2_command(
                    config,
                    domain=domain,
                    strategy=strategy,
                    configured_run_id=configured_run_id,
                    run_label=run_label,
                    task_ids=task_ids,
                    num_tasks=num_tasks,
                    train_num_tasks=train_num_tasks,
                    seed=seed,
                )
                fixed_first_user_file = _fixed_first_user_file(config, domain)
                scope_prompt_file = _scope_prompt_file(config, strategy, domain)
                non_executable_reason = None
                if command is None:
                    non_executable_reason = (
                        "This OpenViking memory strategy is planned but not wired to "
                        "the TAU-2 adapter in this PR."
                    )
                cells.append(
                    {
                        "domain": domain,
                        "strategy_id": strategy["id"],
                        "strategy_label": strategy.get("label", strategy["id"]),
                        "repeat_index": repeat_index + 1,
                        "seed": seed,
                        "run_label": run_label,
                        "train_required": bool(strategy.get("train_required")),
                        "memory_backend": strategy.get("memory_backend"),
                        "corpus_id": strategy.get("corpus_id", strategy["id"]),
                        "corpus_key": _memory_corpus_key_for(
                            domain=domain,
                            strategy=strategy,
                            train_num_tasks=(
                                train_num_tasks
                                if train_num_tasks is not None
                                else strategy.get("train_num_tasks")
                            ),
                        ),
                        "corpus_dir": str(
                            _memory_corpus_dir(
                                config,
                                configured_run_id,
                                _memory_corpus_key_for(
                                    domain=domain,
                                    strategy=strategy,
                                    train_num_tasks=(
                                        train_num_tasks
                                        if train_num_tasks is not None
                                        else strategy.get("train_num_tasks")
                                    ),
                                ),
                            )
                        ),
                        "retrieval_mode": strategy.get("retrieval_mode"),
                        "train_transcript_format": _train_transcript_format(strategy),
                        "train_include_system_prompt": _enabled(
                            strategy.get("train_include_system_prompt")
                        ),
                        "train_skip_failed_sessions": _train_skip_failed_sessions(strategy),
                        "train_tool_output_max_chars": _train_tool_output_max_chars(strategy),
                        "retrieval_budget": _retrieval_budget(config, strategy),
                        "search_memory_type": strategy.get("search_memory_type", "experiences"),
                        "adapter_status": strategy.get("adapter_status", "ready"),
                        "executable": command is not None,
                        "user_simulator_policy": user_simulator_policy(config),
                        "user_simulator_policy_supported": policy_report["supported"],
                        "fixed_first_user_file": str(fixed_first_user_file)
                        if fixed_first_user_file
                        else None,
                        "scope_prompt_file": str(scope_prompt_file) if scope_prompt_file else None,
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
        "eval_protocol": config.get("eval", {}).get("protocol"),
        "require_fixed_first_user": require_fixed_first_user,
        "simulator_policy": policy_report,
        "cell_count": len(cells),
        "executable_cell_count": executable_cell_count,
        "pending_cell_count": len(cells) - executable_cell_count,
        "corpus_prepare_concurrency": int(config["benchmark"].get("corpus_prepare_concurrency", 1)),
        "strategy_concurrency": strategy_concurrency,
        "cell_concurrency": strategy_concurrency,
        "cell_timeout_seconds": cell_timeout_seconds or None,
        "cells": cells,
    }


def _cell_artifacts(cell: dict[str, Any], repo: Path, out: Path) -> dict[str, str]:
    if cell.get("memory_backend") in {"openviking", "none"}:
        run_dir = out / "memory_cells" / cell["run_label"]
        artifacts = {
            "summary": str(run_dir / f"{cell['run_label']}.summary.json"),
            "results": str(run_dir / f"{cell['run_label']}.json"),
        }
        if cell.get("memory_backend") == "none":
            return artifacts
        corpus_dir = Path(cell["corpus_dir"])
        artifacts["retrieval_trace"] = str(run_dir / f"{cell['run_label']}.retrieval_trace.jsonl")
        artifacts["corpus_manifest"] = str(corpus_dir / "corpus_manifest.json")
        return artifacts
    return {"results": str(repo / "data" / "simulations" / f"{cell['run_label']}.json")}


def _cell_metrics(cell: dict[str, Any], artifacts: dict[str, str]) -> dict[str, Any] | None:
    summary_path = Path(artifacts.get("summary", ""))
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return summary.get("metrics")

    results_path = Path(artifacts["results"])
    if not results_path.is_file():
        return None
    return _metrics_from_tau2_results(results_path)


def _memory_corpus_key(cell: dict[str, Any]) -> str:
    return str(cell.get("corpus_key") or f"{cell['domain']}_{cell['corpus_id']}")


def _tau2_subprocess_env(repo: Path) -> dict[str, str]:
    env = os.environ.copy()
    src = repo / "src"
    pythonpath_entry = str(src if src.is_dir() else repo)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        pythonpath_entry if not existing else f"{pythonpath_entry}{os.pathsep}{existing}"
    )
    return env


def _prepare_memory_corpus(cell: dict[str, Any], repo: Path, out: Path) -> dict[str, Any]:
    key = _memory_corpus_key(cell)
    manifest_path = Path(cell["corpus_dir"]) / "corpus_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        cached_transcript_format = str(
            manifest.get("train_transcript_format") or TRAIN_TRANSCRIPT_OPENVIKING_TEXT
        )
        requested_transcript_format = str(
            cell.get("train_transcript_format") or TRAIN_TRANSCRIPT_OPENVIKING_TEXT
        )
        if cached_transcript_format != requested_transcript_format:
            raise RuntimeError(
                "cached corpus train_transcript_format mismatch for "
                f"{key}: {cached_transcript_format!r} != {requested_transcript_format!r}; "
                "use a distinct corpus_id or rebuild the corpus"
            )
        cached_include_system_prompt = bool(manifest.get("train_include_system_prompt") or False)
        requested_include_system_prompt = bool(cell.get("train_include_system_prompt") or False)
        if cached_include_system_prompt != requested_include_system_prompt:
            raise RuntimeError(
                "cached corpus train_include_system_prompt mismatch for "
                f"{key}: {cached_include_system_prompt!r} != {requested_include_system_prompt!r}; "
                "use a distinct corpus_id or rebuild the corpus"
            )
        cached_tool_output_max_chars = int(
            manifest.get("train_tool_output_max_chars") or DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS
        )
        requested_tool_output_max_chars = int(
            cell.get("train_tool_output_max_chars") or DEFAULT_TRAIN_TOOL_OUTPUT_MAX_CHARS
        )
        if cached_tool_output_max_chars != requested_tool_output_max_chars:
            raise RuntimeError(
                "cached corpus train_tool_output_max_chars mismatch for "
                f"{key}: {cached_tool_output_max_chars!r} != {requested_tool_output_max_chars!r}; "
                "use a distinct corpus_id or rebuild the corpus"
            )
        cached_skip_failed = bool(manifest.get("train_skip_failed_sessions") or False)
        requested_skip_failed = bool(cell.get("train_skip_failed_sessions") or False)
        if cached_skip_failed != requested_skip_failed:
            raise RuntimeError(
                "cached corpus train_skip_failed_sessions mismatch for "
                f"{key}: {cached_skip_failed!r} != {requested_skip_failed!r}; "
                "use a distinct corpus_id or rebuild the corpus"
            )
        row = {
            "domain": cell["domain"],
            "strategy_id": cell["strategy_id"],
            "corpus_id": str(cell.get("corpus_id") or cell["strategy_id"]),
            "corpus_key": key,
            "returncode": 0,
            "reused": True,
            "artifacts": {"corpus_manifest": str(manifest_path)},
        }
        write_json(out / "corpus_prepare_results" / f"{key}.json", row)
        print(f"[tau2] reusing corpus {key}", flush=True)
        return row
    command = list(cell["command"]) + ["--prepare-corpus-only"]
    print(f"[tau2] preparing corpus {key}", flush=True)
    completed = subprocess.run(
        command,
        cwd=repo,
        env=_tau2_subprocess_env(repo),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    row = {
        "domain": cell["domain"],
        "strategy_id": cell["strategy_id"],
        "corpus_id": str(cell.get("corpus_id") or cell["strategy_id"]),
        "corpus_key": key,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "artifacts": {"corpus_manifest": str(Path(cell["corpus_dir"]) / "corpus_manifest.json")},
    }
    write_json(out / "corpus_prepare_results" / f"{key}.json", row)
    if completed.returncode != 0:
        raise RuntimeError(f"corpus prepare failed: {key} returncode={completed.returncode}")
    return row


def _prepare_memory_corpora(plan: dict[str, Any], repo: Path, out: Path) -> list[dict[str, Any]]:
    corpus_cells: dict[str, dict[str, Any]] = {}
    for cell in plan["cells"]:
        if cell.get("memory_backend") != "openviking" or not cell.get("train_required"):
            continue
        corpus_cells.setdefault(_memory_corpus_key(cell), cell)
    if not corpus_cells:
        return []

    worker_count = max(1, int(plan.get("corpus_prepare_concurrency") or 1))
    if worker_count == 1 or len(corpus_cells) == 1:
        return [_prepare_memory_corpus(cell, repo, out) for cell in corpus_cells.values()]

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_prepare_memory_corpus, cell, repo, out): key
            for key, cell in corpus_cells.items()
        }
        for future in as_completed(futures):
            rows.append(future.result())
    return rows


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def weighted(rows_for_group: list[dict[str, Any]]) -> dict[str, Any]:
        metric_rows = [row for row in rows_for_group if row.get("metrics")]
        sim_count = sum(int(row["metrics"].get("simulation_count") or 0) for row in metric_rows)
        reward_sum = sum(
            float(row["metrics"].get("avg_reward") or 0.0)
            * int(row["metrics"].get("simulation_count") or 0)
            for row in metric_rows
        )
        db_weighted_rows = [
            row
            for row in metric_rows
            if row["metrics"].get("db_match_rate") is not None
            and int(row["metrics"].get("simulation_count") or 0) > 0
        ]
        db_weight = sum(
            int(row["metrics"].get("simulation_count") or 0) for row in db_weighted_rows
        )
        db_sum = sum(
            float(row["metrics"]["db_match_rate"])
            * int(row["metrics"].get("simulation_count") or 0)
            for row in db_weighted_rows
        )
        return {
            "cell_count": len(rows_for_group),
            "completed_cell_count": len(metric_rows),
            "simulation_count": sim_count,
            "avg_reward": reward_sum / sim_count if sim_count else None,
            "db_match_rate": db_sum / db_weight if db_weight else None,
        }

    by_strategy: dict[str, dict[str, Any]] = {}
    for row in rows:
        strategy_id = row["strategy_id"]
        strategy_summary = by_strategy.setdefault(
            strategy_id,
            {
                "strategy_id": strategy_id,
                "domains": {},
                "task_weighted_total": {},
            },
        )
        strategy_summary["domains"].setdefault(row["domain"], []).append(row)

    for strategy_summary in by_strategy.values():
        all_rows = []
        for domain, domain_rows in list(strategy_summary["domains"].items()):
            strategy_summary["domains"][domain] = weighted(domain_rows)
            all_rows.extend(domain_rows)
        strategy_summary["task_weighted_total"] = weighted(all_rows)

    return {
        "schema_version": "openviking.tau2.scoreboard.v0",
        "strategies": by_strategy,
    }


def _execute_cell(
    cell: dict[str, Any], repo: Path, out: Path, cell_timeout: int | None
) -> dict[str, Any]:
    cell_result_path = out / "cell_results" / f"{cell['run_label']}.json"
    if cell_result_path.is_file():
        existing_row = json.loads(cell_result_path.read_text(encoding="utf-8"))
        if existing_row.get("returncode") == 0 and existing_row.get("metrics"):
            print(f"[tau2] skipping completed {cell['run_label']}", flush=True)
            return existing_row

    print(f"[tau2] running {cell['run_label']}", flush=True)
    try:
        completed = subprocess.run(
            cell["command"],
            cwd=repo,
            env=_tau2_subprocess_env(repo),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=cell_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        row = {
            "run_label": cell["run_label"],
            "domain": cell["domain"],
            "strategy_id": cell["strategy_id"],
            "returncode": 124,
            "timed_out": True,
            "timeout_seconds": cell_timeout,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
            "artifacts": _cell_artifacts(cell, repo, out),
            "metrics": None,
        }
        write_json(cell_result_path, row)
        return row

    row = {
        "run_label": cell["run_label"],
        "domain": cell["domain"],
        "strategy_id": cell["strategy_id"],
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }
    row["artifacts"] = _cell_artifacts(cell, repo, out)
    row["metrics"] = _cell_metrics(cell, row["artifacts"])
    write_json(cell_result_path, row)
    return row


def _execute_cells(plan: dict[str, Any], repo: Path, out: Path) -> list[dict[str, Any]]:
    policy_report = plan.get("simulator_policy") or {}
    if not policy_report.get("supported", False):
        raise RuntimeError(
            "configured user simulator policy is not supported by this TAU-2 checkout: "
            f"{policy_report}"
        )
    _prepare_memory_corpora(plan, repo, out)
    cells = []
    for cell in plan["cells"]:
        if not cell.get("executable"):
            raise RuntimeError(
                f"cell is not executable yet: {cell['run_label']} "
                f"(strategy_id={cell['strategy_id']}, adapter_status={cell.get('adapter_status')})"
            )
        cells.append(cell)

    cell_timeout = int(plan.get("cell_timeout_seconds") or 0) or None
    worker_count = max(
        1, int(plan.get("strategy_concurrency") or plan.get("cell_concurrency") or 1)
    )
    if worker_count == 1 or len(cells) == 1:
        return [_execute_cell(cell, repo, out, cell_timeout) for cell in cells]

    print(f"[tau2] running eval cells with concurrency={worker_count}", flush=True)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_execute_cell, cell, repo, out, cell_timeout): cell for cell in cells
        }
        for future in as_completed(futures):
            rows.append(future.result())
    return rows


def _execution_failures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("returncode") != 0 or row.get("timed_out") or not row.get("metrics")
    ]


def _preflight(config: dict[str, Any], out: Path, *, strict: bool) -> int:
    errors: list[str] = []
    llm_env = normalize_litellm_env()
    tau2_info = tau2_context(config)
    policy_report = simulator_policy_report(config)
    if strict and not tau2_info["tau2_repo_exists"]:
        errors.append(f"missing TAU-2 repo: {tau2_info['tau2_repo']}")
    if strict and not tau2_info["tau2_cli_resolved"]:
        errors.append(f"missing TAU-2 CLI: {tau2_info['tau2_cli']}")
    if strict and not llm_env["has_api_key"]:
        errors.append("missing LLM API key: set OPENAI_API_KEY or ARK_API_KEY")
    if strict and not llm_env["has_base_url"]:
        errors.append(
            "missing OpenAI-compatible base URL: set OPENAI_API_BASE, OPENAI_BASE_URL, or ARK_BASE_URL"
        )
    if strict and not policy_report["supported"]:
        errors.append(
            "configured confirmation-aware user simulator policy requires a TAU-2 "
            f"checkout with the prompt fix: {policy_report['prompt_files']}"
        )
    split_rows = []
    fixture_rows = []
    for domain in domains(config):
        path = split_file(config, domain)
        exists = path.is_file()
        split_rows.append({"domain": domain, "path": str(path), "exists": exists})
        if strict and not exists:
            errors.append(f"missing split file for {domain}: {path}")
        fixed_first_user_file = _fixed_first_user_file(config, domain)
        fixture_exists = fixed_first_user_file.is_file() if fixed_first_user_file else False
        fixture_rows.append(
            {
                "domain": domain,
                "path": str(fixed_first_user_file) if fixed_first_user_file else None,
                "exists": fixture_exists,
            }
        )
        if strict and _require_fixed_first_user(config) and not fixture_exists:
            errors.append(
                "missing fixed-first-user fixture for "
                f"{domain}: set TAU2_{domain.upper()}_FIXED_FIRST_USER_FILE"
            )

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
        "eval_protocol": config.get("eval", {}).get("protocol"),
        "require_fixed_first_user": _require_fixed_first_user(config),
        "llm_env": llm_env,
        "simulator_policy": policy_report,
        "domains": domains(config),
        "strategies": strategy_ids(config),
        "imports": import_rows,
        "split_files": split_rows,
        "fixed_first_user_fixtures": fixture_rows,
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
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).parents[1] / "config" / "no_memory.yaml"
    )
    parser.add_argument("--run-id", default=run_id())
    parser.add_argument(
        "--domain", action="append", help="Run only this configured domain; may be repeated."
    )
    parser.add_argument(
        "--repeat-count", type=int, help="Override benchmark.repeat_count for smoke runs."
    )
    parser.add_argument(
        "--cell-concurrency",
        type=int,
        help="Deprecated alias for --strategy-concurrency.",
    )
    parser.add_argument(
        "--strategy-concurrency",
        type=int,
        help="Override benchmark.strategy_concurrency for parallel matrix cells.",
    )
    parser.add_argument(
        "--strategy-id", action="append", help="Run only this strategy id; may be repeated."
    )
    parser.add_argument(
        "--task-id", action="append", help="Run only this TAU-2 task id; may be repeated."
    )
    parser.add_argument(
        "--num-tasks", type=int, help="Run the first N tasks from the selected split."
    )
    parser.add_argument(
        "--train-num-tasks", type=int, help="Train OpenViking memory on the first N train tasks."
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Write a lightweight environment/config preflight report.",
    )
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Fail if optional runtime imports or split files are missing.",
    )
    parser.add_argument("--plan-only", action="store_true", help="Only write run_plan.json.")
    parser.add_argument("--execute", action="store_true", help="Execute planned cells.")
    args = parser.parse_args()
    normalize_litellm_env()

    if args.plan_only and args.execute:
        raise SystemExit("--plan-only and --execute are mutually exclusive")
    if args.cell_concurrency is not None and args.cell_concurrency < 1:
        raise SystemExit("--cell-concurrency must be >= 1")
    if args.strategy_concurrency is not None and args.strategy_concurrency < 1:
        raise SystemExit("--strategy-concurrency must be >= 1")

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
        train_num_tasks=args.train_num_tasks,
        repeat_count_override=args.repeat_count,
        cell_concurrency_override=args.cell_concurrency,
        strategy_concurrency_override=args.strategy_concurrency,
    )
    write_json(out / "run_plan.json", plan)
    write_json(out / "resolved_config.json", config)
    print(f"[tau2] wrote {out / 'run_plan.json'}")

    if args.execute:
        try:
            rows = _execute_cells(plan, tau2_repo(config), out)
            failures = _execution_failures(rows)
            plan["status"] = "failed" if failures else "succeeded"
            plan["executed_cell_count"] = len(rows)
            plan["failed_cell_count"] = len(failures)
            write_json(out / "run_plan.json", plan)
            write_json(out / "scoreboard.json", _summarize(rows))
            if failures:
                labels = ", ".join(str(row.get("run_label")) for row in failures[:5])
                raise RuntimeError(f"{len(failures)} cell(s) failed or incomplete: {labels}")
        except Exception as exc:
            plan["status"] = "failed"
            plan["error"] = str(exc)
            write_json(out / "run_plan.json", plan)
            print(f"[tau2][ERROR] {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
