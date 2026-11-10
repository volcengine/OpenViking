"""One isolated repository VikingBot agent per rollout; OV owns experience learning."""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import sys
from pathlib import Path

from benchmark.agent_benchmarks.agent_runtime import save, trajectory_messages


def configure(payload):
    from dotenv import load_dotenv

    runtime, options = payload["runtime"], payload["options"]
    if runtime.get("env_file"):
        load_dotenv(runtime["env_file"], override=False)
    key_name = runtime.get("api_key_env", "ARK_API_KEY")
    if not os.environ.get(key_name):
        raise ValueError(f"Required model credential environment variable is missing: {key_name}")
    settings = {
        k: options.get(k, runtime.get(k, default))
        for k, default in {
            "model": "ep-20261027174527-vfzzz",
            "api_base": "https://ark.cn-beijing.volces.com/api/v3",
            "reasoning_effort": "low",
            "temperature": 0.7,
            "max_tokens": 16384,
            "max_iterations": 30,
        }.items()
    }
    if settings["max_tokens"] <= 0 or settings["max_iterations"] <= 0:
        raise ValueError("Token and iteration budgets must be positive")
    if settings["reasoning_effort"] not in {"auto", "low", "medium", "high"}:
        raise ValueError("Unsupported reasoning_effort")
    if (
        not math.isfinite(float(settings["temperature"]))
        or not 0 <= float(settings["temperature"]) <= 2
    ):
        raise ValueError("temperature must be finite and between 0 and 2")
    output = Path(payload["output"])
    save(output / "runtime-settings.json", {"runtime": "vikingbot.AgentLoop", **settings})
    settings.update(
        provider=runtime.get("provider", "volcengine"),
        api_key=os.environ[key_name],
        thinking=True,
        timeout=float(runtime.get("model_timeout_seconds", 300)),
        max_retries=3,
    )
    if settings["reasoning_effort"] == "auto":
        settings.pop("reasoning_effort")
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    os.environ.update(
        EVALUATION_API_KEY=settings["api_key"],
        EVALUATION_API_BASE=settings["api_base"],
        EVALUATION_MODEL=settings["model"],
    )
    from benchmark.agent_benchmarks.quota import install_quota_backoff

    install_quota_backoff(output)
    # Native Evo LLM judges use the upstream synchronous OpenAI client.
    from openai.resources.chat.completions import Completions

    original = Completions.create

    def create(self, *args, **kwargs):
        kwargs.update(model=settings["model"], temperature=settings["temperature"])
        if settings.get("reasoning_effort"):
            kwargs["extra_body"] = {
                **(kwargs.get("extra_body") or {}),
                "thinking": {"type": "enabled"},
                "reasoning_effort": settings["reasoning_effort"],
            }
        return original(self, *args, **kwargs)

    Completions.create = create
    return settings


async def run(payload):
    output = Path(payload["output"])
    settings = configure(payload)
    if payload["dataset"] == "skillflow":
        from benchmark.skillflow.run import run_task
    elif payload["dataset"] == "evoagentbench":
        from benchmark.evoagentbench.run import run_task
    else:
        raise ValueError("Unknown benchmark dataset")
    result, execution = await run_task(payload, settings)
    save(output / "native-result.json", result)
    from benchmark.agent_benchmarks.health import execution_health

    health = execution_health(execution)
    if any(health.values()):
        raise RuntimeError(f"Tool contract or output visibility failure: {health}")
    audit = json.loads((output / "ov-recall.json").read_text())
    rollout = {
        "case": payload["case"],
        "policy_snapshot_id": payload["snapshot_id"],
        "messages": trajectory_messages((output / "ov-prompt.md").read_text(), execution),
        "evaluation": {
            "passed": bool(result["passed"]),
            "score": float(result["reward"]),
            "criterion_results": [],
            "feedback": [],
            "metadata": {"official_verifier": result["verifier"]},
        },
        "metadata": {
            "dataset": payload["dataset"],
            "native_output_dir": str(output),
            "native_session_id": execution["session_id"],
            "agent_runtime": "vikingbot.AgentLoop",
            "ov_recall": audit,
            "memory": "\n\n".join(x["content"] for x in audit["matches"]),
            "external_memory_searches": int(audit["enabled"]),
            "learning_owner": "openviking",
            "native_learning": False,
            "tool_health": health,
        },
    }
    # Record actual loaded modules so real smokes can verify runtime isolation.
    save(
        output / "runtime-audit.json",
        {
            "agent_runtime": "vikingbot.AgentLoop",
            "agent_source": str(
                Path(__file__).resolve().parents[2] / "bot/vikingbot/agent/loop.py"
            ),
            "external_agent_modules": [x for x in sys.modules if x.split(".")[0] == "openspace"],
        },
    )
    save(output / "rollout.json", rollout)


async def main():
    payload = json.loads(sys.stdin.read())
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    await run(payload)


if __name__ == "__main__":
    asyncio.run(main())
