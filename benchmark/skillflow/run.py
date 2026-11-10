"""Official Harbor task lifecycle with repository VikingBot execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shlex
import time
from pathlib import Path

from benchmark.agent_benchmarks.agent_runtime import callback_tool, run_agent, save
from benchmark.skillflow.container_tools import execute
from benchmark.skillflow.verifier_health import check_verifier_stdout

PREAMBLE = """Solve this workflow inside its isolated Docker container.
Use exec/read_file/write_file/list_dir: their working directory is {workdir} in
the container. Inspect inputs and produce the exact requested artifacts.
Do not inspect verifier-owned /tests, /solution, /logs/verifier or reference
solutions, or modify checker files. Official verification runs after you finish.
Only finish once the requested artifact is on disk.

Task:
"""

FILE_INVENTORY = """import pathlib,hashlib,json,os,sys
rows={}
for root in sorted(set(['/root',sys.argv[1],'/tmp/skillflow-tool-output'])):
 for folder,dirs,files in os.walk(root):
  dirs[:]=[d for d in dirs if not d.startswith('.') and d not in ('__pycache__','node_modules')]
  for name in files:
   if name.startswith('.'):continue
   p=pathlib.Path(folder)/name
   if p.is_file() and not p.is_symlink():rows[str(p)]=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
print(json.dumps(rows))
"""


def container_tools(container: str, workdir: str, failures: list):
    async def command_run(argv, *, stdin=None, timeout=120):
        result = await asyncio.to_thread(
            execute, container, workdir, argv, stdin=stdin, timeout=timeout
        )
        if result.get("transport_error"):
            failures.append(result)
            raise RuntimeError("Task-container tool transport failed")
        return result

    async def shell(command: str):
        return await command_run(["bash", "-lc", command])

    async def read(path: str, offset: int = 1, limit: int = 100):
        return await command_run(
            [
                "python3",
                "-c",
                "import pathlib,sys;lines=pathlib.Path(sys.argv[1]).read_text(errors='replace').splitlines(keepends=True);i=max(0,int(sys.argv[2])-1);print(''.join(lines[i:i+min(500,max(1,int(sys.argv[3])))]),end='')",
                path,
                str(offset),
                str(limit),
            ]
        )

    async def write(path: str, content: str):
        return await command_run(
            [
                "python3",
                "-c",
                "import pathlib,json,sys;p,s=json.load(sys.stdin);p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s);print('Wrote',p)",
            ],
            stdin=json.dumps([path, content]),
        )

    async def ls(path: str = "."):
        return await command_run(["ls", "-la", "--", path])

    def schema(properties, required=()):
        return {"type": "object", "properties": properties, "required": list(required)}

    return [
        callback_tool(
            "exec",
            "Execute a shell command inside the task container. Large output is saved inside the same container; inspect the returned paths with exec/read_file.",
            schema({"command": {"type": "string"}}, ["command"]),
            shell,
        ),
        callback_tool(
            "read_file",
            "Read UTF-8 text in the container; offset and limit count lines. Use exec with Python for binary formats.",
            schema(
                {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1},
                },
                ["path"],
            ),
            read,
        ),
        callback_tool(
            "write_file",
            "Write UTF-8 text inside the task container.",
            schema(
                {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]
            ),
            write,
        ),
        callback_tool(
            "list_dir",
            "List files inside the task container.",
            schema({"path": {"type": "string"}}),
            ls,
        ),
    ]


async def check_image(container: str, item: dict, output: Path):
    if not item.get("parent_image"):
        return

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

    actual = await inspect("image", (await inspect("container", container))["Image"])
    parent = await inspect("image", item["parent_image"])
    a, b = dict(actual["Config"]), dict(parent["Config"])
    a.pop("Labels", None)
    b.pop("Labels", None)
    if parent["Id"] != item["parent_image_id"] or actual["RootFS"] != parent["RootFS"] or a != b:
        raise RuntimeError("Actual SkillFlow image differs from its validated parent")
    save(
        output / "image-audit.json",
        {"image_id": actual["Id"], "parent_id": parent["Id"], "filesystem_and_config_match": True},
    )


async def inventory(environment):
    result = await environment.exec(
        "python3 -c "
        + shlex.quote(FILE_INVENTORY)
        + " "
        + shlex.quote(environment._skillflow_workdir),
        timeout_sec=120,
    )
    if result.return_code:
        raise RuntimeError(f"Task-container inventory failed: {result.stderr}")
    return json.loads(result.stdout)


async def collect_artifacts(environment, paths, before):
    after = await inventory(environment)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest, sources = {}, {}
    for source, sha in after.items():
        if before.get(source) == sha:
            continue
        relative = Path(source).relative_to("/")
        destination = paths.artifacts_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        await environment.download_file(source, destination)
        manifest[str(relative)] = sha
        sources[str(relative)] = source
    save(paths.artifacts_dir / "manifest.json", manifest)
    save(paths.artifacts_dir / "container-paths.json", sources)


async def run_task(payload: dict, settings: dict):
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier

    output, item = Path(payload["output"]), payload["task"]
    task = Task(Path(item["path"]))
    trial = output / "native"
    paths = TrialPaths(trial)
    paths.mkdir()
    workspace = output / "workspace"
    workspace.mkdir()
    digest = hashlib.sha256(str(item["path"]).encode()).hexdigest()[:16]
    environment = DockerEnvironment(
        environment_dir=task.paths.environment_dir,
        environment_name=f"sf-{digest}",
        session_id=f"sf-vikingbot-{digest}-{os.getpid()}",
        trial_paths=paths,
        task_env_config=task.config.environment,
        logger=logging.getLogger("skillflow"),
    )
    before = None
    collected = False
    started = time.monotonic()
    try:
        await asyncio.wait_for(
            environment.start(force_build=True),
            timeout=task.config.environment.build_timeout_sec + 60,
        )
        result = await environment._run_docker_compose_command(["ps", "-q", "main"])
        container = (result.stdout or "").strip()
        if not container or len(container.splitlines()) != 1:
            raise RuntimeError("Harbor did not identify exactly one task container")
        location = await environment.exec("pwd", timeout_sec=30)
        workdir = location.stdout.strip()
        if not Path(workdir).is_absolute() or "\n" in workdir:
            raise RuntimeError("Invalid task-container working directory")
        environment._skillflow_workdir = workdir
        save(
            output / "environment.json",
            {"container_id": container, "workdir": workdir, "runtime": "vikingbot.AgentLoop"},
        )
        await check_image(container, item, output)
        before = await inventory(environment)
        failures = []
        execution = await run_agent(
            settings,
            PREAMBLE.format(workdir=workdir) + task.instruction,
            container_tools(container, workdir, failures),
            workspace,
            output,
            payload["memory"],
            timeout=task.config.agent.timeout_sec,
        )
        if failures:
            raise RuntimeError(
                "Task-container transport failed; refusing to grade infrastructure failure"
            )
        verified = await asyncio.wait_for(
            Verifier(task, paths, environment).verify(), timeout=task.config.verifier.timeout_sec
        )
        health = check_verifier_stdout(paths.test_stdout_path)
        save(output / "verifier-health.json", health)
        if not health["healthy"]:
            raise RuntimeError(
                f"Official SkillFlow verifier failed internally: {health['problems']}"
            )
        rewards = verified.rewards
        if not rewards or len(rewards) != 1:
            raise RuntimeError(f"Expected exactly one official reward: {rewards}")
        reward = float(next(iter(rewards.values())))
        if not math.isfinite(reward) or not 0 <= reward <= 1:
            raise RuntimeError(f"Invalid official reward: {reward}")
        verifier = {
            "reward": reward,
            "rewards": rewards,
            "source": "official_skillflow_harbor_verifier",
            "test_stdout_path": str(paths.test_stdout_path),
        }
        save(output / "verifier.json", verifier)
        # Exclude the verifier's transient files from collected task artifacts.
        await environment.exec("rm -rf /tests")
        await collect_artifacts(environment, paths, before)
        collected = True
        return {
            "reward": reward,
            "passed": reward >= 1,
            "verifier": verifier,
            "elapsed_sec": time.monotonic() - started,
        }, execution
    finally:
        try:
            if before is not None and not collected:
                await collect_artifacts(environment, paths, before)
        finally:
            await environment.stop(delete=False)
