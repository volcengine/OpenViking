"""One process per rollout; native environments/grading, OV-only learning."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace


class QuietProgress:
    def task(self, *args, **kwargs):
        pass

    def activity(self, *args, **kwargs):
        pass


async def recall(prompt: str, memory: dict, output: Path) -> tuple[str, dict]:
    """Read trained Experience files once at task entry; baseline never calls OV."""
    if not memory:
        audit = {"enabled": False, "boundary": "task_entry", "matches": []}
        (output / "ov-recall.json").write_text(json.dumps(audit))
        return prompt, audit
    import httpx

    url, root = memory.get("openviking_url"), memory.get("root_uri")
    if not url or not root:
        raise ValueError("Memory-enabled rollout requires OV URL and policy root")
    headers = {}
    for key, header in [
        ("openviking_api_key", "X-API-Key"),
        ("openviking_account", "X-OpenViking-Account"),
        ("openviking_user", "X-OpenViking-User"),
    ]:
        if memory.get(key):
            headers[header] = str(memory[key])
    async with httpx.AsyncClient(base_url=url.rstrip("/"), headers=headers, timeout=120) as client:
        response = await client.post(
            "/api/v1/search/search",
            json={"query": prompt[:16000], "target_uri": root, "limit": 3, "read_content": True},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "ok" or not isinstance(data.get("result"), dict):
            raise RuntimeError("OV retrieval did not return a successful search result")
        result = data["result"]
        if result.get("errors"):
            raise RuntimeError("OV retrieval reported errors")
        matches = result.get("memories", result.get("entries"))
        if not isinstance(matches, list):
            raise RuntimeError("OV retrieval response has no memory list")
        blocks, evidence = [], []
        for row in matches[:3]:
            uri = row.get("uri", "")
            if not uri.startswith(root.rstrip("/") + "/"):
                raise RuntimeError("OV returned memory outside the experiment policy root")
            content = row.get("content")
            if not isinstance(content, str) or not content.strip():
                read = await client.get("/api/v1/content/read", params={"uri": uri})
                read.raise_for_status()
                read_data = read.json()
                if read_data.get("status") != "ok" or not isinstance(read_data.get("result"), str):
                    raise RuntimeError("Retrieved OV memory could not be read")
                content = read_data["result"]
            blocks.append(f"Experience ({uri}):\n{content[:4000]}")
            evidence.append({"uri": uri, "content": content[:4000]})
    audit = {"enabled": True, "boundary": "task_entry", "matches": evidence}
    (output / "ov-recall.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    if blocks:
        prompt += (
            "\n\nPrior training experiences (guidance; check against this task's authoritative rules):\n"
            + "\n\n".join(blocks)
        )
    return prompt, audit


class MemoryAwareApp:
    """Adapt immutable/slotted OpenSpace instances without mutating their methods."""

    def __init__(self, app, memory: dict, output: Path):
        self._app, self._memory, self._output = app, memory, output

    def __getattr__(self, name):
        return getattr(self._app, name)

    async def execute(self, request):
        prompt, _ = await recall(request.prompt, self._memory, self._output)
        (self._output / "ov-prompt.md").write_text(prompt)
        return await self._app.execute(dataclasses.replace(request, prompt=prompt))


def configure(payload):
    from dotenv import load_dotenv

    runtime, options = payload["runtime"], payload["options"]
    if runtime.get("env_file"):
        load_dotenv(runtime["env_file"], override=False)
    key_name = runtime.get("api_key_env", "ARK_API_KEY")
    if not os.environ.get(key_name):
        raise ValueError(f"Required model credential environment variable is missing: {key_name}")
    os.environ["ARK_API_KEY"] = os.environ[key_name]
    output = Path(payload["output"])
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
    if (
        not math.isfinite(float(settings["temperature"]))
        or not 0 <= float(settings["temperature"]) <= 2
    ):
        raise ValueError("temperature must be finite and between 0 and 2")
    os.environ.update(
        OPENSPACE_DISABLE_AUTO_MEMORY="1",
        OPENSPACE_DISABLE_SESSION_MEMORY="1",
        EVALUATION_API_KEY=os.environ["ARK_API_KEY"],
        EVALUATION_API_BASE=settings["api_base"],
        EVALUATION_MODEL=settings["model"],
    )
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    from benchmark.agent_benchmarks.quota import install_quota_backoff
    from benchmark.evoagentbench.openspace_run import configure_registry

    install_quota_backoff(output)
    configure_registry()
    from openai.resources.chat.completions import Completions

    original = Completions.create

    def create(self, *args, **kwargs):
        kwargs.update(model=settings["model"], temperature=settings["temperature"])
        if settings["reasoning_effort"] != "auto":
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled"},
                "reasoning_effort": settings["reasoning_effort"],
            }
        return original(self, *args, **kwargs)

    Completions.create = create
    args = SimpleNamespace(
        repo=Path(runtime.get("repo", ".")),
        openspace_repo=Path(runtime["openspace_repo"]),
        benchmark_config=Path(runtime.get("benchmark_config", "unused")),
        output=output,
        model=settings["model"],
        base_url=settings["api_base"],
        reasoning_effort=settings["reasoning_effort"],
        temperature=settings["temperature"],
        max_tokens=settings["max_tokens"],
        max_iterations=settings["max_iterations"],
        resume=False,
        replay_policy="optional",
        replay_command=None,
        progress=QuietProgress(),
        cache=Path(runtime.get("cache", ".")),
    )
    (output / "runtime-settings.json").write_text(json.dumps(settings, indent=2))
    return args


def load_evo(args, runtime, item, split):
    from benchmark.agent_benchmarks.catalog import checked_evo_splits
    from benchmark.evoagentbench.openspace_run import load_domain, task_args

    domain_name, tid = item["domain"], item["task_id"]
    if tid not in checked_evo_splits(args.repo, domain_name)[split]:
        raise ValueError("Task is outside its official Evo split")
    load_domain(args)
    from config import get_domain, get_domain_config

    cfg = get_domain_config(domain_name)
    if domain_name == "code_implementation":
        cfg["cache_dir"] = runtime["code_cache"]
        if runtime.get("lcb_repo"):
            cfg["lcb_repo"] = runtime["lcb_repo"]
    elif domain_name == "information_retrieval":
        cfg["dataset_file"] = runtime["retrieval_queries"]
        cfg["mcp_server"]["auto_start"] = False
        cfg["judge"].update(
            model=args.model,
            api_base=args.base_url,
            api_key=os.environ["ARK_API_KEY"],
            disable_thinking=False,
        )
        from benchmark.evoagentbench.corpus_search_mcp import search_corpus

        corpus = args.cache / "corpus.sqlite"
        search_corpus(corpus, "United States", 1)
    elif domain_name == "knowledge_work":
        cfg.update(
            workspace_root=str(args.output / "document-workspaces"),
            reference_dir=runtime["reference_dir"],
            meta_prompts_dir=runtime["meta_prompts_dir"],
        )
    domain = get_domain(domain_name)
    if domain_name == "knowledge_work":
        rows = json.loads(Path(runtime["knowledge_dataset"]).read_text())
        for row in rows:
            if not isinstance(row["rubric_json"], str):
                row["rubric_json"] = json.dumps(row["rubric_json"])
        domain._dataset = rows
    tasks = domain.load_tasks(task_args(split, [tid]))
    if len(tasks) != 1 or str(tasks[0].get("task_id", tasks[0]["name"])) != tid:
        raise ValueError("Official Evo loader did not return exactly the requested task")
    return domain, tasks[0]


async def run(payload):
    output = Path(payload["output"])
    args = configure(payload)
    # Both backends use the same entry-point hook. Native OpenSpace learning stays off.
    import benchmark.evoagentbench.openspace_run as shared

    original_make_app = shared.make_app

    async def make_app(*a, **kw):
        kw["train"] = False
        item = payload["task"]
        if payload["dataset"] == "skillflow" and item.get("parent_image"):
            environment = json.loads((Path(a[1]) / "environment.json").read_text())

            async def inspect(kind, identifier):
                process = await asyncio.create_subprocess_exec(
                    "docker",
                    kind,
                    "inspect",
                    identifier,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                data, _ = await process.communicate()
                if process.returncode:
                    raise RuntimeError("Unable to inspect the actual SkillFlow image")
                return json.loads(data)[0]

            container = await inspect("container", environment["container_id"])
            actual = await inspect("image", container["Image"])
            parent = await inspect("image", item["parent_image"])
            x, y = dict(actual["Config"]), dict(parent["Config"])
            x.pop("Labels", None)
            y.pop("Labels", None)
            if (
                parent["Id"] != item["parent_image_id"]
                or actual["RootFS"] != parent["RootFS"]
                or x != y
            ):
                raise RuntimeError("Actual SkillFlow image differs from its validated parent")
            (output / "image-audit.json").write_text(
                json.dumps(
                    {
                        "image_id": actual["Id"],
                        "parent_id": parent["Id"],
                        "filesystem_and_config_match": True,
                    },
                    indent=2,
                )
            )
        app = await original_make_app(*a, **kw)
        return MemoryAwareApp(app, payload["memory"], output)

    shared.make_app = make_app
    skills = output / "empty-skills"
    skills.mkdir()
    item, dataset = payload["task"], payload["dataset"]
    if dataset == "skillflow":
        if item.get("parent_image"):
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                item["parent_image"],
                stdout=asyncio.subprocess.PIPE,
            )
            data, _ = await proc.communicate()
            if proc.returncode or data.decode().strip() != item["parent_image_id"]:
                raise RuntimeError("Validated SkillFlow parent image ID changed")
        from benchmark.skillflow.openspace_run import task_run

        result = await task_run(
            args, Path(item["path"]), item["domain"], "baseline", skills, output / "skills.db"
        )
    else:
        from benchmark.evoagentbench.openspace_multidomain_run import run_task

        domain, task = load_evo(args, payload["runtime"], item, payload["case"]["input"]["split"])
        result = await run_task(
            args, item["domain"], domain, task, "test", skills, output / "skills.db"
        )
        if item["domain"] == "information_retrieval" and not result.get("infrastructure_error"):
            if result.get("verifier", {}).get("method") != "llm_judge":
                raise RuntimeError(
                    "Official IR LLM judge unavailable; exact-match fallback is excluded"
                )
    (output / "native-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if (
        result.get("infrastructure_error")
        or result.get("reward") is None
        or result.get("artifact_collection_error")
    ):
        raise RuntimeError("Native benchmark infrastructure failed; inspect native-result.json")
    if (
        result.get("agent_error")
        and result.get("agent_status") != "max_output_tokens"
        and not result.get("task_timeout")
    ):
        raise RuntimeError("Agent runtime failed; inspect native-result.json")
    execution_path = next(output.glob("**/execution.json"), None)
    if execution_path is None:
        raise RuntimeError(
            "No complete execution trajectory; refusing to train on a final answer alone"
        )
    execution = json.loads(execution_path.read_text())
    from benchmark.agent_benchmarks.health import execution_health

    health = execution_health(execution)
    if any(health.values()):
        raise RuntimeError(f"Tool contract or output visibility failure: {health}")
    if any(skills.iterdir()) or result.get("committed_actions"):
        raise RuntimeError("Native runtime unexpectedly learned/published skills")
    prompt_path = output / "ov-prompt.md"
    prompt = (
        prompt_path.read_text()
        if prompt_path.exists()
        else json.loads((execution_path.parent / "prompt.json").read_text())["prompt"]
    )
    messages = [{"id": "task", "role": "user", "parts": [{"type": "text", "text": prompt}]}]
    for index, event in enumerate(execution["tool_executions"]):
        messages.append(
            {
                "id": f"tool-{index}",
                "role": "assistant",
                "parts": [
                    {
                        "type": "text",
                        "text": "Native tool execution:\n" + json.dumps(event, ensure_ascii=False),
                    }
                ],
            }
        )
    messages.append(
        {
            "id": "answer",
            "role": "assistant",
            "parts": [{"type": "text", "text": execution.get("text", "")}],
        }
    )
    reward = float(result["reward"])
    if not math.isfinite(reward):
        raise ValueError("Nonfinite native reward")
    verifier_path = execution_path.parent / "verifier.json"
    verifier = (
        json.loads(verifier_path.read_text())
        if verifier_path.exists()
        else result.get("verifier", {})
    )
    recall_audit = json.loads((output / "ov-recall.json").read_text())
    rollout = {
        "case": payload["case"],
        "messages": messages,
        "policy_snapshot_id": payload["snapshot_id"],
        "evaluation": {
            "passed": bool(result["passed"]),
            "score": reward,
            "criterion_results": [],
            "feedback": [],
            "metadata": {"official_verifier": verifier},
        },
        "metadata": {
            "dataset": dataset,
            "native_output_dir": str(output),
            "native_session_id": execution["session_id"],
            "ov_recall": recall_audit,
            "memory": "\n\n".join(row["content"] for row in recall_audit["matches"]),
            "external_memory_searches": 1 if recall_audit["enabled"] else 0,
            "learning_owner": "openviking",
            "native_learning": False,
            "tool_health": health,
        },
    }
    (output / "rollout.json").write_text(json.dumps(rollout, ensure_ascii=False, indent=2))


async def main():
    payload = json.loads(sys.stdin.read())
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    await run(payload)


if __name__ == "__main__":
    asyncio.run(main())
