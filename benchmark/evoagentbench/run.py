"""Official Evo task loading and verification around repository VikingBot execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

from benchmark.agent_benchmarks.agent_runtime import callback_tool, run_agent, save, workspace_tools
from benchmark.agent_benchmarks.catalog import checked_evo_splits
from benchmark.evoagentbench.corpus_search import search_corpus


def official_reward(verifier, domain_name):
    error = verifier.get("error")
    missing_code = domain_name == "code_implementation" and error == "no_code_extracted"
    if error and error != "no_artifacts_found" and not missing_code:
        raise RuntimeError(f"Official grader unavailable: {error}")
    if domain_name == "code_implementation" and verifier.get("total", 0) < 1 and not missing_code:
        raise RuntimeError(f"No official code tests executed: {verifier}")
    reward = float(verifier["reward"])
    if not math.isfinite(reward) or not 0 <= reward <= 1 or (missing_code and reward != 0):
        raise ValueError(f"Invalid reward: {reward}")
    return reward


def load_task(payload, settings):
    runtime, item = payload["runtime"], payload["task"]
    repo = Path(runtime["repo"])
    name, tid, split = item["domain"], item["task_id"], payload["case"]["input"]["split"]
    if tid not in checked_evo_splits(repo, name)[split]:
        raise ValueError("Task is outside its official Evo split")
    sys.path.insert(0, str(repo / "benchmark/src"))
    from config import get_domain, get_domain_config, load_config

    load_config(runtime["benchmark_config"])
    cfg = get_domain_config(name)
    if name == "code_implementation":
        cfg["cache_dir"] = runtime["code_cache"]
        cfg["lcb_repo"] = runtime.get("lcb_repo", str(repo.parent / "lcb-source/livecodebench"))
    elif name == "information_retrieval":
        cfg["dataset_file"] = runtime["retrieval_queries"]
        cfg["mcp_server"]["auto_start"] = False
        cfg["judge"].update(
            model=settings["model"],
            api_base=settings["api_base"],
            api_key=settings["api_key"],
            disable_thinking=False,
        )
    elif name == "knowledge_work":
        cfg.update(
            workspace_root=str(Path(payload["output"]) / "workspace"),
            reference_dir=runtime["reference_dir"],
            meta_prompts_dir=runtime["meta_prompts_dir"],
        )
    domain = get_domain(name)
    if name == "knowledge_work":
        rows = json.loads(Path(runtime["knowledge_dataset"]).read_text())
        for row in rows:
            if not isinstance(row["rubric_json"], str):
                row["rubric_json"] = json.dumps(row["rubric_json"])
        domain._dataset = rows
    tasks = domain.load_tasks(
        argparse.Namespace(split=split, task=tid, trials=1, parallel=1, live=False)
    )
    if len(tasks) != 1 or str(tasks[0].get("task_id", tasks[0]["name"])) != tid:
        raise ValueError("Official Evo loader did not return exactly the requested task")
    return domain, tasks[0]


def retrieval_tool(corpus: Path, failures: list):
    async def search(query: str, k: int = 5):
        try:
            return await asyncio.to_thread(search_corpus, corpus, query, k)
        except Exception:
            failures.append("Corpus search failed")
            raise

    return callback_tool(
        "search",
        "Search the full local knowledge corpus; return ranked cited snippets. Use specific names, dates and constraints and rephrase queries as needed.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
        search,
    )


async def run_task(payload, settings):
    domain, task = load_task(payload, settings)
    output, name = Path(payload["output"]), payload["task"]["domain"]
    # Only the official domain environment/grader is used, not an upstream agent plugin.
    env = await asyncio.to_thread(domain.setup, task, "vikingbot", 1)
    try:
        workspace = Path(env.get("workspace_dir", output / "workspace"))
        workspace.mkdir(parents=True, exist_ok=True)
        prompt = domain.build_prompt(task, env)
        save(output / "task.json", task)
        save(output / "environment.json", {**env, "runtime": "vikingbot.AgentLoop"})
        failures = []
        if name == "information_retrieval":
            corpus = Path(
                payload["runtime"].get(
                    "retrieval_corpus", str(Path(payload["runtime"]["cache"]) / "corpus.sqlite")
                )
            )
            # Validate corpus availability before calling the model.
            await asyncio.to_thread(search_corpus, corpus, "United States", 1)
            tools = [retrieval_tool(corpus, failures)]
        else:
            tools = workspace_tools()
        execution = await run_agent(
            settings,
            prompt,
            tools,
            workspace,
            output,
            payload["memory"],
            timeout=domain.get_agent_timeout(task, env),
        )
        if failures:
            raise RuntimeError("Corpus search failed; refusing to grade infrastructure failure")
        verifier = await asyncio.to_thread(
            domain.verify, task, env, output, {"response": execution["text"]}
        )
        save(output / "verifier.json", verifier)
        if name == "information_retrieval" and (
            verifier.get("method") != "llm_judge"
            or verifier.get("judge_result", {}).get("parse_error")
        ):
            raise RuntimeError(
                "Official IR LLM judge unavailable; fallback or malformed grading is excluded"
            )
        reward = official_reward(verifier, name)
        threshold = domain.pass_threshold()
        threshold = 1.0 if threshold is None else threshold
        return {
            "reward": reward,
            "passed": reward >= threshold,
            "verifier": verifier,
            "pass_threshold": threshold,
        }, execution
    finally:
        await asyncio.to_thread(domain.cleanup, task, env)
